# -*- coding: utf-8 -*-
"""工作筆記（HRMS_MEMORY_NOTE）：note_log 的寫入邏輯

工作筆記存的是每次處理中「知識庫以外」得到的結論，不必等結論確認：
- finding   ：查問題過程中另外查出來的新結論（例如讀程式碼追出來的）
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
KINDS = ("finding", "correction")
KIND_LABELS = {"finding": "新結論", "correction": "使用者的修正"}
LIMITS = (("topic", 100), ("content", 4000), ("evidence", 2000), ("keywords", 300),
          ("entry_path", 500), ("entry_method", 300), ("ref_key", 50))
COLUMNS = ("NOTE_KIND", "TOPIC", "CONTENT", "EVIDENCE", "KEYWORDS", "ENTRY_PATH", "ENTRY_METHOD",
           "REF_KEY", "ORIGIN", "SESSION_ID", "CREATED_BY")

# 本機 MCP 沒有連線編號；用這個行程啟動時產生的編號，讓同一次對話寫的筆記可以歸在一起。
# 本機也沒有使用紀錄，所以 USAGE_LOG_ID 不寫。
SESSION_ID = uuid.uuid4().hex
# 來源一律記為對話：MEMORY_USER 的排程專用值沒有固定寫法，不能拿來可靠地判斷是不是排程在跑。
ORIGIN = "chat"


def _insert(row):
    """新增一筆，回傳編號。row 的鍵見 COLUMNS。"""
    cur = db._get_conn().cursor()
    cur.execute(
        f"SET NOCOUNT ON; INSERT INTO {T}({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))}); "
        f"SELECT CAST(SCOPE_IDENTITY() AS INT)", [row.get(c) for c in COLUMNS])
    return cur.fetchone()[0]


def log_note(kind, topic, content, evidence="", keywords="", entry_path="", entry_method="", ref_key=""):
    a = {k: str(v or "").strip() for k, v in (
        ("kind", kind), ("topic", topic), ("content", content), ("evidence", evidence), ("keywords", keywords),
        ("entry_path", entry_path), ("entry_method", entry_method), ("ref_key", ref_key))}
    if a["kind"] not in KINDS:
        return "⚠️ kind 只能是 finding（知識庫以外查出來的新結論）或 correction（使用者對 AI 的修正）。"
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
            f"工作筆記不是正式記憶、查詢不會查到，之後審核確認正確才會升級成團隊記憶。")
