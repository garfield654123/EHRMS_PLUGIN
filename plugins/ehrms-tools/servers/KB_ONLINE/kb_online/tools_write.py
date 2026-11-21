# -*- coding: utf-8 -*-
"""寫入工具：remember（寫入團隊記憶）與 kb_flag（疑慮標記）

線上版的寫入規則（SPEC.md 決策 8–11）：
- 寫入只有 remember 一個入口，只寫 HRMS_MEMORY；FAQ／SOP／Task／Jira 結案紀錄不由線上版寫入
- 不提供取代（supersedes）與強制寫入（force）；取代只由人或其他 agent 執行
- 與既有記憶幾乎相同 → 不新增
- 與既有記憶高度相似但不是同一個說法 → 新內容完整存成候選（不進正式記憶、不會被查到），
  並自動幫被撞到的那筆記憶加上疑慮標記；由人或清理 agent 決定要採用哪一個
- AI 發現既有知識可能有錯時，用 kb_flag 提出疑慮，不直接改

判斷「是不是同一個說法」只能靠字面比對，分不出結論是否相反，所以門檻設得保守：
只有內容幾乎相同才當作重複丟掉，其餘相似的一律存成候選，寧可多存不要丟。
"""
import re

import anyio

from . import tools_query as tq
from .dispatch import ToolResult
from .tools_core import registry

mc, mdb = tq.mc, tq.mdb
import pathnorm  # noqa: E402  （tools_query 已把 servers/MEMORY 加進路徑）
import refdata_write as rw  # noqa: E402

PURPOSES = ("reply", "procedure", "data_fix", "general")
SOURCE = "kb-online"
SAME_THRESHOLD = 0.90          # 內容相似度達到這個值才當作重複（不新增）
CONFLICT_THRESHOLD = mc.DUP_THRESHOLD   # 相似度達到這個值但不算重複 → 存成候選
LIMITS = (("topic", 100), ("content", 2000), ("keywords", 300), ("func_path", 300),
          ("entry_path", 500), ("entry_method", 300), ("ref_key", 50))
FLAG_TYPES = ("memory", "wiki", "faq", "sop", "task", "jira", "bulletin")
_SQL_BLOCK_RE = re.compile(r"```sql\s*\n(.*?)```", re.S | re.I)


def _content_sim(topic, content, row):
    """只比內容本身的字詞重疊（不含主題、關鍵字，也不加片語加權），用來判斷是不是幾乎相同的說法。

    主題與關鍵字相同、開頭幾句也相同但結論相反的兩筆，主題與片語加權會把分數推到接近 1；
    只比內容才分得開。
    """
    A, B = mc._bigrams(content), mc._bigrams(row["content"])
    if not A or not B:
        return 0.0
    return 2.0 * len(A & B) / (len(A) + len(B))


def _reject(message):
    return ToolResult(f"⚠️ {message}", write_outcome="rejected", error=message)


