# -*- coding: utf-8 -*-
"""HRMS_FAQ / HRMS_FAQ_BULLETIN / HRMS_TASK / HRMS_FAQ_SOP 資料層（純查詢，直接讀原表）

三表皆由人工在 DB 端直接維護（INSERT/UPDATE），MCP 帳號僅有 SELECT 權限，
本模組沒有任何寫入函式。連線與環境變數沿用 memory_db（同一組 MSSQL 帳號）。
"""
import time

import memory_db as mdb

db_enabled = mdb.db_enabled

_CACHE_TTL = 60  # 秒；同 process 短快取，避免每次 lookup 都整表重載
_cache = {}

# 直接查詢底層原表（不使用 vwHRMS_*_ACTIVE view）。
# 實際 DB 的表結構與原設計（hrms_refdata.sql）不同，故在 SELECT 內以別名對映成統一欄位名：
#   faq      <- HRMS_FAQ          (FAQ_ID/QUESTION/ANSWER/MODULE/SUB_CATEGORY)
#   bulletin <- HRMS_FAQ_BULLETIN (僅取 IS_DELETE=0 且 SHOW=1，即未刪除且顯示中的公告)
#   task     <- HRMS_TASK         (task_id/task_name/task_description/syntax/notes；ntext 轉 nvarchar(max))
# 原表沒有 ENABLED/KEYWORDS/RISK_LEVEL 等欄位，分別以固定值或 module/category 補位。
# 注意：三表 REVIEW_STATUS 目前皆為 pending，故不以審核狀態過濾。
TABLES = {
    "faq": {
        "sql": ("SELECT FAQ_ID AS ID, QUESTION, ANSWER, "
                "ISNULL(MODULE, N'') AS CATEGORY, ISNULL(SUB_CATEGORY, N'') AS KEYWORDS "
                "FROM dbo.HRMS_FAQ"),
        "columns": ["ID", "QUESTION", "ANSWER", "CATEGORY", "KEYWORDS"],
    },
    "bulletin": {
        "sql": ("SELECT ID, TITLE, CONTENT, ISNULL(TYPE, N'') AS CATEGORY, N'' AS KEYWORDS "
                "FROM dbo.HRMS_FAQ_BULLETIN WHERE IS_DELETE = 0 AND SHOW = 1"),
        "columns": ["ID", "TITLE", "CONTENT", "CATEGORY", "KEYWORDS"],
    },
    "task": {
        "sql": ("SELECT task_id AS ID, task_name AS TASK_NAME, "
                "CONVERT(NVARCHAR(MAX), task_description) AS SCENARIO, "
                "CONVERT(NVARCHAR(MAX), syntax) AS SQL_TEMPLATE, "
                "N'' AS PARAMS, N'' AS AFFECTED_TABLE, N'未分級' AS RISK_LEVEL, "
                "CONVERT(NVARCHAR(MAX), notes) AS PRECAUTIONS, "
                "ISNULL(module, N'') + N' ' + ISNULL(category, N'') AS KEYWORDS "
                "FROM dbo.HRMS_TASK"),
        "columns": ["ID", "TASK_NAME", "SCENARIO", "SQL_TEMPLATE", "PARAMS",
                    "AFFECTED_TABLE", "RISK_LEVEL", "PRECAUTIONS", "KEYWORDS"],
    },
    # sop <- HRMS_FAQ_SOP（客戶需求處理 SOP：前置確認、操作路徑或 Task、需請客戶補的資料）
    "sop": {
        "sql": ("SELECT sop_id AS ID, title AS TITLE, ISNULL(description, N'') AS DESCRIPTION, "
                "CONVERT(NVARCHAR(MAX), content) AS CONTENT, ISNULL(module, N'') AS CATEGORY, "
                "ISNULL(keywords, N'') AS KEYWORDS "
                "FROM dbo.HRMS_FAQ_SOP WHERE is_active = 1"),
        "columns": ["ID", "TITLE", "DESCRIPTION", "CONTENT", "CATEGORY", "KEYWORDS"],
    },
}

def invalidate_cache(table_key=None):
    if table_key:
        _cache.pop(table_key, None)
    else:
        _cache.clear()


def load_all(table_key):
    """整表（原表別名對映）載入為 dict 列表（60 秒快取，同 jira_db／memory_db 的作法）。"""
    now = time.time()
    hit = _cache.get(table_key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    conf = TABLES[table_key]
    cols = conf["columns"]
    cur = mdb._get_conn().cursor()
    rows = []
    for r in cur.execute(conf["sql"]):
        rows.append({col.lower(): (r[i] if r[i] is not None else "")
                      for i, col in enumerate(cols)})
    _cache[table_key] = (now, rows)
    return rows
