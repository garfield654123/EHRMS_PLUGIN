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
            name="learn",
            description=(
                "把一次查對的『領域→入口』沉澱成領域錨點（回饋迴路，讓 find_entry 越用越準）。"
                "當 find_entry 未命中、但你已用其他方式確認了某敘述對應的正確程式入口時，呼叫這個記起來。"
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
