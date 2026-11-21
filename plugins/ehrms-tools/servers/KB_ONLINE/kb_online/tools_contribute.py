# -*- coding: utf-8 -*-
"""填寫工具：kb_contribute（新增或更新 FAQ／SOP／Task／公告）

讓使用者從對話窗直接把知識加進既有的知識表，寫入後立刻查得到，不設審核關卡（SPEC.md 決策 16）：
- 填寫人是伺服器確認過的使用者，寫入者記為「使用者(AI)」；查詢時會顯示填寫人，有問題可以直接去問
- 權重比人工整理的內容低：沿用既有規則，寫入者帶 (AI) 的資料排在人工資料之後，並標示未審核
- 不覆蓋既有內容：要更新某一筆時，新增一筆更新版，舊的那筆保留並自動加上疑慮標記指向新版；
  之後由人或清理 agent 決定留哪一筆。有人對內容有意見時，同樣用 kb_flag 提出
- 團隊記憶（系統行為、程式入口）仍然用 remember，不走這個工具

欄位檢查沿用本機 plugin 的 refdata_write（模組須為既有值、SOP 模板、Task 的 SQL 檢查），
寫入則直接用 refdata_db，因為記錄者要是線上確認的使用者，不是伺服器那台電腦的帳號。
"""
import anyio

from . import tools_query as tq
from .dispatch import ToolResult
from .tools_core import registry

mc, mdb, rc, refdata_db = tq.mc, tq.mdb, tq.rc, tq.refdata_db
import refdata_write as rw  # noqa: E402  （tools_query 已把 servers/MEMORY 加進路徑）

TYPES = ("faq", "sop", "task", "bulletin")
LABELS = {"faq": "FAQ", "sop": "SOP", "task": "Task", "bulletin": "公告"}
AUTHOR_MAX = {"faq": 50, "sop": 100, "task": 100, "bulletin": 100}   # 各表寫入者欄位的長度

# 各類型：必填欄位、(欄位, 長度上限)
REQUIRED = {
    "faq": ("title", "content", "module"),
    "sop": ("title", "content", "module"),
    "task": ("title", "content", "syntax", "notes", "module", "category"),
    "bulletin": ("title", "content", "category"),
}
LIMITS = {
    "faq": (("category", 50),),
    "sop": (("title", 200), ("module", 50), ("keywords", 500), ("description", 500)),
    "task": (("title", 255), ("module", 50), ("category", 50)),
    "bulletin": (("title", 200), ("category", 50)),
}
FIELD_HELP = {
    "faq": "title＝客戶會問的問題；content＝回覆內容；module＝模組；category＝子分類（可選）",
    "sop": "title＝客戶需求；content＝SOP 內文（要有「## 適用情境」，並至少一段「## 前置確認／## 處理方式／## 需要客戶補的資料」）；"
           "module＝模組；keywords、description 可選",
    "task": "title＝語法名稱；content＝適用情境與前置確認；syntax＝SQL 範本；notes＝執行前確認事項與影響範圍；module、category",
    "bulletin": "title＝公告標題；content＝公告內容；category＝公告類型（版更公告請用既有的類型，例如「版本更新」）",
}


def _reject(message):
    return ToolResult(f"⚠️ {message}", write_outcome="rejected", error=message)


def _footer(text, author, basis, updates_ref):
    parts = [f"線上填寫；填寫人：{author}"]
    if updates_ref:
        parts.append(f"更新自 {updates_ref}")
    if basis:
        parts.append(f"依據：{basis}")
    return f"{text.rstrip()}\n\n（{'；'.join(parts)}）"


def _similar(kind, a, skip_id):
    """同一張表裡最相似的一筆（達到重複門檻才回傳）。"""
    conf = rc.TABLE_CONF[kind]
    best_sc, best = 0.0, None
    for r in refdata_db.load_all(kind):
        if skip_id is not None and r["id"] == skip_id:
            continue
        row = {"topic": r.get(conf["topic_field"], "") or "",
               "content": " ".join(r.get(f, "") or "" for f in conf["content_fields"]),
               "keywords": r.get("keywords", "")}
        sc = mc._dup_sim(a["title"], a["content"], a["keywords"], row)
        if sc > best_sc:
            best_sc, best = sc, r
    return (best_sc, best) if best_sc >= mc.DUP_THRESHOLD else (0.0, None)


