# -*- coding: utf-8 -*-
"""HRMS_FAQ / HRMS_BULLETIN / HRMS_TASK 資料層（純查詢，只讀 active view）

三表皆由人工在 DB 端直接維護（INSERT/UPDATE），MCP 帳號僅有 SELECT 權限，
本模組沒有任何寫入函式。連線與環境變數沿用 memory_db（同一組 MSSQL 帳號）。
"""
import time

import memory_db as mdb

db_enabled = mdb.db_enabled

_CACHE_TTL = 60  # 秒；同 process 短快取，避免每次 lookup 都整表重載
_cache = {}

TABLES = {
    "faq": {
        "view": "dbo.vwHRMS_FAQ_ACTIVE",
        "columns": ["ID", "QUESTION", "ANSWER", "CATEGORY", "KEYWORDS"],
    },
    "bulletin": {
        "view": "dbo.vwHRMS_BULLETIN_ACTIVE",
        "columns": ["ID", "TITLE", "CONTENT", "CATEGORY", "KEYWORDS"],
    },
    "task": {
        "view": "dbo.vwHRMS_TASK_ACTIVE",
        "columns": ["ID", "TASK_NAME", "SCENARIO", "SQL_TEMPLATE", "PARAMS",
                    "AFFECTED_TABLE", "RISK_LEVEL", "PRECAUTIONS", "KEYWORDS"],
    },
}


def invalidate_cache(table_key=None):
    if table_key:
        _cache.pop(table_key, None)
    else:
        _cache.clear()


def load_all(table_key):
    """整表（active view）載入為 dict 列表（60 秒快取，同 jira_db／memory_db 的作法）。"""
    now = time.time()
    hit = _cache.get(table_key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    conf = TABLES[table_key]
    cols = conf["columns"]
    cur = mdb._get_conn().cursor()
    rows = []
    for r in cur.execute(f"SELECT {','.join(cols)} FROM {conf['view']}"):
        rows.append({col.lower(): (r[i] if r[i] is not None else "")
                      for i, col in enumerate(cols)})
    _cache[table_key] = (now, rows)
    return rows