def _remember_sync(store, identity, a):
    kind, purpose = a["kind"], a["purpose"]
    topic, content = a["topic"], a["content"]
    if kind not in mc.KINDS:
        return _reject(f"kind 只能是 {' / '.join(mc.KINDS)}（System＝系統知識、Engineer＝程式入口）。")
    if purpose not in PURPOSES:
        return _reject("purpose 必填，只能是 reply（可回覆客戶）、procedure（處理步驟）、"
                       "data_fix（資料修正語法）、general（一般知識）。")
    if not topic or not content:
        return _reject("topic 與 content 為必填。")
    if kind == "Engineer" and not a["entry_path"]:
        return _reject("Engineer 記憶必須提供 entry_path（程式入口）。")
    if a["entry_path"]:
        a["entry_path"], err = pathnorm.normalize(a["entry_path"])
        if err:
            return _reject(f"entry_path 需為 repo 相對路徑（如 VB/EHRMS/xx.cls）：{err}")
    for field, limit in LIMITS:
        if len(a[field]) > limit:
            return _reject(f"{field} 超過 {limit} 字（目前 {len(a[field])} 字），請精簡後再寫入。")
    if purpose == "data_fix":
        errs = [e for block in _SQL_BLOCK_RE.findall(content) for e in rw.check_sql(block)]
        if errs:
            return _reject("內容裡的 SQL 未通過檢查：\n" + "\n".join(f"- {e}" for e in dict.fromkeys(errs)))
    if not mdb.db_enabled():
        return ToolResult("⚠️ 伺服器未設定知識庫資料庫（MSSQL_*），無法寫入。",
                          write_outcome="failed", error="db not configured")

    created_by = f"{identity.name}(AI)"
    try:
        best_sc, best = 0.0, None
        for r in mc.active_rows():
            if r["type"] != kind:
                continue    # 只在同型別內比：系統知識與程式入口描述同一功能不算重複
            sc = mc._dup_sim(topic, content, a["keywords"], r)
            if sc > best_sc:
                best_sc, best = sc, r

        if best and best_sc >= CONFLICT_THRESHOLD:
            same_entry = kind == "System" or tq.mc._base(a["entry_path"]) == tq.mc._base(best["entry_path"])
            if same_entry and _content_sim(topic, content, best) >= SAME_THRESHOLD:
                return ToolResult(
                    f"♻️ 已有幾乎相同的記憶 memory#{best['id']}【{best['topic']}】（相似 {best_sc:.2f}），未新增。",
                    refs=[{"type": "memory", "id": best["id"], "score": round(best_sc, 3)}],
                    write_outcome="duplicate", write_ref=f"memory#{best['id']}")
            cand_id = store.add_candidate({
                "MEM_TYPE": kind, "PURPOSE": purpose, "TOPIC": topic, "CONTENT": content,
                "KEYWORDS": a["keywords"] or None, "FUNC_PATH": a["func_path"] or None,
                "ENTRY_PATH": a["entry_path"] or None, "ENTRY_METHOD": a["entry_method"] or None,
                "REF_KEY": a["ref_key"] or None, "SOURCE": SOURCE, "CREATED_BY": created_by,
                "CONFLICT_MEMORY_ID": best["id"], "SIMILARITY": round(best_sc, 3)})
            flag_id = store.add_flag({
                "TARGET_TYPE": "memory", "TARGET_ID": str(best["id"]),
                "REASON": f"有新的不同說法（候選 #{cand_id}）：{topic}", "EVIDENCE": a["ref_key"] or None,
                "JIRA_KEY": a["ref_key"] or None, "FLAG_SOURCE": "auto_conflict", "CANDIDATE_ID": cand_id,
                "CREATED_BY": created_by})
            return ToolResult(
                f"📝 已記錄為待確認（候選 #{cand_id}）。\n"
                f"這筆內容與既有記憶 memory#{best['id']}【{best['topic']}】高度相似（{best_sc:.2f}）但不是同一個說法，"
                f"所以先不進正式記憶，並已在 memory#{best['id']} 加上疑慮標記（#{flag_id}），"
                f"之後由人或清理流程決定採用哪一個。你不需要再做任何處理。",
                refs=[{"type": "memory", "id": best["id"], "score": round(best_sc, 3)}],
                write_outcome="candidate", write_ref=f"candidate#{cand_id}")

        new_id = mdb.insert_memory(
            kind, topic, content, a["keywords"], a["func_path"], a["entry_path"], a["entry_method"],
            ref_key=a["ref_key"], source=SOURCE, meta={"purpose": purpose}, created_by=created_by)
    except Exception as e:
        return ToolResult(f"⚠️ 寫入失敗：{type(e).__name__}: {e}", write_outcome="failed",
                          error=f"{type(e).__name__}: {e}")
    return ToolResult(
        f"✅ 已記住 memory#{new_id}【{topic}】（{kind}，{tq.PURPOSE_LABELS[purpose]}；記錄者 {created_by}）。",
        write_outcome="created", write_ref=f"memory#{new_id}")


@registry.tool(
    "remember",
    "把已確認的結論寫入團隊記憶（線上版唯一的寫入入口）。只寫跨案件可通用、且已確認的系統行為或程式入口；"
    "推測、尚未驗證的不要寫，也不要寫客戶的實際資料（公司、員工、帳號）。寫入前先用 kb_search 確認沒有相同內容。\n"
    "kind：System＝系統使用層知識；Engineer＝功能→程式入口與邏輯要點（entry_path 必填）。\n"
    "purpose：reply＝可直接回覆客戶的問答；procedure＝處理某種需求的步驟；"
    "data_fix＝修正資料的語法（SQL 放在 ```sql 區塊內，會檢查）；general＝一般知識。\n"
    "線上版不能取代或覆蓋既有記憶：與既有記憶相似但說法不同時，伺服器會自動存成待確認並標記疑慮。"
    "要指出既有知識有錯，請用 kb_flag。",
    {"type": "object",
     "properties": {
         "kind": {"type": "string", "enum": list(mc.KINDS)},
         "purpose": {"type": "string", "enum": list(PURPOSES)},
         "topic": {"type": "string", "description": "主題短標題（100 字內），如『災防假加班時數計算』"},
         "content": {"type": "string", "description": "記憶內容（2000 字內）"},
         "keywords": {"type": "string", "description": "關鍵字，逗號分隔 3–8 個，用使用者會拿來問的詞"},
         "func_path": {"type": "string", "description": "畫面功能位置，如『出勤管理→假勤管理→報表』（可選）"},
         "entry_path": {"type": "string", "description": "程式入口的 repo 相對路徑（Engineer 必填）"},
         "entry_method": {"type": "string", "description": "入口函式（可選）"},
         "ref_key": {"type": "string", "description": "來源 Jira 單號；這次有對應的單就要填"},
     },
     "required": ["kind", "purpose", "topic", "content"]})