def _choices(kind, a):
    """模組、分類須為表內已有的值；回傳錯誤訊息或 None。"""
    if kind == "faq":
        return rw._check_choice(a["module"], refdata_db.distinct_values("HRMS_FAQ", "MODULE"), "module")
    if kind == "sop":
        modules = sorted(set(refdata_db.distinct_values("HRMS_FAQ_SOP", "module"))
                         | set(refdata_db.distinct_values("HRMS_FAQ", "MODULE")))
        return rw._check_choice(a["module"], modules, "module")
    if kind == "task":
        return (rw._check_choice(a["module"], refdata_db.distinct_values("HRMS_TASK", "module"), "module")
                or rw._check_choice(a["category"], refdata_db.distinct_values("HRMS_TASK", "category"), "category"))
    types = refdata_db.distinct_values("HRMS_FAQ_BULLETIN", "TYPE")
    return rw._check_choice(a["category"], types, "category（公告類型）")


def _insert(kind, a, author, body):
    """寫入對應的資料表，回傳新編號。body 是加上填寫註記後的內文。"""
    if kind == "faq":
        return refdata_db.insert_row("HRMS_FAQ", {
            "QUESTION": a["title"], "ANSWER": body, "MODULE": a["module"],
            "SUB_CATEGORY": a["category"] or None, "RESPONDENT": author})
    if kind == "sop":
        return refdata_db.insert_row("HRMS_FAQ_SOP", {
            "title": a["title"], "content": body, "module": a["module"], "keywords": a["keywords"] or None,
            "description": a["description"] or None, "author": author, "is_active": 1})
    if kind == "task":
        return refdata_db.insert_row("HRMS_TASK", {
            "task_name": a["title"], "task_description": a["content"], "syntax": a["syntax"].rstrip(),
            "notes": body, "module": a["module"], "category": a["category"], "author": author},
            now_cols=("update_date",))
    return refdata_db.insert_row("HRMS_FAQ_BULLETIN", {
        "TITLE": a["title"], "CONTENT": body, "TYPE": a["category"], "AUTHOR": author})


def _contribute_sync(store, identity, kind, a, updates):
    missing = [f for f in REQUIRED[kind] if not a[f]]
    if missing:
        return _reject(f"{LABELS[kind]} 缺少必填欄位：{'、'.join(missing)}。{FIELD_HELP[kind]}")
    for field, limit in LIMITS[kind]:
        if len(a[field]) > limit:
            return _reject(f"{field} 超過 {limit} 字（目前 {len(a[field])} 字）。")
    if kind == "sop" and (rw.SOP_REQUIRED not in a["content"] or not any(h in a["content"] for h in rw.SOP_BODY)):
        return _reject(f"SOP 內文須依模板：要有「{rw.SOP_REQUIRED}」，並至少有 {'、'.join(rw.SOP_BODY)} 其中一段。")
    if kind == "task":
        errs = rw.check_sql(a["syntax"])
        if errs:
            return _reject("syntax 未通過檢查：\n" + "\n".join(f"- {e}" for e in errs))
    if not mdb.db_enabled():
        return ToolResult("⚠️ 伺服器未設定知識庫資料庫（MSSQL_*），無法寫入。",
                          write_outcome="failed", error="db not configured")

    author = f"{identity.name}(AI)"
    if len(author) > AUTHOR_MAX[kind]:
        return _reject(f"填寫人名稱過長，{LABELS[kind]} 的寫入者欄位放不下。")
    try:
        err = _choices(kind, a)
        if err:
            return ToolResult(err, write_outcome="rejected", error=err)

        old = None
        if updates is not None:
            old = next((r for r in refdata_db.load_all(kind) if r["id"] == updates), None)
            if old is None:
                return _reject(f"找不到要更新的 {kind}#{updates}，請用 kb_search 或 kb_get 回傳的編號。")
        else:
            sc, hit = _similar(kind, a, None)
            if hit:
                title = hit.get(rc.TABLE_CONF[kind]["topic_field"], "")
                return ToolResult(
                    f"♻️ 已有相似的 {LABELS[kind]}：{kind}#{hit['id']}【{title}】（相似 {sc:.2f}），未新增。\n"
                    f"如果是要更新那一筆的內容，請帶 updates={hit['id']} 重新呼叫：會新增一筆更新版，"
                    f"舊的保留並標示已有更新版本。內容其實相同就不用再寫。",
                    refs=[{"type": kind, "id": hit["id"], "score": round(sc, 3)}],
                    write_outcome="duplicate", write_ref=f"{kind}#{hit['id']}")

        body_source = a["notes"] if kind == "task" else a["content"]
        body = _footer(body_source, identity.name, a["basis"], f"{kind}#{updates}" if old else "")
        new_id = _insert(kind, a, author, body)
    except Exception as e:
        return ToolResult(f"⚠️ 寫入失敗：{type(e).__name__}: {e}", write_outcome="failed",
                          error=f"{type(e).__name__}: {e}")

    text = (f"✅ 已新增 {LABELS[kind]} {kind}#{new_id}【{a['title']}】，立刻可以查到。"
            f"填寫人 {identity.name}；這筆標示為線上填寫、未審核，權重低於人工整理的內容。")
    if old:
        try:
            flag_id = store.add_flag({
                "TARGET_TYPE": kind, "TARGET_ID": str(updates),
                "REASON": f"已有更新版本 {kind}#{new_id}（填寫人 {identity.name}）"
                          + (f"：{a['reason']}" if a["reason"] else ""),
                "EVIDENCE": a["basis"] or None, "JIRA_KEY": a["jira_key"] or None, "FLAG_SOURCE": "ai",
                "CANDIDATE_ID": None, "CREATED_BY": author})
            text += (f"\n原本的 {kind}#{updates} 沒有被修改，已加上疑慮標記 #{flag_id}，"
                     f"查到它的人會看到「已有更新版本」。之後由人或清理流程決定留哪一筆。")
        except Exception as e:
            text += f"\n⚠️ 新版已寫入，但在 {kind}#{updates} 加上標記時失敗（{type(e).__name__}），請告知管理者。"
    return ToolResult(text, refs=[{"type": kind, "id": new_id, "score": None}],
                      write_outcome="created", write_ref=f"{kind}#{new_id}")


