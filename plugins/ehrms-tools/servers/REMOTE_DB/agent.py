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
import socket
import ssl
import string
from urllib.parse import urlparse

import certifi
import pyodbc
import websockets

MAX_ROWS = 500
QUERY_TIMEOUT = 30  # 秒
# 是否只讀由 SQL Server 自己判斷：SET SHOWPLAN_XML ON 下送出語法只編譯不執行，回報批次中每個語句的 StatementType。
# 自行切字串判斷曾被字串／識別字內的 -- 與單引號、及不需分號的多語句批次繞過（見 SPEC.md）。
ALLOWED_STATEMENTS = {"SELECT", "SELECT WITHOUT QUERY"}
# SHOWPLAN 仍判為 SELECT、但不允許的寫法：讀取伺服器檔案或外部伺服器、鎖定資料表。
# 直接比對原始語法，出現在字串裡也擋（寧可誤擋）。
FORBIDDEN = re.compile(
    r"\b(openrowset|opendatasource|openquery|openxml|tablock|tablockx|xlock|updlock|holdlock|paglock|"
    r"serializable|repeatableread)\b",
    re.I,
)


def check_select(sql):
    """送到資料庫前的靜態檢查；只讀與否的主要判斷在 verify_plan。"""
    body = sql.strip()
    if not body:
        raise ValueError("沒有查詢語法")
    hit = FORBIDDEN.search(body)
    if hit:
        raise ValueError(f"不允許使用 {hit.group(0)}（外部讀取或鎖定資料表）")
    return body


def verify_plan(cur, sql, allowed_dbs):
    """以 SHOWPLAN_XML 編譯（不執行）整個批次：每個語句都必須是 SELECT，且只能用到允許的資料庫。"""
    cur.execute("SET SHOWPLAN_XML ON")
    try:
        try:
            cur.execute(sql)
            plans = []
            while True:
                plans += [r[0] for r in cur.fetchall()]
                if not cur.nextset():
                    break
        except pyodbc.Error as e:  # 語法錯誤、缺少 SHOWPLAN 權限等
            raise ValueError(f"無法分析此語法，不予執行：{e}")
    finally:
        cur.execute("SET SHOWPLAN_XML OFF")

    types = [t for xml in plans for t in re.findall(r'StatementType="([^"]*)"', xml)]
    if not types:
        raise ValueError("無法分析此語法，不予執行")
    bad = sorted({t for t in types if t not in ALLOWED_STATEMENTS})
    if bad:
        raise ValueError(f"只允許 SELECT，此語法含：{'、'.join(bad)}")
    allowed = {d.lower() for d in allowed_dbs}
    used = {d.strip("[]") for xml in plans for d in re.findall(r'Database="([^"]*)"', xml)}
    other = sorted(d for d in used if d.lower() not in allowed)
    if other:
        raise ValueError(f"只能查詢本客戶的資料庫，此語法用到：{'、'.join(other)}")


def run_select(conn_str, sql, allowed_dbs):
    body = check_select(sql)
    conn = pyodbc.connect(conn_str, timeout=10, autocommit=False)
    try:
        conn.timeout = QUERY_TIMEOUT
        cur = conn.cursor()
        verify_plan(cur, body, allowed_dbs)
        cur.execute(body)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = [[None if v is None else str(v) for v in r] for r in rows[:MAX_ROWS]]
        return cols, rows, truncated
    finally:
        try:
            conn.rollback()  # 保險：萬一有漏網的寫入也不留下
        finally:
            conn.close()


def detect_databases(server, user, password):
    """登入前偵測資料庫。統編_WF 是同一客戶的表單資料庫，登入主庫後可查，不另列選項。"""
    found = list_databases(build_conn_str(server, "master", user, password))
    names = {d["name"].upper() for d in found}
    return [d for d in found if not (re.fullmatch(r"\d{8}_WF", d["name"], re.I) and d["name"][:8] in names)]


def list_databases(conn_str):
    """列出這台 SQL Server 上可存取的使用者資料庫（排除系統庫），EHRMS 客戶庫（名稱為 8 碼統編、有 HRMS_COMPANY）排前面。"""
    conn = pyodbc.connect(with_database(conn_str, "master"), timeout=10)
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


