# -*- coding: utf-8 -*-
"""查詢工具：kb_search（一次查全部來源）與 kb_get（取單筆全文）

取代本機 plugin 的 9 個查詢工具（recall、jira_lookup、faq_lookup、bulletin_lookup、version_bulletins、
task_lookup、sop_lookup、wiki_lookup、custom_lookup）。評分與資料讀取沿用 servers/MEMORY 的程式，
這裡只負責「一次問全部來源」與「回傳結構化的知識編號」；本機 plugin 的工具不受影響。

與本機 recall 的差別：這裡不回寫 HIT_COUNT。引用次數改由使用紀錄（HRMS_KB_USAGE_LOG）計算。

MEMORY 的資料層共用一條資料庫連線，所以所有查詢都在同一把鎖底下、放到執行緒裡跑。
"""
import re
import sys
import threading
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "MEMORY"))

import custom_core as cc  # noqa: E402
import jira_core as jc  # noqa: E402
import jira_db  # noqa: E402
import memory_core as mc  # noqa: E402
import memory_db as mdb  # noqa: E402
import refdata_core as rc  # noqa: E402
import refdata_db  # noqa: E402
import wiki_core as wc  # noqa: E402
import wiki_db  # noqa: E402

from .dispatch import ToolResult  # noqa: E402
from .tools_core import registry  # noqa: E402

_LOCK = threading.Lock()

SOURCES = ("wiki", "memory", "faq", "sop", "task", "jira", "bulletin")
LABELS = {"wiki": "Wiki（系統原則與規則）", "memory": "團隊記憶", "faq": "FAQ", "sop": "SOP（客戶需求處理步驟）",
          "task": "Task（修正資料的罐頭語法）", "jira": "Jira 結案紀錄（過往案例，僅供參考）", "bulletin": "公告"}
GET_TYPES = SOURCES + ("custom",)
BRIEF = 140
THRESHOLD = mc.RECALL_THRESHOLD


def _brief(text, limit=BRIEF):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= limit else text[:limit] + "…"


AI_WEIGHT = 0.9      # 經工具寫入、未審核的資料，排序時分數打折，排在相近的人工資料之後


def _is_ai(author):
    return (author or "").endswith("(AI)")


def _ai_tag(author):
    """經工具寫入的資料：顯示填寫人並標示未審核，有問題可以直接去問填寫人。"""
    return f" 👤填寫人 {author[:-4]}・未審核" if _is_ai(author) else ""


PURPOSE_LABELS = {"reply": "可回覆客戶", "procedure": "處理步驟", "data_fix": "資料修正語法", "general": "一般知識"}
def note_reminder(stats):
    """查詢結果結尾的工作筆記提醒（決策 17、23）：帶這個連線的實際數字，比固定一句話不容易被略過。"""
    lookups, notes = stats["lookups"], stats["notes"]
    if notes:
        return (f"📝 本次連線已查詢 {lookups} 次、已寫 {notes} 筆工作筆記。之後每讀一段程式碼、資料表結構或 Jira 單，"
                f"就當場再記一筆，不要等到回覆前。")
    return (f"⚠ 本次連線已查詢 {lookups} 次，尚未寫工作筆記。這輪只要讀過程式碼、資料表結構或 Jira 單，或被使用者糾正，"
            f"現在就用 kb_note 記一筆（只填 topic、content；不需確認、對錯不論，寫錯由審核處理），"
            f"或在下次 kb_search 帶 note 順手寫。只查知識庫的不用寫。")


def _with_reminder(call, result, looked_up):
    """有查到東西或查無資料時，把這次算進查詢次數並在結尾附提醒；參數錯誤、找不到編號時不附。"""
    if looked_up:
        stats = call.services.activity(call.ctx.session_id)
        stats["lookups"] += 1
        result.text += "\n\n" + note_reminder(stats)
    return result


DATA_FIX_NOTE = "⚠️ 這筆含修正資料的語法，僅供參考：須人工確認參數與影響範圍後才可執行，AI 不可自動執行。"


def _purpose_tag(r):
    """記憶的用途標記（線上版 remember 寫入時存在 META.purpose）；一般知識與舊資料不顯示。"""
    purpose = (r.get("meta") or {}).get("purpose")
    return f"〔{PURPOSE_LABELS[purpose]}〕" if purpose in PURPOSE_LABELS and purpose != "general" else ""


# ── 各來源的排序：回傳 [(分數, 編號, 一行摘要)]，由高到低 ─────────────

