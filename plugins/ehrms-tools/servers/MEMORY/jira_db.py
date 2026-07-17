# -*- coding: utf-8 -*-
"""HRMS_JIRA 資料層：Jira 維運單結案紀錄（append-only）

權限邊界（比照 hrms_jira.sql 的 GRANT/DENY）：
- 只發 SELECT / INSERT；整表 DENY UPDATE/DELETE（比 HRMS_MEMORY 更嚴）
- 修正結論一律以「插入新記錄 + SUPERSEDES_ID」表達，讀取端排除被取代者

連線與記錄者身分沿用 memory_db（同一個 MSSQL 帳號與 JIRA_EMAIL）。
"""
import json
import time

import memory_db as mdb

T = "dbo.HRMS_JIRA"

_CACHE_TTL = 60  # 秒；同 process 短快取，避免每次 lookup 都整表重載
_cache = {}


def invalidate_cache():
    _cache.clear()


def load_all():
    """整表載入為 dict 列表（60 秒快取）。有效性（supersede 過濾）由 jira_core 判斷。"""
    now = time.time()
    hit = _cache.get("all")
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    cur = mdb._get_conn().cursor()
    rows = []
    for r in cur.execute(
            f"SELECT ID,JIRA_KEY,ISSUE_KIND,TITLE,ROOT_CAUSE,RESOLUTION,"
            f"CHANGED_FILES,KEYWORDS,SUPERSEDES_ID,CREATED_BY,CREATED_AT,META FROM {T}"):
        try:
            meta = json.loads(r[11]) if r[11] else {}
        except Exception:
            meta = {}
        rows.append({
            "id": r[0], "key": r[1] or "", "kind": r[2] or "",
            "title": r[3] or "", "root_cause": r[4] or "",
            "resolution": r[5] or "", "changed_files": r[6] or "",
            "keywords": r[7] or "", "supersedes": r[8],
            "created_by": r[9] or "", "created_at": str(r[10])[:10],
            "meta": meta,
        })
    _cache["all"] = (now, rows)
    return rows


def insert_case(jira_key, kind, title, root_cause, resolution,
                changed_files="", keywords="", supersedes_id=None, meta=None):
    """插入一筆結案紀錄，回傳新 ID。"""
    cur = mdb._get_conn().cursor()
    cur.execute(
        f"SET NOCOUNT ON; "
        f"INSERT INTO {T}(JIRA_KEY,ISSUE_KIND,TITLE,ROOT_CAUSE,RESOLUTION,"
        f"CHANGED_FILES,KEYWORDS,SUPERSEDES_ID,CREATED_BY,META)"
        f" VALUES (?,?,?,?,?,?,?,?,?,?); "
        f"SELECT CAST(SCOPE_IDENTITY() AS INT)",
        (jira_key, kind, title, root_cause, resolution,
         changed_files or None, keywords or None, supersedes_id,
         mdb.created_by(),
         json.dumps(meta, ensure_ascii=False) if meta else None))
    new_id = cur.fetchone()[0]
    invalidate_cache()
    return new_id
