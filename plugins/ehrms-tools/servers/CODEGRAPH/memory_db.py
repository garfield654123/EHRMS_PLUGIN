# -*- coding: utf-8 -*-
"""codegraph 記憶層：單表 HRMS_MEMORY（append-only）＋ anchors.json 備援

設計（參考 Letta passages / LangMem 單存放區模式）：
- 一張表存所有記憶，MEM_TYPE 區分：episode（問題→入口）/ fact（系統知識）
  / anchor（領域錨點）/ synonym（同義詞）
- append-only：內容永不 UPDATE；anchor/synonym 的「修正」=插入新版本，
  讀取端同主題取最新一筆（版本歷史自然保留）
- 人工 review：REVIEW_STATUS pending/verified/rejected。verified 提升檢索可信度、
  rejected 從檢索中剔除。此欄位由獨立驗證工具（較高權限帳號）維護，
  本模組（MCP 帳號）無權限寫入。

安全邊界：只操作白名單單表、全程參數化、只發 SELECT/INSERT 與
HIT_COUNT/LAST_HIT_AT 的 UPDATE，絕不 DELETE、絕不改記憶內容與 review 欄位。
"""
import os
import json
import time

T_MEMORY = "dbo.HRMS_MEMORY"

_CACHE_TTL = 60  # 秒；同一 process 內短快取，避免每次 find_entry 都打 DB
_cache = {}
_conn = None


def _user():
    return os.getenv("USERNAME") or os.getenv("USER") or ""


def db_enabled():
    """MSSQL_* 環境變數齊全才啟用 DB 記憶。"""
    return all(os.getenv(k) for k in (
        "MSSQL_SERVER", "MSSQL_DATABASE", "MSSQL_USERNAME", "MSSQL_PASSWORD"))


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


def _cached(key, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    data = loader()
    _cache[key] = (now, data)
    return data


def invalidate_cache():
    _cache.clear()


# ── 讀取：整表載入（快取）→ 依型別衍生 ─────────────────────

def load_all():
    """整表載入為 dict 列表（60 秒快取）。"""
    def _load():
        cur = _get_conn().cursor()
        rows = []
        for r in cur.execute(
                f"SELECT ID,MEM_TYPE,TOPIC,CONTENT,ENTRY_PATH,ENTRY_METHOD,META,"
                f"HIT_COUNT,REVIEW_STATUS,LAST_HIT_AT FROM {T_MEMORY}"):
            try:
                meta = json.loads(r[6]) if r[6] else {}
            except Exception:
                meta = {}
            rows.append({
                "id": r[0], "type": r[1], "topic": r[2] or "", "content": r[3],
                "entry_path": r[4] or "", "entry_method": r[5] or "", "meta": meta,
                "hits": r[7], "review": r[8] or "pending", "last": str(r[9])[:10],
            })
        return rows
    return _cached("memory", _load)


def _active(mem_type):
    """指定型別、排除 rejected（人工標記為錯誤的記憶不進檢索）。"""
    return [r for r in load_all() if r["type"] == mem_type and r["review"] != "rejected"]


def _latest_per_topic(rows):
    """append-only 版本化：同主題取 ID 最大（最新版）的一筆。"""
    best = {}
    for r in rows:
        k = r["topic"]
        if k not in best or r["id"] > best[k]["id"]:
            best[k] = r
    return list(best.values())


def get_anchors():
    """[(domain, triggers, entry_path, entry_methods, key_tables, skill)]（同名取最新版）"""
    rows = _latest_per_topic(_active("anchor"))
    return [(r["topic"], r["content"], r["entry_path"], r["entry_method"],
             r["meta"].get("key_tables", ""), r["meta"].get("skill", "")) for r in rows]


def get_synonyms():
    """[(term, maps_to)]（同詞取最新版）"""
    return [(r["topic"], r["content"]) for r in _latest_per_topic(_active("synonym"))]


def get_episodes():
    """[(id, question, entry_path, entry_method, index_sha, hit_count, last_hit_date, verified)]"""
    return [(r["id"], r["content"], r["entry_path"], r["entry_method"],
             r["meta"].get("index_sha", ""), r["hits"], r["last"],
             r["review"] == "verified")
            for r in _active("episode")]


def get_facts():
    """[(id, topic, fact, related_path, hit_count, last_hit_date, verified)]"""
    return [(r["id"], r["topic"], r["content"], r["entry_path"],
             r["hits"], r["last"], r["review"] == "verified")
            for r in _active("fact")]


def get_cases():
    """[(issue, description, file)] — git 挖掘的歷史前例（自動匯入，信心低於人工記憶）"""
    return [(r["topic"], r["content"], r["entry_path"]) for r in _active("case")]


# ── 寫入：單一入口（append-only）─────────────────────────

def add_memory(mem_type, topic, content, entry_path="", entry_method="", meta=None):
    """寫入一筆記憶。episode/fact 遇到完全相同的 內容+入口 時改累加命中數；
    anchor/synonym 一律插入新版本（讀取端取最新）。
    回傳 (memory_id_or_None, 是否為新記憶)。"""
    cur = _get_conn().cursor()
    if mem_type in ("episode", "fact"):
        row = cur.execute(
            f"SELECT ID FROM {T_MEMORY} WHERE MEM_TYPE=? AND CONTENT=? AND ISNULL(ENTRY_PATH,'')=?",
            (mem_type, content, entry_path or "")).fetchone()
        if row:
            cur.execute(
                f"UPDATE {T_MEMORY} SET HIT_COUNT=HIT_COUNT+1,LAST_HIT_AT=SYSDATETIME() WHERE ID=?",
                (row[0],))
            invalidate_cache()
            return row[0], False
    cur.execute(
        f"SET NOCOUNT ON; "
        f"INSERT INTO {T_MEMORY}(MEM_TYPE,TOPIC,CONTENT,ENTRY_PATH,ENTRY_METHOD,META,CREATED_BY)"
        f" VALUES (?,?,?,?,?,?,?); "
        f"SELECT CAST(SCOPE_IDENTITY() AS INT)",
        (mem_type, topic or None, content, entry_path or None, entry_method or None,
         json.dumps(meta, ensure_ascii=False) if meta else None, _user()))
    new_id = cur.fetchone()[0]
    invalidate_cache()
    return new_id, True


# ── 相容包裝：維持 codegraph_core 既有呼叫介面 ─────────────

def load_anchors_db():
    return get_anchors(), get_synonyms()


def load_episodes():
    return get_episodes()


def load_knowledge():
    return get_facts()


def load_cases():
    return get_cases()


def save_anchor(domain, triggers, entry_path, entry_methods="", key_tables="", note=""):
    """learn：插入 anchor 新版本（同名領域讀取端自動取最新）。回傳領域總數。"""
    add_memory("anchor", domain, triggers, entry_path, entry_methods,
               {"key_tables": key_tables, "note": note})
    return len(get_anchors())


def add_episode(question, entry_path, entry_method="", index_sha=""):
    return add_memory("episode", "", question, entry_path, entry_method,
                      {"index_sha": index_sha} if index_sha else None)


def add_knowledge(topic, fact, related_path=""):
    return add_memory("fact", topic, fact, related_path)