def _rank_memory(query, qs, top_k, **_):
    hits = {"System": [], "Engineer": []}
    for r in mc.active_rows():
        sc = mc._score(query, qs, r)
        if sc >= THRESHOLD:
            hits[r["type"]].append((sc, r))
    out = []
    for kind in ("System", "Engineer"):      # 兩型各取 top_k，與本機 recall 一致
        for sc, r in sorted(hits[kind], key=lambda x: -x[0])[:top_k]:
            entry = f" → `{r['entry_path']}`" if kind == "Engineer" and r["entry_path"] else ""
            verified = " ✅已驗證" if r["review"] == "verified" else ""
            out.append((sc, r["id"], f"【{r['topic']}】{kind}{_purpose_tag(r)}{entry}{verified}"
                                     f"{_ai_tag(r['created_by'])}｜{_brief(r['content'])}"))
    return sorted(out, key=lambda x: -x[0])


def _rank_jira(query, qs, top_k, **_):
    scored = []
    for r in jc.active_rows():
        sc = mc._score(query, qs, {"topic": r["title"], "content": r["root_cause"] + " " + r["resolution"],
                                   "keywords": r["keywords"], "review": ""})
        if sc >= THRESHOLD:
            scored.append((sc, r))
    scored.sort(key=lambda x: (x[1]["review"] != "verified", -x[0]))    # 已審核的優先
    return [(sc, r["id"], f"{r['key']}（{r['kind']}）【{r['title']}】"
                          f"{' ✅已審核' if r['review'] == 'verified' else ''}｜根因：{_brief(r['root_cause'], 90)}")
            for sc, r in scored[:top_k]]


def _rank_refdata(key):
    conf = rc.TABLE_CONF[key]

    def rank(query, qs, top_k, **_):
        scored = []
        for r in refdata_db.load_all(key):
            content = " ".join(r.get(f, "") or "" for f in conf["content_fields"])
            sc = mc._score(query, qs, {"topic": r.get(conf["topic_field"], "") or "", "content": content,
                                       "keywords": r.get("keywords", ""), "review": ""})
            if sc >= THRESHOLD:
                scored.append((sc * AI_WEIGHT if _is_ai(r.get("author")) else sc, r, content))
        scored.sort(key=lambda x: -x[0])
        out = []
        for sc, r, content in scored[:top_k]:
            title = r.get(conf["topic_field"], "")
            cat = f"（{r['category']}）" if r.get("category") else ""
            out.append((sc, r["id"], f"{title}{cat}{_ai_tag(r.get('author'))}｜{_brief(content)}"))
        return out
    return rank


def _rank_wiki(query, qs, top_k, customer_code=None, **_):
    scored = []
    for r in wc._visible(wiki_db.load_all(), customer_code):
        parsed = wc.parse_page(r.get("content", ""))
        units = []
        for topic, content, _rule in wc._units(parsed):
            sc = wc._score(query, qs, topic, content)
            if sc >= wc.UNIT_SHOW_THRESHOLD:
                units.append((sc, topic))
        units.sort(key=lambda x: -x[0])
        page_sc = wc._score(query, qs, r.get("title", ""),
                            parsed["summary"] + " " + " ".join(u[0] for u in wc._units(parsed)),
                            r.get("keywords", ""))
        best = max([page_sc] + [u[0] for u in units if u[0] >= THRESHOLD])
        if best >= THRESHOLD:
            scored.append((best, r, parsed, units))
    scored.sort(key=lambda x: -x[0])
    out = []
    for sc, r, parsed, units in scored[:top_k]:
        hit = "；命中：" + "、".join(u[1] for u in units[:wc.MAX_RULES_SHOWN]) if units else ""
        review = f" ⚠此頁有 {int(r.get('open_review_count') or 0)} 筆疑慮複查中" if int(r.get("open_review_count") or 0) else ""
        out.append((sc, r.get("topic_code") or r["git_path"],
                    f"{wc._page_label(r)}{review}｜{_brief(parsed['summary'], 100)}{hit}"))
    return out


RANKERS = {"memory": _rank_memory, "jira": _rank_jira, "wiki": _rank_wiki,
           "faq": _rank_refdata("faq"), "sop": _rank_refdata("sop"),
           "task": _rank_refdata("task"), "bulletin": _rank_refdata("bulletin")}


