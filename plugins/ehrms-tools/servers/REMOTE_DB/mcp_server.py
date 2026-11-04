"""ehrms-custom-mcp：讓 AI 經由 relay 對客戶資料庫執行 SELECT。

每次 query 都須在 Claude Code 由使用者核准（settings.json 的 permissions.ask）。
第一階段 MVP：明文 ws、無驗證，只供內網測試。
relay 位址由環境變數 REMOTE_DB_RELAY 指定，預設 ws://127.0.0.1:8770。
"""

import asyncio
import json
import os
import re
import uuid
from typing import Annotated

import websockets
from mcp.server.fastmcp import FastMCP
from pydantic import Field

RELAY = os.environ.get("REMOTE_DB_RELAY", "ws://127.0.0.1:8770")
RESULT_TIMEOUT = 60  # 秒

# 使用規則放在 MCP 本身，不需要搭配 skill。連上 MCP 時就會給 AI，每次對話都載入，所以只放整體流程；
# 語法限制放工具說明，錯誤處理放錯誤訊息。
INSTRUCTIONS = """\
用途：直接查詢客戶端 EHRMS 資料庫，取代「給 SQL、客戶端執行、貼回結果」。只能 SELECT，每筆 query 由工程師核准。

何時使用：使用者提供客戶端 EHRMS_Connect.exe 的 6 碼配對碼時。沒有配對碼就向使用者要，或改提供 SQL 請客戶端執行；不得猜測配對碼。

流程：
1. connect：把回傳的資料庫、表單資料庫、SQL Server、公司清單列給使用者，確認是正確客戶後才可 query。資料庫名稱不一定是統編（可能是備份庫），公司統編也可能與工單不同（公司可能合併過），由使用者判斷。
2. 欄位與關聯先用 DB MCP 的 get_table_columns、search_tables，或 recall、wiki_lookup 確認；這些只查結構與知識，不是客戶資料。
3. query：寫單一 SELECT，由工程師核准。使用者拒絕時不重送同一筆，詢問要如何調整。

查詢原則：
- 一律加篩選條件（員工、日期區間、單號），只選需要的欄位；同一問題能合併的查詢就合併，減少核准次數。
- 避免查個資欄位（身分證、生日、住址、帳號、薪資明細）；確有必要時在 reason 說明。

回覆原則：
- 只根據查到的資料下結論；資料不足就再查或說明缺什麼，不補推測值。
- 只引用必要的欄位與筆數，不整批轉貼個資。
- 寫回知識庫只寫已確認的原因與處理方式，不寫客戶的實際資料。

連線：網路短暫中斷時客戶端會以同一配對碼自動重連，不需重新 connect。
目前為測試階段：經 zrok 對外、沒有額外驗證，查詢紀錄尚未寫入資料庫。"""

RELAY_HINT = "請使用者在工程師電腦執行 servers\\REMOTE_DB\\start-relay.ps1 啟動 relay"
NO_AGENT_HINT = "客戶端可能已關閉、中斷，或 1 小時沒有查詢而自動中斷。請使用者在客戶端重新登入並提供新配對碼"
# 客戶端拒絕查詢的訊息開頭 -> 該怎麼處理
QUERY_HINTS = (
    ("只允許 SELECT", "拿掉所列的語句，改寫成純 SELECT"),
    ("只能查詢本客戶的資料庫", "移除跨庫查詢；_WF 資料改用 database=\"wf\"；要查其他資料庫，請使用者在客戶端畫面切換"),
    ("不允許使用", "移除外部讀取或鎖定 hint；這些字出現在字串裡也會被擋"),
    ("無法分析此語法", "常見原因是用了 2008 不支援的寫法，或帳號沒有 SHOWPLAN 權限"),
)

mcp = FastMCP("ehrms-custom-mcp", instructions=INSTRUCTIONS)
state = {"code": None, "info": None}


def relay_error(ack):
    """relay 回傳的錯誤加上處理方式。"""
    text = ack["error"]
    return f"{text}。{NO_AGENT_HINT}" if "找不到配對碼" in text else text


def query_error(text):
    hint = next((h for prefix, h in QUERY_HINTS if text.startswith(prefix)), None)
    return f"查詢失敗：{text}" + (f"\n處理方式：{hint}" if hint else "")


@mcp.tool()
async def connect(
    code: Annotated[str, Field(description="客戶端畫面上的 6 碼數字配對碼，由使用者提供，不得猜測")],
) -> str:
    """以配對碼連到客戶端，回傳客戶資訊。必須把客戶資訊列給使用者，確認是正確客戶後才可呼叫 query。"""
    code = re.sub(r"\s", "", code)
    if not re.fullmatch(r"\d{6}", code):
        return "配對碼是客戶端畫面上的 6 碼數字，請向使用者確認"
    try:
        async with websockets.connect(RELAY, max_size=None) as ws:
            await ws.send(json.dumps({"role": "client", "code": code, "probe": True}))
            ack = json.loads(await ws.recv())
    except (OSError, websockets.exceptions.WebSocketException) as e:
        return f"無法連到 relay（{RELAY}）：{e}。{RELAY_HINT}"
    if "error" in ack:
        return relay_error(ack)

    state["code"], state["info"] = code, ack.get("info", {})
    return format_customer(state["info"])


