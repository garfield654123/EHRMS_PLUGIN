"""remote-db 客戶端 agent：在客戶機器上執行，連到 relay，接收並執行 SELECT。

第一階段 MVP：明文 ws、無驗證，只供內網測試。
啟動：py agent.py --relay ws://內網機器IP:8770
"""

import argparse
import asyncio
import getpass
import json
import re
import secrets
import ssl
import string

import certifi
import pyodbc
import websockets

MAX_ROWS = 500
QUERY_TIMEOUT = 30  # 秒
# 一律以註解與字串之外的內容判斷，避免 SELECT 後面夾帶其他語法
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|grant|revoke|deny|"
    r"backup|restore|shutdown|dbcc|openrowset|opendatasource|openquery|bulk|into|waitfor|use)\b|\bxp_|\bsp_",
    re.I,
)


def strip_comments_and_strings(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"N?'(?:[^']|'')*'", "''", sql)


def check_select(sql):
    body = sql.strip().rstrip(";").strip()
    bare = strip_comments_and_strings(body).strip()
    if not re.match(r"^(select|with)\b", bare, re.I):
        raise ValueError("只允許 SELECT（或 WITH ... SELECT）語法")
    if ";" in bare:
        raise ValueError("只允許單一語法，不可用分號串接")
    hit = FORBIDDEN.search(bare)
    if hit:
        raise ValueError(f"語法含不允許的關鍵字：{hit.group(0)}")
    return body


def run_select(conn_str, sql):
    body = check_select(sql)
    conn = pyodbc.connect(conn_str, timeout=10)
    try:
        conn.timeout = QUERY_TIMEOUT
        cur = conn.cursor()
        cur.execute(body)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = [[None if v is None else str(v) for v in r] for r in rows[:MAX_ROWS]]
        return cols, rows, truncated
    finally:
        conn.close()


def detect_databases(server, user, password):
    """列出這台 SQL Server 上可存取的使用者資料庫，EHRMS 客戶庫（名稱為 8 碼統編、有 HRMS_COMPANY）排前面。"""
    conn = pyodbc.connect(build_conn_str(server, "master", user, password), timeout=10)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sys.databases WHERE database_id > 4 AND state = 0 "
            "AND HAS_DBACCESS(name) = 1 ORDER BY name")
        found = []
        for (name,) in cur.fetchall():
            item = {"name": name, "ehrms": False, "company": ""}
            db = "[" + name.replace("]", "]]") + "]"
            try:
                cur.execute(f"SELECT OBJECT_ID(N'{db.replace(chr(39), chr(39) * 2)}.dbo.HRMS_COMPANY')")
                if cur.fetchone()[0] is not None:
                    item["ehrms"] = True
                    cur.execute(f"SELECT TOP 1 COMPANY_CNAME FROM {db}.dbo.HRMS_COMPANY ORDER BY COMPANY_ID")
                    row = cur.fetchone()
                    item["company"] = row[0] if row and row[0] else ""
            except pyodbc.Error:
                pass  # 沒有權限讀取的庫照樣列出，只是不標 EHRMS
            found.append(item)
    finally:
        conn.close()
    # 統編_WF 是同一客戶的表單資料庫，登入主庫後可切換，不另列選項
    names = {d["name"].upper() for d in found}
    found = [d for d in found if not (re.fullmatch(r"\d{8}_WF", d["name"], re.I) and d["name"][:8] in names)]
    found.sort(key=lambda d: (not d["ehrms"], not re.fullmatch(r"\d{8}", d["name"]), d["name"]))
    return found


def read_customer_info(conn_str):
    """配對時交給 MCP 端核對的客戶資訊。客戶資料庫名稱通常為統編。"""
    conn = pyodbc.connect(conn_str, timeout=10)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DB_NAME(), @@SERVERNAME, HOST_NAME(), CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(50))")
        database, server, host, version = cur.fetchone()
        info = {"database": database, "server": server, "host": host, "version": version, "companies": []}
        try:
            cur.execute("SELECT COMPANY_CODE, COMPANY_CNAME, COMPANY_SNO, COMPANY_STATUS FROM HRMS_COMPANY ORDER BY COMPANY_ID")
            info["companies"] = [
                {"code": r[0], "name": r[1], "sno": r[2], "status": "關閉" if r[3] == "1" else "開啟"}
                for r in cur.fetchall()
            ]
        except pyodbc.Error as e:
            info["companies_error"] = f"讀取 HRMS_COMPANY 失敗：{e}"
        # 表單資料庫：同名加 _WF
        cur.execute("SELECT name FROM sys.databases WHERE name = DB_NAME() + '_WF' AND state = 0 AND HAS_DBACCESS(name) = 1")
        row = cur.fetchone()
        info["wf_database"] = row[0] if row else None
        return info
    finally:
        conn.close()