def _flag_notes(store):
    """待處理的疑慮標記：{(類型, 編號字串): [理由]}。標記表還沒建或查不到時當作沒有。"""
    notes = {}
    try:
        for f in store.open_flags():
            kind, target, reason = f["target_type"], str(f["target_id"]), f["reason"]
            if kind == "wiki" and "#" in target:      # 標在某條規則上（CARD#R7）：顯示在該頁，並註明是哪一條
                target, rule = target.split("#", 1)
                reason = f"{rule}：{reason}"
            notes.setdefault((kind, target), []).append(reason)
    except Exception:
        pass
    return notes


def _search_sync(store, query, sources, top_k, customer_code, with_version):
    qs = mc._bigrams(query)
    if not qs:
        return ToolResult("⚠️ 查詢敘述過短，無法比對。", query_text=query, error="query too short")
    if not mdb.db_enabled():
        return ToolResult("⚠️ 伺服器未設定知識庫資料庫（MSSQL_*），無法查詢。", query_text=query, error="db not configured")

    flags = _flag_notes(store)
    lines, refs, empty, failed = [f"# 知識庫搜尋：{query}"], [], [], []
    for src in sources:
        try:
            hits = RANKERS[src](query, qs, top_k, customer_code=customer_code)
        except Exception as e:
            failed.append(f"{LABELS[src]}（{type(e).__name__}）")
            continue
        if not hits:
            empty.append(LABELS[src].split("（")[0])
            continue
        lines.append(f"\n## {LABELS[src]}")
        for sc, rid, summary in hits:
            lines.append(f"- [{src}#{rid}]（相似 {sc:.2f}）{summary}")
            for reason in flags.get((src, str(rid)), []):
                lines.append(f"  ⚠ 有疑慮：{_brief(reason, 100)}")
            refs.append({"type": src, "id": rid, "score": round(sc, 3)})

    if with_version:
        try:
            lines.append("\n" + rc.version_bulletins())
            filled = []
            for r in refdata_db.load_all("bulletin"):
                if (r.get("category") or "").strip() not in rc.VERSION_TYPES:
                    continue
                if _is_ai(r.get("author")):
                    filled.append(f"公告 #{r['id']}（填寫人 {r['author'][:-4]}）")
                if not any(x["type"] == "bulletin" and x["id"] == r["id"] for x in refs):
                    refs.append({"type": "bulletin", "id": r["id"], "score": None})
            if filled:
                lines.append(f"\n其中線上填寫、未審核的：{'、'.join(filled)}。內容有疑問可以直接問填寫人。")
        except Exception as e:
            failed.append(f"版更公告（{type(e).__name__}）")

    if empty:
        lines.append(f"\n（查無相符：{'、'.join(empty)}）")
    if failed:
        lines.append(f"\n⚠️ 以下來源查詢失敗，不代表沒有資料：{'、'.join(failed)}")
    if refs:
        lines.append("\n→ 以上只是摘要。要引用前請用 kb_get(type, id) 讀全文；Wiki 可加 section（規則編號如 R7、章節名、全文）。")
        if any(r["type"] == "task" for r in refs):
            lines.append("→ Task 的 SQL 只是範本，須人工確認參數與影響範圍，不可自動執行。")
    return ToolResult("\n".join(lines), refs=refs, no_result=not refs, query_text=query,
                      error=("；".join(failed) or None) if failed else None)


@registry.tool(
    "kb_search",
    "搜尋 EHRMS 知識庫，一次查全部來源：Wiki（系統原則與規則）、團隊記憶、FAQ、SOP、Task 罐頭語法、"
    "Jira 結案紀錄、公告。回傳各來源最相關的幾筆摘要與編號，要引用前用 kb_get 讀全文。"
    "處理 Jira 單時請把單號寫進 query 或 jira_key。回報的是錯誤或異常、要比對是不是已知版更問題時，"
    "加上 version_bulletins=true 會整批列出版更公告。"
    "可以用 note 順手留一筆工作筆記（這次在查什麼、已經看過什麼、目前的假設），不用另外呼叫 kb_note。",
    {"type": "object",
     "properties": {
         "query": {"type": "string", "description": "問題或現象敘述（中文），可含代碼、資料表名、錯誤訊息"},
         "sources": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)},
                     "description": "只查部分來源（可選；預設全部）"},
         "top_k": {"type": "integer", "description": "每個來源回傳筆數，預設 3，最多 10"},
         "jira_key": {"type": "string", "description": "正在處理的 Jira 單號（可選）"},
         "customer_code": {"type": "string", "description": "客戶統編；有給才會回傳該客戶的客製 Wiki 頁"},
         "version_bulletins": {"type": "boolean", "description": "true＝另外整批列出版更公告"},
         "note": {"type": "string",
                  "description": "順手寫的工作筆記（可選，4000 字內）：這次在查什麼、已經讀過哪些程式碼或單、目前的假設。"
                                 "伺服器會存成過程紀錄，不需確認、對錯不論；不要寫客戶的實際資料"},
     },
     "required": ["query"]})
