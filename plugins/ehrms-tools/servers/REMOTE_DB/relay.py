"""remote-db 中繼伺服器：依配對碼把 MCP 端的查詢轉送給客戶端 agent。

第一階段 MVP：明文 ws、無驗證，只供內網測試。
啟動：py relay.py [--host 0.0.0.0] [--port 8770]
"""

import argparse
import asyncio
import json

import websockets

agents = {}   # 配對碼 -> (agent 連線, 客戶資訊)
pending = {}  # 查詢 id -> MCP 端連線


async def handler(ws):
    hello = json.loads(await ws.recv())
    code = str(hello.get("code", "")).upper()

    if hello.get("role") == "agent":
        info = hello.get("info", {})
        agents[code] = (ws, info)
        print(f"agent 上線：{code}（{info.get('database')}）")
        try:
            async for msg in ws:  # agent 回傳的查詢結果
                client = pending.pop(json.loads(msg).get("id"), None)
                if client:
                    await client.send(msg)
        finally:
            if agents.get(code, (None,))[0] is ws:
                del agents[code]
            print(f"agent 離線：{code}")
        return

    # MCP 端：probe 只取客戶資訊；否則每次查詢建立一次連線
    if code not in agents:
        await ws.send(json.dumps({"error": f"找不到配對碼 {code} 的客戶端，請確認客戶端程式仍在執行"}, ensure_ascii=False))
        return
    agent, info = agents[code]
    await ws.send(json.dumps({"ok": True, "info": info}, ensure_ascii=False))
    if hello.get("probe"):
        return
    req = json.loads(await ws.recv())
    pending[req["id"]] = ws
    print(f"轉送查詢 {req['id']} → {code}")
    try:
        await agent.send(json.dumps(req, ensure_ascii=False))
        await ws.wait_closed()
    finally:
        pending.pop(req["id"], None)


async def main(host, port):
    # ping_timeout 與 agent 一致放寬到 60 秒，避免經 zrok 的延遲誤判斷線
    async with websockets.serve(handler, host, port, max_size=None, ping_interval=20, ping_timeout=60):
        print(f"relay 啟動於 {host}:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        pass