@registry.tool(
    "kb_contribute",
    "把使用者提供的知識直接加進知識庫：FAQ、SOP、Task 罐頭語法、公告（含版更公告）。寫入後立刻查得到，不用審核；"
    "會記錄填寫人，並標示為線上填寫、未審核，權重低於人工整理的內容。\n"
    "使用時機：使用者明確表示要新增或更新一則 FAQ／SOP／Task／公告時。內容要來自使用者提供的資料，不可自行編寫或補充。"
    "系統行為、程式入口這類內部知識請改用 remember。\n"
    "更新既有內容：帶 updates=那一筆的編號。不會覆蓋原內容，而是新增一筆更新版，並在舊的那一筆標示已有更新版本。\n"
    "各類型欄位——faq：title（問題）、content（回覆）、module、category（子分類，可選）；"
    "sop：title（客戶需求）、content（依 SOP 模板）、module、keywords、description；"
    "task：title（語法名稱）、content（適用情境）、syntax（SQL 範本，會檢查）、notes（執行前確認事項）、module、category；"
    "bulletin：title、content、category（公告類型，版更公告用既有類型如「版本更新」）。"
    "module、category 須為既有值，填錯時會回傳可用的值。",
    {"type": "object",
     "properties": {
         "type": {"type": "string", "enum": list(TYPES)},
         "title": {"type": "string", "description": "FAQ 的問題／SOP 的客戶需求／Task 的語法名稱／公告標題"},
         "content": {"type": "string", "description": "FAQ 的回覆／SOP 內文／Task 的適用情境／公告內容"},
         "module": {"type": "string", "description": "模組（faq、sop、task 必填，須為既有值）"},
         "category": {"type": "string", "description": "FAQ 子分類（可選）／Task 分類／公告類型"},
         "syntax": {"type": "string", "description": "Task 的 SQL 範本"},
         "notes": {"type": "string", "description": "Task 執行前要確認的事項與影響範圍"},
         "keywords": {"type": "string", "description": "SOP 關鍵字，逗號分隔（可選）"},
         "description": {"type": "string", "description": "SOP 一句話摘要（可選）"},
         "updates": {"type": "string", "description": "要更新的既有編號（同一類型）；不帶就是新增"},
         "reason": {"type": "string", "description": "更新時：為什麼要更新（會顯示在舊那一筆的標記上）"},
         "basis": {"type": "string", "description": "依據：Jira 單號、文件名稱、版本號等（建議填）"},
         "jira_key": {"type": "string", "description": "相關的 Jira 單號（可選）"},
     },
     "required": ["type", "title", "content"]})
async def kb_contribute(call, args):
    kind = str(args.get("type") or "").strip().lower()
    if kind not in TYPES:
        return _reject(f"type 只能是 {'、'.join(TYPES)}。團隊記憶請用 remember。")
    a = {k: str(args.get(k) or "").strip() for k in
         ("title", "content", "module", "category", "syntax", "notes", "keywords", "description",
          "reason", "basis", "jira_key")}
    updates = None
    raw = str(args.get("updates") or "").strip().lstrip("#")
    if raw:
        raw = raw.split("#")[-1]      # 接受「faq#48」這種寫法
        if not raw.isdigit():
            return _reject(f"updates 須為編號：{args.get('updates')}")
        updates = int(raw)
    store, identity = call.services.store, call.identity

    def run():
        with tq._LOCK:
            return _contribute_sync(store, identity, kind, a, updates)
    result = await anyio.to_thread.run_sync(run)
    result.jira_key = a["jira_key"] or None
    return result
