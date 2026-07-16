#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EHRMS codegraph MCP server（stdio）
① find_entry：從敘述找程式入口   ② trace：追呼叫鏈   ③ verify_call_path：反幻覺驗證
"""
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

import codegraph_core as cg

server = Server("ehrms-codegraph")


def _text(s):
    return [{"type": "text", "text": s}]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="find_entry",
            description=(
                "從自然語言敘述找到 EHRMS 的程式入口。當使用者用中文問「某功能在哪、"
                "某邏輯怎麼算、為什麼會發生某現象、某通知/計算/設定/報表的程式在哪裡」時，"
                "**先呼叫這個**。回傳：領域、入口類別/函式、關鍵欄位、對應 skill、歷史前例(git)。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "使用者的問題或功能敘述（中文）"},
                    "top_k": {"type": "integer", "description": "回傳候選領域數，預設 3"},
                },
                "required": ["description"],
            },
        ),
        Tool(
            name="trace",
            description=(
                "從一個函式沿呼叫鏈追蹤（函式→函式、函式→SP）。用 find_entry 找到入口函式後，"
                "用這個看它呼叫誰、被誰呼叫，建立程式鏈以定位邏輯。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entry": {"type": "string", "description": "起始函式名稱"},
                    "cls_hint": {"type": "string", "description": "類別檔名關鍵字（可選，多個同名函式時鎖定用）"},
                    "depth": {"type": "integer", "description": "往下追幾層，預設 2"},
                },
                "required": ["entry"],
            },
        ),
        Tool(
            name="verify_call_path",
            description=(
                "驗證某條呼叫是否真的存在（反幻覺閘門）。回 verified 或 not_found，"
                "**永不回『一定沒有』**。當你要斷言『A 呼叫 B』或『某鏈碰到某 SP』時，先用這個確認再輸出。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "src_method": {"type": "string", "description": "來源函式名稱"},
                    "dst": {"type": "string", "description": "目標函式或 SP 名稱"},
                },
                "required": ["src_method", "dst"],
            },
        ),
        Tool(
            name="remember",
            description=(
                "把這次「使用者的問題 → 最後確認的程式入口」記進共用記憶（情節記憶）。"
                "當你用 find_entry/trace/讀碼確認了某個問題的實際入口後，**呼叫這個記下來**，"
                "下次任何人問類似問題，find_entry 會直接命中、不用重掃。"
                "比 learn 輕量：不用歸納領域與觸發詞，照實記錄即可。"
                "若 find_entry 未命中時回傳了 pending_id，帶入 pending_id 即可省略 question（一行完成沉澱）。"
                "★ 若使用者訂正了你先前的結論，務必以**訂正後的版本**呼叫；"
                "若本次對話先前已記入錯誤版本，需在回覆中明確告知使用者該筆記憶 ID"
                "（寫入成功時會回傳），供日後驗證工具標記 rejected。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "使用者的問題原文（中文，照實記錄；有 pending_id 時可省略）"},
                    "entry_path": {"type": "string", "description": "確認的入口類別/檔案路徑"},
                    "entry_method": {"type": "string", "description": "確認的入口函式（可選）"},
                    "pending_id": {"type": "integer", "description": "find_entry 未命中時回傳的待沉澱記錄 ID（帶入即免填 question）"},
                },
                "required": ["entry_path"],
            },
        ),
        Tool(
            name="remember_fact",
            description=(
                "把確認過的『系統性知識片段』記進共用記憶（語意記憶）。適合記錄"
                "不一定對應到程式入口的系統行為，例如『通知信主要由排程程式每日定期寄發』、"
                "『刷卡比對可手動執行也可自動排程』、『某功能只在月結時觸發』。"
                "當你在追碼或與使用者對話中**確認了**這類系統運作事實，呼叫這個記下來，"
                "下次任何人問到相關主題，find_entry 會一併帶出。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "主題，如『通知寄送』『刷卡比對』"},
                    "fact": {"type": "string", "description": "知識內容（一段完整的事實敘述，中文）"},
                    "related_path": {"type": "string", "description": "相關程式/排程路徑（可選）"},
                },
                "required": ["topic", "fact"],
            },
        ),
        Tool(
            name="learn",
            description=(
                "把一次查對的『領域→入口』沉澱成領域錨點（寫入共用記憶 DB，全團隊生效；回饋迴路，讓 find_entry 越用越準）。"
                "當 find_entry 未命中、但你已用其他方式確認了某敘述對應的正確程式入口時，呼叫這個記起來。"
                "單次問題的記錄請改用較輕量的 remember；learn 用於歸納出一整個領域時。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "領域名稱，如『權限管理』"},
                    "triggers": {"type": "string", "description": "觸發詞，逗號分隔，如『權限,角色,授權』"},
                    "entry_path": {"type": "string", "description": "主入口類別/檔案路徑"},
                    "entry_methods": {"type": "string", "description": "關鍵入口函式（可選）"},
                    "key_tables": {"type": "string", "description": "關鍵資料表/欄位（可選）"},
                    "note": {"type": "string", "description": "備註（可選）"},
                },
                "required": ["domain", "triggers", "entry_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    if name == "find_entry":
        return _text(cg.find_entry(arguments["description"], arguments.get("top_k", 3)))
    if name == "trace":
        return _text(cg.trace(arguments["entry"], arguments.get("cls_hint"), arguments.get("depth", 2)))
    if name == "verify_call_path":
        return _text(cg.verify_call_path(arguments["src_method"], arguments["dst"]))
    if name == "remember":
        return _text(cg.remember(
            arguments.get("question", ""), arguments["entry_path"],
            arguments.get("entry_method", ""), arguments.get("pending_id")))
    if name == "remember_fact":
        return _text(cg.remember_fact(
            arguments["topic"], arguments["fact"], arguments.get("related_path", "")))
    if name == "learn":
        return _text(cg.learn(
            arguments["domain"], arguments["triggers"], arguments["entry_path"],
            arguments.get("entry_methods", ""), arguments.get("key_tables", ""),
            arguments.get("note", "")))
    raise ValueError(f"Unknown tool: {name}")


async def main_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main_stdio())
