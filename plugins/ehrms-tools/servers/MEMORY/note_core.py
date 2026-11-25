# -*- coding: utf-8 -*-
"""工作筆記（HRMS_MEMORY_NOTE）：note_log 的寫入邏輯

工作筆記記下每次處理的過程或結論，不必等結論確認、對錯不論（寫錯由審核處理）。
寫不寫看「做了什麼動作」：這輪只要讀過程式碼、查過資料表結構、讀過 Jira 單，或被使用者糾正，就寫。
- process   ：過程紀錄（做了什麼、查到什麼、卡在哪），沒有結論也能寫；kind 沒給時的預設
- finding   ：知識庫以外查出來的結論（例如讀程式碼追出來的）
- correction：使用者對 AI 的修正
它不是正式記憶：這裡只新增，不查重、不讀取，recall 等查詢工具查不到；
之後由審核流程確認正確才寫進 HRMS_MEMORY。處理結果欄位（STATUS 等）不寫，用資料表預設值 open。

規則與線上知識庫 MCP 的 kb_note 相同（servers/KB_ONLINE/kb_online/tools_note.py），
建表語法在 servers/KB_ONLINE/hrms_kb_online.sql 第 ⑤ 段。連線沿用 memory_db（同一組 MSSQL 帳號）。
"""
import uuid

import memory_db as db
import pathnorm

T = "dbo.HRMS_MEMORY_NOTE"
KINDS = ("process", "finding", "correction")
DEFAULT_KIND = "process"
KIND_LABELS = {"process": "過程紀錄", "finding": "結論", "correction": "使用者的修正"}
PROCESS_MARK = "〔過程紀錄〕"
LIMITS = (("topic", 100), ("content", 4000), ("evidence", 2000), ("keywords", 300),
          ("entry_path", 500), ("entry_method", 300), ("ref_key", 50))
COLUMNS = ("NOTE_KIND", "TOPIC", "CONTENT", "EVIDENCE", "KEYWORDS", "ENTRY_PATH", "ENTRY_METHOD",
           "REF_KEY", "ORIGIN", "SESSION_ID", "CREATED_BY")

# 本機 MCP 沒有連線編號；用這個行程啟動時產生的編號，讓同一次對話寫的筆記可以歸在一起。
# 本機也沒有使用紀錄，所以 USAGE_LOG_ID 不寫。
SESSION_ID = uuid.uuid4().hex
# 來源一律記為對話：MEMORY_USER 的排程專用值沒有固定寫法，不能拿來可靠地判斷是不是排程在跑。
ORIGIN = "chat"


def _execute(row):
    cur = db._get_conn().cursor()
    cur.execute(
        f"SET NOCOUNT ON; INSERT INTO {T}({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))}); "
        f"SELECT CAST(SCOPE_IDENTITY() AS INT)", [row.get(c) for c in COLUMNS])
    return cur.fetchone()[0]


def _insert(row):
    """新增一筆，回傳編號。row 的鍵見 COLUMNS。

    資料表的種類限制還沒加上 process 時（hrms_kb_online.sql「補丁（2026-11-25）」未套用），
    過程紀錄改存成 finding，內容最前面加上標記；補丁套用後就不會走到這裡。"""
    try:
        return _execute(row)
    except Exception as e:
        if row.get("NOTE_KIND") != "process" or "CK_HRMSMEMNOTE_KIND" not in str(e):
            raise
        return _execute(dict(row, NOTE_KIND="finding", CONTENT=(PROCESS_MARK + row["CONTENT"])[:4000]))


def log_note(topic, content, kind="", evidence="", keywords="", entry_path="", entry_method="", ref_key=""):
    a = {k: str(v or "").strip() for k, v in (
        ("kind", kind), ("topic", topic), ("content", content), ("evidence", evidence), ("keywords", keywords),
        ("entry_path", entry_path), ("entry_method", entry_method), ("ref_key", ref_key))}
    a["kind"] = a["kind"] or DEFAULT_KIND
    if a["kind"] not in KINDS:
        return "⚠️ kind 只能是 process（過程紀錄，預設）、finding（查出來的結論）或 correction（使用者的修正）；不確定就不要填。"
    if not a["topic"] or not a["content"]:
        return "⚠️ topic 與 content 為必填。"
    if a["entry_path"]:
        # 能轉成 repo 相對路徑就轉；轉不了照原樣留著，筆記不因此被擋下
        normalized, err = pathnorm.normalize(a["entry_path"])
        if not err:
            a["entry_path"] = normalized
    for field, limit in LIMITS:
        if len(a[field]) > limit:
            return f"⚠️ {field} 超過 {limit} 字（目前 {len(a[field])} 字），請精簡後再寫入。"
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，此功能停用。"

    created_by = db.created_by_ai()
    try:
        note_id = _insert({
            "NOTE_KIND": a["kind"], "TOPIC": a["topic"], "CONTENT": a["content"],
            "EVIDENCE": a["evidence"] or None, "KEYWORDS": a["keywords"] or None,
            "ENTRY_PATH": a["entry_path"] or None, "ENTRY_METHOD": a["entry_method"] or None,
            "REF_KEY": a["ref_key"] or None, "ORIGIN": ORIGIN, "SESSION_ID": SESSION_ID,
            "CREATED_BY": created_by})
    except Exception as e:
        return f"⚠️ 工作筆記寫入失敗：{e}"
    return (f"📝 已寫入工作筆記 note#{note_id}【{a['topic']}】（{KIND_LABELS[a['kind']]}；記錄者 {created_by}）。"
            f"之後每查出一件事就再記一筆。工作筆記不是正式記憶、查詢不會查到，審核確認正確才會升級。")


# ── 查詢結果結尾的提醒：這個 MCP 行程（＝一次對話）查了幾次、寫了幾筆 ──

_activity = {"lookups": 0, "notes": 0}


def count_note(text):
    """note_log 回傳成功訊息時計入已寫的筆數。"""
    if text.startswith("📝"):
        _activity["notes"] += 1
    return text


def with_reminder(text):
    """查詢工具的回傳結尾附上帶數字的提醒；查詢本身失敗（⚠️ 開頭）時不附。"""
    if text.startswith("⚠️"):
        return text
    _activity["lookups"] += 1
    lookups, notes = _activity["lookups"], _activity["notes"]
    if notes:
        tail = (f"📝 這次對話已查詢 {lookups} 次、已寫 {notes} 筆工作筆記。之後每讀一段程式碼、資料表結構或 Jira 單，"
                f"就當場再用 note_log 記一筆，不要等到回覆前。")
    else:
        tail = (f"⚠ 這次對話已查詢 {lookups} 次，尚未寫工作筆記。這輪只要讀過程式碼、資料表結構或 Jira 單，或被使用者糾正，"
                f"現在就用 note_log 記一筆（只填 topic、content；不需確認、對錯不論，寫錯由審核處理）。只查知識庫的不用寫。")
    return f"{text}\n\n{tail}"
