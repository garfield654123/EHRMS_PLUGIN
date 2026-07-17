#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EHRMS codegraph MCP server（stdio）——純程式圖譜
① find_entry：從敘述找程式入口   ② trace：追呼叫鏈   ③ verify_call_path：反幻覺驗證
記憶功能由獨立的 ehrms-memory MCP 提供。
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
                "從自然語言敘述找 EHRMS 的程式入口（領域錨點路由）。當需要知道"
                "「某功能/某邏輯/某報表的程式在哪裡」時呼叫。"
                "回傳：候選領域、入口類別/函式、關鍵欄位、歷史前例(git)。"
                "只查程式碼地圖，不含團隊記憶——過往查案結論請用 ehrms-memory 的 recall。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "功能或問題敘述（中文）"},
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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    if name == "find_entry":
        return _text(cg.find_entry(arguments["description"], arguments.get("top_k", 3)))
    if name == "trace":
        return _text(cg.trace(arguments["entry"], arguments.get("cls_hint"), arguments.get("depth", 2)))
    if name == "verify_call_path":
        return _text(cg.verify_call_path(arguments["src_method"], arguments["dst"]))
    raise ValueError(f"Unknown tool: {name}")


async def main_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main_stdio())