def format_customer(info):
    database = info.get("database") or ""
    companies = info.get("companies", [])
    lines = [
        "已連上客戶端，請向使用者確認是否為正確客戶：",
        f"- 資料庫：{database}" + ("" if re.fullmatch(r"\d{8}", database) else "（不是 8 碼統編格式，請特別確認）"),
        f"- SQL Server：{info.get('server')}，版本 {info.get('version')}",
        f"- 主機：{info.get('host')}",
        f"- 表單資料庫：{info['wf_database']}（query 帶 database=\"wf\" 查詢）" if info.get("wf_database") else "- 表單資料庫：無",
    ]
    if companies:
        lines.append("- 公司（HRMS_COMPANY）：")
        lines += [f"  - {c['code']} {c['name']}，統編 {c['sno']}（{c['status']}）" for c in companies]
    if "companies_error" in info:
        lines.append(f"- {info['companies_error']}")
    return "\n".join(lines)


@mcp.tool()
async def query(
    sql: Annotated[str, Field(description="單一 SELECT，可用 CTE、UNION、子查詢。一律加篩選條件，只選需要的欄位")],
    reason: Annotated[str, Field(description="具體寫要確認什麼，有 Jira 單號就帶上，例如「EHRMSONE-32543：確認員工 A001 在 10/1 的打卡原始資料」。工程師核准時會看到")],
    database: Annotated[str, Field(description='"main"：客戶端目前選的資料庫（通常是統編）。"wf"：它的表單資料庫（統編_WF，簽核表單相關）；connect 顯示「表單資料庫：無」時不可用')] = "main",
) -> str:
    """在已確認的客戶資料庫執行 SELECT，最多回傳 500 筆；被截斷時加條件重查。
    客戶端以 SHOWPLAN 編譯檢查，下列情況整批拒絕：非 SELECT 語句（DECLARE、SET、EXEC、交易控制、IF 等）、SELECT INTO、
    跨到目前資料庫與其 _WF 以外的資料庫（含 master、sys.databases）、OPENROWSET 類外部讀取、TABLOCKX／HOLDLOCK 等鎖定 hint（NOLOCK 可用）。
    多個 SELECT 只會回傳第一個結果。
    客戶端多為 MSSQL 2008：不可用 FOR JSON、STRING_AGG、TRY_CONVERT、IIF、FORMAT、OFFSET/FETCH。"""
    if not state["code"]:
        return "尚未連線，請先向使用者取得配對碼並呼叫 connect"
    database = database.strip().lower() or "main"
    if database not in ("main", "wf"):
        return 'database 只能是 "main"（目前資料庫）或 "wf"（表單資料庫）'
    if database == "wf" and not state["info"].get("wf_database"):
        return "此資料庫沒有表單資料庫（統編_WF）"
    target = state["info"]["wf_database"] if database == "wf" else state["info"].get("database")
    try:
        async with websockets.connect(RELAY, max_size=None) as ws:
            await ws.send(json.dumps({"role": "client", "code": state["code"]}))
            ack = json.loads(await ws.recv())
            if "error" in ack:
                return relay_error(ack)
            if ack.get("info", {}).get("database") != state["info"].get("database"):
                return "配對碼對應的資料庫已改變，請重新 connect 並與使用者確認客戶"
            await ws.send(json.dumps({"id": str(uuid.uuid4()), "sql": sql, "db": database}, ensure_ascii=False))
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=RESULT_TIMEOUT))
    except asyncio.TimeoutError:
        return f"等待客戶端回應逾時（{RESULT_TIMEOUT} 秒）。可能是查詢太重或客戶端斷線，請縮小範圍後重試"
    except websockets.exceptions.ConnectionClosed:
        return "與客戶端的連線中斷，查詢結果未知。請重新 connect 後再試"
    except (OSError, websockets.exceptions.WebSocketException) as e:
        return f"無法連到 relay（{RELAY}）：{e}。{RELAY_HINT}"

    if "error" in data:
        return query_error(data["error"])
    return f"客戶資料庫：{target}\n" + format_table(data["columns"], data["rows"], data.get("truncated"))


def format_table(cols, rows, truncated):
    def cell(v):
        return "NULL" if v is None else str(v).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    head = f"共 {len(rows)} 筆" + ("（超過上限已截斷，請加條件縮小範圍）" if truncated else "")
    return head + "\n\n" + "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
