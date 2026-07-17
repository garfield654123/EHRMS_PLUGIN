# -*- coding: utf-8 -*-
"""ehrms-memory 資料層：HRMS_MEMORY 單表（append-only）

權限邊界（比照 hrms_memory.sql 的 GRANT/DENY）：
- 只發 SELECT / INSERT，以及 HIT_COUNT / LAST_HIT_AT 兩欄的 UPDATE
- 絕不 DELETE、絕不改記憶內容與 REVIEW_* 欄位
- 訂正/淘汰一律以「插入新記錄 + SUPERSEDES_ID」表達，讀取端排除被取代者
"""
import os
import json
import time

T = "dbo.HRMS_MEMORY"

_CACHE_TTL = 60  # 秒；同 process 短快取，避免每次 recall 都整表重載
_cache = {}
_conn = None


def db_enabled():
    """MSSQL_* 環境變數齊全才啟用。"""
    return all(os.getenv(k) for k in (
        "MSSQL_SERVER", "MSSQL_DATABASE", "MSSQL_USERNAME", "MSSQL_PASSWORD"))


def created_by():
    """記錄者身分，與 Jira 使用者一致：
    MEMORY_USER > JIRA_EMAIL 的 @ 前段 > Windows 帳號。"""
    u = os.getenv("MEMORY_USER")
    if u:
        return u
    email = os.getenv("JIRA_EMAIL") or ""
    if "@" in email:
        return email.split("@", 1)[0]
    return os.getenv("USERNAME") or os.getenv("USER") or "unknown"


def _get_conn():
    global _conn
    import pyodbc
    if _conn is not None:
        try:
            _conn.cursor().execute("SELECT 1").fetchone()
            return _conn
        except Exception:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
    preferred = [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ]
    available = pyodbc.drivers()
    driver = next((d for d in preferred if d in available),
                  next((d for d in available if "SQL Server" in d), preferred[0]))
    _conn = pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={os.getenv('MSSQL_SERVER')};"
        f"DATABASE={os.getenv('MSSQL_DATABASE')};"
        f"UID={os.getenv('MSSQL_USERNAME')};"
        f"PWD={os.getenv('MSSQL_PASSWORD')};"
        f"TrustServerCertificate=yes;",
        timeout=5)
    _conn.autocommit = True
    return _conn


def invalidate_cache():
    _cache.clear()


def load_all():
    """整表載入為 dict 列表（60 秒快取）。有效性（supersede/rejected 過濾）由 memory_core 判斷。"""
    now = time.time()
    hit = _cache.get("all")
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    cur = _get_conn().cursor()
    rows = []
    for r in cur.execute(
            f"SELECT ID,MEM_TYPE,TOPIC,CONTENT,KEYWORDS,FUNC_PATH,ENTRY_PATH,ENTRY_METHOD,"
            f"SUPERSEDES_ID,REF_KEY,SOURCE,INDEX_SHA,CREATED_BY,CREATED_AT,"
            f"HIT_COUNT,LAST_HIT_AT,REVIEW_STATUS,META FROM {T}"):
        try:
            meta = json.loads(r[17]) if r[17] else {}
        except Exception:
            meta = {}
        rows.append({
            "id": r[0], "type": r[1], "topic": r[2] or "", "content": r[3] or "",
            "keywords": r[4] or "", "func_path": r[5] or "",
            "entry_path": r[6] or "", "entry_method": r[7] or "",
            "supersedes": r[8], "ref_key": r[9] or "", "source": r[10] or "",
            "index_sha": r[11] or "", "created_by": r[12] or "",
            "created_at": str(r[13])[:10],
            "hits": r[14], "last_hit": str(r[15])[:10] if r[15] else "",
            "review": r[16] or "pending", "meta": meta,
        })
    _cache["all"] = (now, rows)
    return rows


def insert_memory(mem_type, topic, content, keywords="", func_path="",
                  entry_path="", entry_method="", supersedes_id=None,
                  ref_key="", source="", index_sha="", meta=None):
    """插入一筆記憶，回傳新 ID。"""
    cur = _get_conn().cursor()
    cur.execute(
        f"SET NOCOUNT ON; "
        f"INSERT INTO {T}(MEM_TYPE,TOPIC,CONTENT,KEYWORDS,FUNC_PATH,ENTRY_PATH,ENTRY_METHOD,"
        f"SUPERSEDES_ID,REF_KEY,SOURCE,INDEX_SHA,CREATED_BY,META)"
        f" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?); "
        f"SELECT CAST(SCOPE_IDENTITY() AS INT)",
        (mem_type, topic, content, keywords or None, func_path or None,
         entry_path or None, entry_method or None, supersedes_id,
         ref_key or None, source or None, index_sha or None, created_by(),
         json.dumps(meta, ensure_ascii=False) if meta else None))
    new_id = cur.fetchone()[0]
    invalidate_cache()
    return new_id


def bump_hits(ids):
    """recall 命中回寫（使用即強化）。只動 MCP 唯二有 UPDATE 權限的兩欄。"""
    if not ids:
        return
    cur = _get_conn().cursor()
    marks = ",".join("?" * len(ids))
    cur.execute(
        f"UPDATE {T} SET HIT_COUNT=HIT_COUNT+1, LAST_HIT_AT=SYSDATETIME() WHERE ID IN ({marks})",
        list(ids))
    invalidate_cache()
