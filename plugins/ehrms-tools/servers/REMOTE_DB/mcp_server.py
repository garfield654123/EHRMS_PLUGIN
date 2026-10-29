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

import websockets
from mcp.server.fastmcp import FastMCP

RELAY = os.environ.get("REMOTE_DB_RELAY", "ws://127.0.0.1:8770")
RESULT_TIMEOUT = 60  # 秒

mcp = FastMCP("ehrms-custom-mcp")
state = {"code": None, "info": None}


@mcp.tool()
async def connect(code: str, expected_sno: str = "") -> str:
    """以配對碼（客戶端 agent 畫面上的 6 碼）連到客戶端，回傳客戶資訊供核對。
    客戶資料庫名稱通常為客戶統編。expected_sno：預期的客戶統編（例如 Jira 單上的客戶），有給就自動比對。
    連上後必須把客戶資訊列給使用者，確認是正確客戶後才可呼叫 query。"""
    code = code.strip().upper()
    try:
        async with websockets.connect(RELAY, max_size=None) as ws:
            await ws.send(json.dumps({"role": "client", "code": code, "probe": True}))
            ack = json.loads(await ws.recv())
    except OSError as e:
        return f"無法連到 relay（{RELAY}）：{e}"
    if "error" in ack:
        return ack["error"]

    state["code"], state["info"] = code, ack.get("info", {})
    return format_customer(state["info"], expected_sno.strip())


def format_customer(info, expected_sno):
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
    if expected_sno:
        snos = {database} | {c.get("sno") for c in companies}
        if expected_sno in snos:
            lines.append(f"\n✅ 預期統編 {expected_sno} 與此客戶相符")
        else:
            lines.append(f"\n⚠ 預期統編 {expected_sno} 與資料庫名稱及公司統編都不符，可能連錯客戶，請先與使用者確認")
    return "\n".join(lines)


@mcp.tool()
async def query(sql: str, reason: str, database: str = "main") -> str:
    """在已確認的客戶資料庫執行單一 SELECT，最多回傳 500 筆。
    客戶端多為 MSSQL 2008：不可用 FOR JSON、STRING_AGG、TRY_CONVERT、OFFSET/FETCH 等新語法。
    reason：說明這次查詢要確認什麼，供使用者核准時判斷。
    database："main" 為客戶主資料庫（統編）；"wf" 為表單資料庫（統編_WF，簽核表單相關資料）。"""
    if not state["code"]:
        return "尚未連線，請先向使用者取得配對碼並呼叫 connect"
    database = database.strip().lower() or "main"
    if database not in ("main", "wf"):
        return 'database 只能是 "main"（主資料庫）或 "wf"（表單資料庫）'
    if database == "wf" and not state["info"].get("wf_database"):
        return "此客戶沒有表單資料庫（統編_WF）"
    target = state["info"]["wf_database"] if database == "wf" else state["info"].get("database")
    try:
        async with websockets.connect(RELAY, max_size=None) as ws:
            await ws.send(json.dumps({"role": "client", "code": state["code"]}))
            ack = json.loads(await ws.recv())
            if "error" in ack:
                return ack["error"]
            if ack.get("info", {}).get("database") != state["info"].get("database"):
                return "配對碼對應的資料庫已改變，請重新 connect 並與使用者確認客戶"
            await ws.send(json.dumps({"id": str(uuid.uuid4()), "sql": sql, "db": database}, ensure_ascii=False))
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=RESULT_TIMEOUT))
    except asyncio.TimeoutError:
        return f"等待客戶端回應逾時（{RESULT_TIMEOUT} 秒）"
    except OSError as e:
        return f"無法連到 relay（{RELAY}）：{e}"

    if "error" in data:
        return f"查詢失敗：{data['error']}"
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