async def remember(call, args):
    a = {k: str(args.get(k) or "").strip() for k in
         ("kind", "purpose", "topic", "content", "keywords", "func_path", "entry_path", "entry_method", "ref_key")}
    store, identity = call.services.store, call.identity

    def run():
        with tq._LOCK:
            return _remember_sync(store, identity, a)
    result = await anyio.to_thread.run_sync(run)
    result.jira_key = a["ref_key"] or None
    return result


# ── kb_flag：疑慮標記 ────────────────────────────────────────────

def _flag_sync(store, identity, kind, target, reason, evidence, jira_key):
    if not mdb.db_enabled():
        return ToolResult("⚠️ 伺服器未設定知識庫資料庫（MSSQL_*），無法確認要標記的知識是否存在。",
                          write_outcome="failed", error="db not configured")
    page, _, rule = target.partition("#") if kind == "wiki" else (target, "", "")
    try:
        text, found_id = tq.GETTERS[kind](page, section=rule or None, customer_code=None)
    except Exception as e:
        return ToolResult(f"⚠️ 確認目標時失敗：{type(e).__name__}: {e}", write_outcome="failed",
                          error=f"{type(e).__name__}: {e}")
    if text is None or (rule and text.startswith("⚠️")):
        return _reject(f"找不到 {kind}#{target}，請用 kb_search 或 kb_get 回傳的編號。")
    target_id = f"{found_id}#{rule.upper()}" if rule else str(found_id)
    try:
        flag_id = store.add_flag({
            "TARGET_TYPE": kind, "TARGET_ID": target_id, "REASON": reason, "EVIDENCE": evidence or None,
            "JIRA_KEY": jira_key or None, "FLAG_SOURCE": "ai", "CANDIDATE_ID": None,
            "CREATED_BY": f"{identity.name}(AI)"})
    except Exception as e:
        return ToolResult(f"⚠️ 標記失敗：{type(e).__name__}: {e}", write_outcome="failed",
                          error=f"{type(e).__name__}: {e}")
    return ToolResult(
        f"🚩 已標記疑慮 #{flag_id}：{kind}#{target_id}。之後查到這筆知識時會顯示「有疑慮」與理由；"
        f"原內容不會被修改，由人或清理流程處理。",
        refs=[{"type": kind, "id": found_id, "score": None}], write_outcome="flagged", write_ref=f"flag#{flag_id}")


@registry.tool(
    "kb_flag",
    "對既有知識提出疑慮：發現某筆知識可能有錯、過時，或與程式、其他知識、實際案件結果矛盾時呼叫。"
    "不會修改原內容，只會在之後查到時顯示「有疑慮」與理由，由人或清理流程處理。"
    "type 與 id 用 kb_search／kb_get 回傳的編號；Wiki 可指到某條規則，id 寫成「主題代碼#規則編號」（如 CARD#R7）。"
    "reason 要寫清楚哪裡不對，evidence 寫依據（程式位置、單號、另一筆知識的編號）。只有猜測、沒有依據時不要標記。",
    {"type": "object",
     "properties": {
         "type": {"type": "string", "enum": list(FLAG_TYPES)},
         "id": {"type": "string", "description": "編號；Wiki 為主題代碼，可加 #規則編號"},
         "reason": {"type": "string", "description": "哪裡可能有錯（1000 字內）"},
         "evidence": {"type": "string", "description": "依據：程式位置、Jira 單號、其他知識的編號"},
         "jira_key": {"type": "string", "description": "發現這個疑慮時正在處理的 Jira 單號（可選）"},
     },
     "required": ["type", "id", "reason", "evidence"]})
async def kb_flag(call, args):
    kind = str(args.get("type") or "").strip()
    target = str(args.get("id") or "").strip().lstrip("#")
    reason, evidence = str(args.get("reason") or "").strip(), str(args.get("evidence") or "").strip()
    jira_key = str(args.get("jira_key") or "").strip()
    if kind not in FLAG_TYPES or not target:
        return _reject(f"type 只能是 {'、'.join(FLAG_TYPES)}，且 id 必填。")
    if not reason or not evidence:
        return _reject("reason（哪裡不對）與 evidence（依據）都必填；沒有依據請先查證，不要標記。")
    if len(reason) > 1000 or len(evidence) > 1000:
        return _reject("reason 與 evidence 各限 1000 字。")
    store, identity = call.services.store, call.identity

    def run():
        with tq._LOCK:
            return _flag_sync(store, identity, kind, target, reason, evidence, jira_key)
    result = await anyio.to_thread.run_sync(run)
    result.jira_key = jira_key or None
    return result