async def open_relay(relay, fallback_ips, state, log):
    """連到 relay。客戶機器的 DNS 解析不到 relay 網址（getaddrinfo failed）時，改直接連 fallback_ips；
    TLS 驗證與 Host 標頭仍使用原網址，所以不必改客戶的 hosts 檔。連上的 IP 記在 state，重連時優先使用。"""
    # ping_timeout 放寬到 60 秒：經 zrok 的客戶網路延遲較大，預設 20 秒曾在實測中途斷線
    opts = dict(max_size=None, open_timeout=15, ping_interval=20, ping_timeout=60)
    url = urlparse(relay)
    if url.scheme == "wss":
        opts["ssl"] = tls_context()
    if not state.get("ip"):
        try:
            return await websockets.connect(relay, **opts)
        except socket.gaierror as e:
            if not fallback_ips:
                raise
            log(f"無法解析中繼伺服器網址（{e}），改用 IP 連線…")
    ips = [state["ip"]] if state.get("ip") else []
    ips += [ip for ip in fallback_ips if ip not in ips]
    if url.scheme == "wss":
        # 指定 host 時 websockets 不會自動帶 server_hostname，要明確給，否則會拿 IP 驗證憑證
        opts["server_hostname"] = url.hostname
    last = None
    for ip in ips:
        try:
            ws = await websockets.connect(relay, host=ip, port=url.port or (443 if url.scheme == "wss" else 80), **opts)
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
            last = e
            continue
        if state.get("ip") != ip:
            log(f"已改用 IP {ip} 連上中繼伺服器")
        state["ip"] = ip
        return ws
    raise last


def set_database(session, conn_str, info):
    """更新連線目前查詢的資料庫。conns 與 info 一起換，查詢開始時取得的一定是同一個資料庫的設定。"""
    conns = {"main": conn_str}
    if info.get("wf_database"):
        conns["wf"] = with_database(conn_str, info["wf_database"])
    session["target"] = (conn_str, conns, info)


def switch_database(session, name):
    """切換查詢的資料庫（在背景執行緒呼叫）：先確認新資料庫可連線，成功才切換，失敗維持原資料庫。
    只能切到 list_databases 列出的資料庫，系統庫不在其中。切換完要呼叫 reregister。"""
    conn_str = session["target"][0]
    if name not in {d["name"] for d in list_databases(conn_str)}:
        raise ValueError(f"無法切換到 {name}：不存在、未上線、沒有權限，或是系統資料庫")
    new_conn_str = with_database(conn_str, name)
    info = read_customer_info(new_conn_str)
    session["switching"] = True  # relay 更新前先擋查詢，避免工程師以為還在查舊資料庫
    set_database(session, new_conn_str, info)
    return info


async def reregister(session):
    """切換後讓 serve 重新登記：relay 只在連線時記客戶資訊，要重連才會更新。
    工程師端下一筆查詢會發現資料庫已改變，要求重新 connect 並確認客戶。"""
    session["reregister"] = True
    ws = session.get("ws")
    if ws:
        await ws.close()


async def serve(relay, code, conn_str, info, log=print, on_ready=None, on_query=None, fallback_ips=(), session=None):
    """log：訊息輸出（CLI 為 print，圖形介面另行顯示）；on_ready：第一次連上 relay 後呼叫；
    on_query：每收到一筆查詢呼叫（圖形介面用來重設閒置計時）；fallback_ips：DNS 解析失敗時改連的 IP；
    session：連線狀態，傳入後可用 switch_database、reregister 切換資料庫。
    連上後若中途斷線，以同一配對碼自動重連；第一次就連不上則直接拋出例外。
    查詢只能指定目前資料庫（db=main）或其表單資料庫（db=wf），不能跨到其他資料庫。"""
    session = {} if session is None else session
    set_database(session, conn_str, info)
    state = {}
    connected = False
    while True:
        try:
            ws = await open_relay(relay, fallback_ips, state, log)
            session["ws"] = ws
            try:
                await ws.send(json.dumps({"role": "agent", "code": code, "info": session["target"][2]}, ensure_ascii=False))
                session["switching"] = False
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
                    await ws.send(json.dumps(await handle(json.loads(msg), session, log), ensure_ascii=False))
                reason = f"relay 關閉連線（代碼 {ws.close_code}）"
            finally:
                session["ws"] = None
                await ws.close()
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
            if not connected:
                raise
            reason = f"{type(e).__name__}：{e}"
        if session.pop("reregister", False):
            continue
        log(f"與 relay 的連線中斷（{reason}），5 秒後自動重連…")
        await asyncio.sleep(5)


async def handle(req, session, log):
    _, conns, info = session["target"]
    if session.get("switching"):
        log("→ 拒絕：資料庫切換中")
        return {"id": req["id"], "error": "客戶端正在切換資料庫，請重新 connect 並確認客戶"}
    db = req.get("db") or "main"
    log(f"收到查詢（{info['wf_database'] if db == 'wf' else info['database']}）：\n{req['sql']}")
    try:
        if db not in conns:
            raise ValueError("此客戶沒有表單資料庫（統編_WF）" if db == "wf" else f"不支援的資料庫：{db}")
        # 不用 asyncio.to_thread：客戶端 exe 以 Python 3.8 打包（支援舊版 Windows）
        allowed_dbs = [d for d in (info.get("database"), info.get("wf_database")) if d]
        cols, rows, truncated = await asyncio.get_running_loop().run_in_executor(
            None, run_select, conns[db], req["sql"], allowed_dbs)
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
