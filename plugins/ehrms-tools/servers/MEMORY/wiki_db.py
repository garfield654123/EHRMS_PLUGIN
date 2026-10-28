# -*- coding: utf-8 -*-
"""HRMS_WIKI 資料層（純查詢，只讀 vwHRMS_WIKI_SEARCH）

Wiki 正本在 104corp/eHRMS-Wiki（git），由少數指定工程師以 /wiki-publish 發佈到 HRMS_WIKI；
本模組只讀檢索 View（有效的 article／custom／reference），沒有任何寫入函式。
連線與環境變數沿用 memory_db（同一組 MSSQL 帳號）。
"""
import time

import memory_db as mdb

db_enabled = mdb.db_enabled

_CACHE_TTL = 60  # 秒；作法同 refdata_db
_cache = {}

COLUMNS = ["wiki_id", "GIT_PATH", "DOC_TYPE", "TOPIC_CODE", "CUSTOMER_CODE", "title",
           "keywords", "description", "content", "GIT_COMMIT", "OPEN_REVIEW_COUNT"]

SQL = f"SELECT {', '.join(COLUMNS)} FROM dbo.vwHRMS_WIKI_SEARCH"


def invalidate_cache():
    _cache.clear()


def load_all():
    """整個檢索 View 載入為 dict 列表（key 一律小寫）。"""
    now = time.time()
    hit = _cache.get("all")
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    cur = mdb._get_conn().cursor()
    rows = []
    for r in cur.execute(SQL):
        rows.append({col.lower(): (r[i] if r[i] is not None else "")
                     for i, col in enumerate(COLUMNS)})
    _cache["all"] = (now, rows)
    return rows