async def serve(relay, code, conn_str, info, log=print, on_ready=None, on_query=None):
    """log：訊息輸出（CLI 為 print，圖形介面另行顯示）；on_ready：第一次連上 relay 後呼叫；
    on_query：每收到一筆查詢呼叫（圖形介面用來重設閒置計時）。
    連上後若中途斷線，以同一配對碼自動重連；第一次就連不上則直接拋出例外。
    查詢只能指定主庫（db=main）或其表單資料庫（db=wf），不能切到其他客戶的資料庫。"""
    conns = {"main": conn_str}
    if info.get("wf_database"):
        conns["wf"] = with_database(conn_str, info["wf_database"])
    extra = {"ssl": tls_context()} if relay.startswith("wss://") else {}
    connected = False
    while True:
        try:
            # ping_timeout 放寬到 60 秒：經 zrok 的客戶網路延遲較大，預設 20 秒曾在實測中途斷線
            async with websockets.connect(relay, max_size=None, open_timeout=15,
                                          ping_interval=20, ping_timeout=60, **extra) as ws:
                await ws.send(json.dumps({"role": "agent", "code": code, "info": info}, ensure_ascii=False))
                if not connected:
                    connected = True
                    if on_ready:
                        on_ready()
                    log(f"配對碼：{code}　已連上 relay，等待查詢中…")
                else:
                    log("已重新連上 relay")
                async for msg in ws:
                    if on_query:
                        on_query()
                    await ws.send(json.dumps(await handle(json.loads(msg), conns, info, log), ensure_ascii=False))
                reason = f"relay 關閉連線（代碼 {ws.close_code}）"
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
            if not connected:
                raise
            reason = f"{type(e).__name__}：{e}"
        log(f"與 relay 的連線中斷（{reason}），5 秒後自動重連…")
        await asyncio.sleep(5)


async def handle(req, conns, info, log):
    db = req.get("db") or "main"
    log(f"收到查詢（{info['wf_database'] if db == 'wf' else info['database']}）：\n{req['sql']}")
    try:
        if db not in conns:
            raise ValueError("此客戶沒有表單資料庫（統編_WF）" if db == "wf" else f"不支援的資料庫：{db}")
        # 不用 asyncio.to_thread：客戶端 exe 以 Python 3.8 打包（支援舊版 Windows）
        cols, rows, truncated = await asyncio.get_running_loop().run_in_executor(
            None, run_select, conns[db], req["sql"])
        log(f"→ 回傳 {len(rows)} 筆" + ("（已截斷）" if truncated else ""))
        return {"id": req["id"], "columns": cols, "rows": rows, "truncated": truncated}
    except Exception as e:
        log(f"→ 錯誤：{e}")
        return {"id": req["id"], "error": str(e)}


def tls_context():
    # 客戶機器多為舊版 Windows，系統根憑證過舊會 CERTIFICATE_VERIFY_FAILED，另外載入內附的 certifi 根憑證
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(certifi.where())
    return ctx


def make_code():
    return "".join(secrets.choice(string.digits) for _ in range(6))


def build_conn_str(server, database, user, password):
    # {SQL Server} 為 Windows 內建驅動，客戶機器不需另外安裝，支援 MSSQL 2008
    conn_str = f"DRIVER={{SQL Server}};SERVER={server};DATABASE={database};"
    if not user:
        return conn_str + "Trusted_Connection=yes"
    # 以大括號包住，密碼含 ; 等特殊字元也能正確傳遞
    return conn_str + f"UID={user};PWD={{{password.replace('}', '}}')}}}"


def with_database(conn_str, database):
    return re.sub(r"DATABASE=[^;]*;", lambda m: f"DATABASE={database};", conn_str, count=1)


def prompt_conn_str():
    server = input("DB 主機：").strip()
    database = input("資料庫：").strip()
    user = input("帳號（留空使用 Windows 驗證）：").strip()
    password = getpass.getpass("密碼：") if user else ""
    return build_conn_str(server, database, user, password)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relay", default="ws://127.0.0.1:8770")
    args = parser.parse_args()

    conn_str = prompt_conn_str()
    info = read_customer_info(conn_str)
    print(f"資料庫連線成功：{info['database']}（{info['server']}）")
    for c in info["companies"]:
        print(f"  公司 {c['code']} {c['name']} 統編 {c['sno']}（{c['status']}）")
    if "companies_error" in info:
        print("  " + info["companies_error"])

    code = make_code()
    try:
        asyncio.run(serve(args.relay, code, conn_str, info))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