async def kb_search(call, args):
    query = (args.get("query") or "").strip()
    sources = [s for s in (args.get("sources") or SOURCES) if s in SOURCES] or list(SOURCES)
    top_k = max(1, min(10, int(args.get("top_k") or 3)))
    store = call.services.store

    def run():
        with _LOCK:
            return _search_sync(store, query, sources, top_k, (args.get("customer_code") or "").strip() or None,
                                bool(args.get("version_bulletins")))
    result = await anyio.to_thread.run_sync(run)
    note = str(args.get("note") or "").strip()
    if note:
        await _piggyback_note(call, result, query, note, str(args.get("jira_key") or "").strip())
    return _with_reminder(call, result, looked_up=result.no_result is not None)


async def _piggyback_note(call, result, query, note, jira_key):
    """kb_search 帶 note：查詢當下順手存一筆過程紀錄。筆記寫不進去不影響查詢結果。"""
    from . import tools_note
    from .dispatch import find_jira_key
    ref_key = (jira_key or find_jira_key({"query": query, "note": note}) or "")[:50]
    cut = len(note) > 4000
    try:
        note_id = await tools_note.save(call, {
            "kind": "process", "topic": f"查詢：{query}"[:100] or "查詢", "content": note[:4000], "ref_key": ref_key})
    except Exception as e:
        result.text += f"\n\n⚠️ note 沒有寫成工作筆記：{type(e).__name__}: {e}"
        result.write_outcome = "failed"
        result.error = "；".join(x for x in (result.error, f"note: {type(e).__name__}: {e}") if x)
        return
    result.text += (f"\n\n📝 已把 note 存成工作筆記 note#{note_id}（過程紀錄）"
                    f"{'；超過 4000 字的部分沒有存' if cut else ''}。")
    result.write_outcome, result.write_ref = "created", f"note#{note_id}"


# ── kb_get：取單筆全文 ───────────────────────────────────────────

