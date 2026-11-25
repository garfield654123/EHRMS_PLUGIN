# -*- coding: utf-8 -*-
"""線上知識庫 MCP 的資料層：使用者對應（HRMS_KB_USER_BINDING）、使用紀錄（HRMS_KB_USAGE_LOG）、
候選（HRMS_KB_CANDIDATE）、疑慮標記（HRMS_KB_FLAG）與工作筆記（HRMS_MEMORY_NOTE）

兩種實作，介面相同：
- MssqlStore ：正式環境，表由 hrms_kb_online.sql 建立；只發 SELECT 與 INSERT
- SqliteStore：表還沒建好之前，或本機開發、測試用；欄位名稱與正式表相同，之後可以整批搬進去

兩者都是同步呼叫，由呼叫端放到執行緒裡跑；內部用一把鎖讓同一條連線不會被同時使用。
"""
import os
import sqlite3
import threading

USAGE_COLUMNS = (
    "EVENT_TIME", "USER_NAME", "USER_EMAIL", "IDENTITY_SOURCE", "CLIENT_ADDR",
    "SESSION_ID", "CALL_SEQ", "ORIGIN", "CLIENT_NAME",
    "TOOL_NAME", "ARGUMENTS", "QUERY_TEXT", "JIRA_KEY",
    "RESULT_REFS", "RESULT_COUNT", "NO_RESULT", "WRITE_OUTCOME", "WRITE_REF",
    "ELAPSED_MS", "ERROR_MSG", "SERVER_VERSION", "SEARCH_VERSION",
)
_USAGE_INSERT = (f"INSERT INTO {{table}} ({', '.join(USAGE_COLUMNS)}) "
                 f"VALUES ({', '.join('?' * len(USAGE_COLUMNS))})")


def _usage_values(row):
    return [row.get(c) for c in USAGE_COLUMNS]


CANDIDATE_COLUMNS = ("MEM_TYPE", "PURPOSE", "TOPIC", "CONTENT", "KEYWORDS", "FUNC_PATH", "ENTRY_PATH",
                     "ENTRY_METHOD", "REF_KEY", "SOURCE", "CREATED_BY", "CONFLICT_MEMORY_ID", "SIMILARITY")
FLAG_COLUMNS = ("TARGET_TYPE", "TARGET_ID", "REASON", "EVIDENCE", "JIRA_KEY", "FLAG_SOURCE",
                "CANDIDATE_ID", "CREATED_BY")
# 工作筆記（HRMS_MEMORY_NOTE）：處理結果欄位（STATUS 等）不寫，用資料表的預設值；
# USAGE_LOG_ID 也不寫——使用紀錄在工具執行完才產生，回填需要 UPDATE，而這裡只發 SELECT 與 INSERT。
# 要對回使用紀錄，用使用紀錄的 WRITE_REF（note#編號）或兩邊的 SESSION_ID。
NOTE_COLUMNS = ("NOTE_KIND", "TOPIC", "CONTENT", "EVIDENCE", "KEYWORDS", "ENTRY_PATH", "ENTRY_METHOD",
                "REF_KEY", "ORIGIN", "SESSION_ID", "CREATED_BY")


def _insert_sql(columns):
    return f"INSERT INTO {{table}} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"


_CANDIDATE_INSERT, _FLAG_INSERT = _insert_sql(CANDIDATE_COLUMNS), _insert_sql(FLAG_COLUMNS)
_NOTE_INSERT = _insert_sql(NOTE_COLUMNS)


def _candidate_values(c):
    return [c.get(col) for col in CANDIDATE_COLUMNS]


def _flag_values(f):
    return [f.get(col) for col in FLAG_COLUMNS]


def _note_values(n):
    return [n.get(col) for col in NOTE_COLUMNS]


class SqliteStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS HRMS_KB_USER_BINDING (
                ID INTEGER PRIMARY KEY AUTOINCREMENT, CLIENT_ADDR TEXT NOT NULL, USER_EMAIL TEXT NOT NULL,
                USER_NAME TEXT NOT NULL, JIRA_ACCOUNT_ID TEXT,
                CONFIRMED_AT TEXT NOT NULL DEFAULT (datetime('now', 'localtime')));
            CREATE INDEX IF NOT EXISTS IX_HRMSKBBIND_ADDR ON HRMS_KB_USER_BINDING (CLIENT_ADDR, ID);
            CREATE TABLE IF NOT EXISTS HRMS_KB_USAGE_LOG (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                EVENT_TIME TEXT NOT NULL, CREATED_AT TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                USER_NAME TEXT NOT NULL, USER_EMAIL TEXT, IDENTITY_SOURCE TEXT NOT NULL, CLIENT_ADDR TEXT,
                SESSION_ID TEXT NOT NULL, CALL_SEQ INTEGER NOT NULL, ORIGIN TEXT NOT NULL, CLIENT_NAME TEXT,
                TOOL_NAME TEXT NOT NULL, ARGUMENTS TEXT, QUERY_TEXT TEXT, JIRA_KEY TEXT,
                RESULT_REFS TEXT, RESULT_COUNT INTEGER, NO_RESULT INTEGER, WRITE_OUTCOME TEXT, WRITE_REF TEXT,
                ELAPSED_MS INTEGER, ERROR_MSG TEXT, SERVER_VERSION TEXT NOT NULL, SEARCH_VERSION TEXT);
            CREATE TABLE IF NOT EXISTS HRMS_KB_FLAG (
                ID INTEGER PRIMARY KEY AUTOINCREMENT, TARGET_TYPE TEXT NOT NULL, TARGET_ID TEXT NOT NULL,
                REASON TEXT NOT NULL, EVIDENCE TEXT, JIRA_KEY TEXT, FLAG_SOURCE TEXT NOT NULL,
                CANDIDATE_ID INTEGER, USAGE_LOG_ID INTEGER, CREATED_BY TEXT NOT NULL,
                CREATED_AT TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                STATUS TEXT NOT NULL DEFAULT 'open', RESOLVED_BY TEXT, RESOLVED_AT TEXT, RESOLVE_NOTE TEXT);
            CREATE TABLE IF NOT EXISTS HRMS_KB_CANDIDATE (
                ID INTEGER PRIMARY KEY AUTOINCREMENT, MEM_TYPE TEXT NOT NULL, PURPOSE TEXT, TOPIC TEXT NOT NULL,
                CONTENT TEXT NOT NULL, KEYWORDS TEXT, FUNC_PATH TEXT, ENTRY_PATH TEXT, ENTRY_METHOD TEXT,
                REF_KEY TEXT, SOURCE TEXT, CREATED_BY TEXT NOT NULL,
                CREATED_AT TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                CONFLICT_MEMORY_ID INTEGER NOT NULL, SIMILARITY REAL, USAGE_LOG_ID INTEGER,
                STATUS TEXT NOT NULL DEFAULT 'open', RESOLVED_BY TEXT, RESOLVED_AT TEXT,
                RESULT_MEMORY_ID INTEGER, RESOLVE_NOTE TEXT);
            CREATE TABLE IF NOT EXISTS HRMS_MEMORY_NOTE (
                ID INTEGER PRIMARY KEY AUTOINCREMENT, NOTE_KIND TEXT NOT NULL, TOPIC TEXT NOT NULL,
                CONTENT TEXT NOT NULL, EVIDENCE TEXT, KEYWORDS TEXT, ENTRY_PATH TEXT, ENTRY_METHOD TEXT,
                REF_KEY TEXT, ORIGIN TEXT NOT NULL, SESSION_ID TEXT, USAGE_LOG_ID INTEGER,
                CREATED_BY TEXT NOT NULL, CREATED_AT TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                STATUS TEXT NOT NULL DEFAULT 'open', RESOLVED_BY TEXT, RESOLVED_AT TEXT,
                RESULT_MEMORY_ID INTEGER, RESOLVE_NOTE TEXT);
        """)
        self._conn.commit()

    def find_binding(self, client_addr):
        """同一個位址以最新一列為準。回傳 dict 或 None。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT USER_EMAIL, USER_NAME, JIRA_ACCOUNT_ID FROM HRMS_KB_USER_BINDING "
                "WHERE CLIENT_ADDR = ? ORDER BY ID DESC LIMIT 1", (client_addr,)).fetchone()
        return {"email": r[0], "name": r[1], "account_id": r[2]} if r else None

    def add_binding(self, client_addr, email, name, account_id):
        with self._lock:
            self._conn.execute(
                "INSERT INTO HRMS_KB_USER_BINDING (CLIENT_ADDR, USER_EMAIL, USER_NAME, JIRA_ACCOUNT_ID) "
                "VALUES (?, ?, ?, ?)", (client_addr, email, name, account_id))
            self._conn.commit()

    def insert_usage(self, row):
        with self._lock:
            self._conn.execute(_USAGE_INSERT.format(table="HRMS_KB_USAGE_LOG"), _usage_values(row))
            self._conn.commit()

    def add_candidate(self, c):
        """存一筆候選，回傳編號。c 的鍵見 CANDIDATE_COLUMNS。"""
        with self._lock:
            cur = self._conn.execute(_CANDIDATE_INSERT.format(table="HRMS_KB_CANDIDATE"), _candidate_values(c))
            self._conn.commit()
            return cur.lastrowid

    def add_flag(self, f):
        """存一筆疑慮標記，回傳編號。f 的鍵見 FLAG_COLUMNS。"""
        with self._lock:
            cur = self._conn.execute(_FLAG_INSERT.format(table="HRMS_KB_FLAG"), _flag_values(f))
            self._conn.commit()
            return cur.lastrowid

    def add_note(self, n):
        """存一筆工作筆記，回傳編號。n 的鍵見 NOTE_COLUMNS。工作筆記只新增，這一層沒有讀取它的方法。"""
        with self._lock:
            cur = self._conn.execute(_NOTE_INSERT.format(table="HRMS_MEMORY_NOTE"), _note_values(n))
            self._conn.commit()
            return cur.lastrowid

    def open_flags(self):
        """待處理的疑慮標記：[{target_type, target_id, reason}]。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT TARGET_TYPE, TARGET_ID, REASON FROM HRMS_KB_FLAG WHERE STATUS = 'open' ORDER BY ID").fetchall()
        return [{"target_type": r[0], "target_id": r[1], "reason": r[2]} for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()

    # 測試與人工檢查用
    def usage_rows(self):
        with self._lock:
            cur = self._conn.execute("SELECT * FROM HRMS_KB_USAGE_LOG ORDER BY ID")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


class MssqlStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None

    def _connect(self):
        import pyodbc
        if self._conn is not None:
            try:
                self._conn.cursor().execute("SELECT 1").fetchone()
                return self._conn
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        preferred = ["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server",
                     "ODBC Driver 13 for SQL Server", "SQL Server Native Client 11.0", "SQL Server"]
        available = pyodbc.drivers()
        driver = next((d for d in preferred if d in available),
                      next((d for d in available if "SQL Server" in d), preferred[0]))
        self._conn = pyodbc.connect(
            f"DRIVER={{{driver}}};SERVER={os.getenv('MSSQL_SERVER')};DATABASE={os.getenv('MSSQL_DATABASE')};"
            f"UID={os.getenv('MSSQL_USERNAME')};PWD={os.getenv('MSSQL_PASSWORD')};TrustServerCertificate=yes;",
            timeout=5)
        self._conn.autocommit = True
        return self._conn

    def find_binding(self, client_addr):
        with self._lock:
            r = self._connect().cursor().execute(
                "SELECT TOP 1 USER_EMAIL, USER_NAME, JIRA_ACCOUNT_ID FROM dbo.HRMS_KB_USER_BINDING "
                "WHERE CLIENT_ADDR = ? ORDER BY ID DESC", (client_addr,)).fetchone()
        return {"email": r[0], "name": r[1], "account_id": r[2]} if r else None

    def add_binding(self, client_addr, email, name, account_id):
        with self._lock:
            self._connect().cursor().execute(
                "INSERT INTO dbo.HRMS_KB_USER_BINDING (CLIENT_ADDR, USER_EMAIL, USER_NAME, JIRA_ACCOUNT_ID) "
                "VALUES (?, ?, ?, ?)", (client_addr, email, name, account_id))

    def insert_usage(self, row):
        with self._lock:
            self._connect().cursor().execute(
                _USAGE_INSERT.format(table="dbo.HRMS_KB_USAGE_LOG"), _usage_values(row))

    def _insert_returning_id(self, sql, values):
        cur = self._connect().cursor()
        cur.execute(f"SET NOCOUNT ON; {sql}; SELECT CAST(SCOPE_IDENTITY() AS INT)", values)
        return cur.fetchone()[0]

    def add_candidate(self, c):
        with self._lock:
            return self._insert_returning_id(
                _CANDIDATE_INSERT.format(table="dbo.HRMS_KB_CANDIDATE"), _candidate_values(c))

    def add_flag(self, f):
        with self._lock:
            return self._insert_returning_id(_FLAG_INSERT.format(table="dbo.HRMS_KB_FLAG"), _flag_values(f))

    def add_note(self, n):
        with self._lock:
            return self._insert_returning_id(_NOTE_INSERT.format(table="dbo.HRMS_MEMORY_NOTE"), _note_values(n))

    def open_flags(self):
        with self._lock:
            rows = self._connect().cursor().execute(
                "SELECT TARGET_TYPE, TARGET_ID, REASON FROM dbo.vwHRMS_KB_FLAG_OPEN ORDER BY ID").fetchall()
        return [{"target_type": r[0], "target_id": r[1], "reason": r[2]} for r in rows]


def make_store(settings):
    if settings.store == "mssql":
        if not settings.mssql_ready():
            raise ValueError("KB_STORE=mssql 但 MSSQL_* 環境變數不齊全")
        return MssqlStore()
    if settings.store == "sqlite":
        return SqliteStore(settings.sqlite_path)
    raise ValueError(f"KB_STORE 只能是 mssql 或 sqlite：{settings.store}")