def _int(value):
    try:
        return int(str(value).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


def _get_memory(rid, **_):
    n = _int(rid)
    rows = mdb.load_all()
    r = next((x for x in rows if x["id"] == n), None)
    if not r:
        return None, None
    lines = [f"# 團隊記憶 #{r['id']}【{r['topic']}】（{r['type']}）{_purpose_tag(r)}"]
    if (r.get("meta") or {}).get("purpose") == "data_fix":
        lines.append(DATA_FIX_NOTE)
    newer = [x["id"] for x in rows if x["review"] != "rejected"
             and (x["supersedes"] == n or n in [_int(i) for i in x["meta"].get("supersedes_all", [])])]
    if r["review"] == "rejected":
        lines.append("⚠ 這筆已被人工判定為錯誤，不可引用。")
    elif newer:
        lines.append(f"⚠ 這筆已被新版本取代，請改讀 memory#{newer[-1]}。")
    if r["entry_path"]:
        method = f" :: `{r['entry_method']}`" if r["entry_method"] else ""
        lines.append(f"程式入口：`{r['entry_path']}`{method}")
    if r["func_path"]:
        lines.append(f"功能位置：{r['func_path']}")
    lines += ["", r["content"], "",
              f"<sub>{r['created_by']}・{r['created_at']}"
              f"{'・來源 ' + r['ref_key'] if r['ref_key'] else ''}"
              f"{'・✅已驗證' if r['review'] == 'verified' else '・未驗證'}</sub>"]
    return "\n".join(lines), r["id"]


def _get_jira(rid, **_):
    """純數字先當紀錄編號找，找不到再當單號（補上預設專案前綴）；完整單號直接用單號找。"""
    n = _int(rid)
    r = next((x for x in jira_db.load_all() if x["id"] == n), None) if n is not None else None
    if r is None:
        key = jc.normalize_key(str(rid).lstrip("#"))
        r = next((x for x in jc.active_rows() if x["key"] == key), None) if key else None
    if not r:
        return None, None
    return "\n".join([f"# Jira 結案紀錄 #{r['id']}", jc.PRECISION_NOTE] + jc._fmt(r)), r["id"]


def _get_refdata(key):
    def get(rid, **_):
        n = _int(rid)
        r = next((x for x in refdata_db.load_all(key) if x["id"] == n), None)
        if not r:
            return None, None
        lines = [f"# {rc.LABELS[key]} #{r['id']}"] + rc._fmt(key, r)
        if _is_ai(r.get("author")):
            lines.append(f"\n👤 填寫人：{r['author'][:-4]}（線上填寫、未審核；內容有疑問可以直接問填寫人）")
        if key == "task":
            lines.append(f"\n{rc.TASK_DISCLAIMER}")
        return "\n".join(lines), r["id"]
    return get


def _get_wiki(rid, section=None, customer_code=None, **_):
    text = wc.lookup(page=str(rid), section=section, customer_code=customer_code)
    if text.startswith("⚠️ 找不到頁面"):
        return None, None
    # 編號統一用主題代碼（沒有就用路徑），不管呼叫端給的是標題、路徑或大小寫不同的代碼
    page = wc._find_page(wc._visible(wiki_db.load_all(), customer_code), str(rid))
    return text, (page.get("topic_code") or page["git_path"]) if page else str(rid)


def _get_custom(rid, section=None, **_):
    return cc.lookup(str(rid), section or ""), str(rid)


GETTERS = {"memory": _get_memory, "jira": _get_jira, "wiki": _get_wiki, "custom": _get_custom,
           "faq": _get_refdata("faq"), "sop": _get_refdata("sop"),
           "task": _get_refdata("task"), "bulletin": _get_refdata("bulletin")}


def _get_sync(store, kind, rid, section, customer_code):
    if not mdb.db_enabled():
        return ToolResult("⚠️ 伺服器未設定知識庫資料庫（MSSQL_*），無法查詢。", error="db not configured")
    try:
        text, found_id = GETTERS[kind](rid, section=section, customer_code=customer_code)
    except Exception as e:
        return ToolResult(f"⚠️ 讀取失敗：{type(e).__name__}: {e}", error=f"{type(e).__name__}: {e}")
    if text is None:
        return ToolResult(f"找不到 {kind}#{rid}。請用 kb_search 回傳的編號。", no_result=True)
    reasons = _flag_notes(store).get((kind, str(found_id)), [])
    if reasons:
        text += "\n\n⚠ 這筆知識有待處理的疑慮：\n" + "\n".join(f"- {r}" for r in reasons)
    return ToolResult(text, refs=[{"type": kind, "id": found_id, "score": None}], no_result=False)


@registry.tool(
    "kb_get",
    "讀取知識庫單筆全文。type 與 id 用 kb_search 回傳的編號（例：[faq#48] → type=faq, id=48）。"
    "Wiki 的 id 是主題代碼或頁面標題，section 可給規則編號（R7）、章節名、上半／下半／全文，不給則回傳目錄。"
    "Jira 的 id 可給紀錄編號或單號。type=custom 用來查某客戶的客製：id 給統編或公司名稱，"
    "section 可給路徑關鍵字；查無客製分支不等於確定是標準版。",
    {"type": "object",
     "properties": {
         "type": {"type": "string", "enum": list(GET_TYPES)},
         "id": {"type": "string", "description": "編號；Wiki 為主題代碼或標題；custom 為統編或公司名稱"},
         "section": {"type": "string", "description": "Wiki：規則編號、章節名、上半／下半／全文；custom：路徑關鍵字（可選）"},
         "customer_code": {"type": "string", "description": "客戶統編；讀該客戶的客製 Wiki 頁時才需要"},
     },
     "required": ["type", "id"]})
async def kb_get(call, args):
    kind, rid = (args.get("type") or "").strip(), str(args.get("id") or "").strip()
    if kind not in GET_TYPES or not rid:
        return ToolResult(f"⚠️ type 只能是 {'、'.join(GET_TYPES)}，且 id 必填。", error="bad arguments")
    store = call.services.store

    def run():
        with _LOCK:
            return _get_sync(store, kind, rid, (args.get("section") or "").strip() or None,
                             (args.get("customer_code") or "").strip() or None)
    result = await anyio.to_thread.run_sync(run)
    return _with_reminder(call, result, looked_up=result.no_result is False)
