#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EHRMS memory MCP server（stdio）——團隊共用記憶＋Jira 結案紀錄
① recall / remember：跨單可泛化知識（HRMS_MEMORY）
② jira_lookup / jira_log：單一案件結案紀錄（HRMS_JIRA，一單一筆有效紀錄）
"""
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

import memory_core as mc
import jira_core as jc

server = Server("ehrms-memory")


def _text(s):
    return [{"type": "text", "text": s}]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="recall",
            description=(
                "檢索團隊共用記憶。查案/排查流程的**第一步**呼叫。"
                "回傳兩組：System（系統使用知識，客服視角——操作順序、前置條件、功能行為）"
                "與 Engineer（程式入口與邏輯要點，維運視角）。命中會自動累計引用次數。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "使用者的問題或功能敘述（中文）"},
                    "kind": {"type": "string", "enum": ["System", "Engineer"],
                             "description": "只查某一型（可選；預設兩型都查）"},
                    "top_k": {"type": "integer", "description": "每型回傳筆數，預設 3"},
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="remember",
            description=(
                "寫入一筆團隊共用記憶（唯一寫入口，內建去重）。在 skill 流程的沉澱步驟呼叫——"
                "結論確認後、輸出報告前。kind 二選一：\n"
                "- System：系統使用層知識（如『員工資料建立後需先建薪資結構才能算薪』）\n"
                "- Engineer：功能→程式入口對應＋邏輯要點（entry_path 必填）\n"
                "訂正先前的錯誤結論時帶 supersedes=舊ID（新筆取代舊筆，一次完成）。"
                "keywords 請填 3~8 個使用者實際會拿來問的詞，"
                "特別是高鑑別度英數詞（SP 名、代碼如 SLC01、資料表名）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["System", "Engineer"],
                             "description": "System=系統知識（客服）/ Engineer=程式入口（維運）"},
                    "topic": {"type": "string", "description": "主題（聚合用的短標題），如『災防假加班時數計算』"},
                    "content": {"type": "string", "description": "記憶內容：System=知識敘述；Engineer=入口說明＋邏輯要點"},
                    "keywords": {"type": "string", "description": "關鍵字，逗號分隔，3~8 個（強烈建議填）"},
                    "func_path": {"type": "string", "description": "UI 功能位置，如『出勤管理→假勤管理→報表』（可選）"},
                    "entry_path": {"type": "string",
                                   "description": "程式入口的 repo 相對路徑（Engineer 必填），如 VB/EHRMS/xx.cls；客製 repo 保留前綴如 CUSTOM_GIT/23019591_Skhb/…；勿用本機絕對路徑"},
                    "entry_method": {"type": "string", "description": "入口函式（可選）"},
                    "ref_key": {"type": "string", "description": "來源 Jira 單號，如 EHRMSONE-32543（可選）"},
                    "source": {"type": "string", "description": "產生途徑：skill 名稱或 'curate'（可選）"},
                    "supersedes": {"type": "string", "description": "要取代的舊記憶 ID（訂正/歸納用；逗號分隔可多筆）"},
                    "force": {"type": "boolean", "description": "跳過去重檢查（確認為新議題時用）"},
                },
                "required": ["kind", "topic", "content"],
            },
        ),
        Tool(
            name="jira_lookup",
            description=(
                "查 HRMS_JIRA 結案紀錄（每張 Jira 單的根因/解法/修改程式）。"
                "query 給單號（EHRMSONE-XXXXX 或純數字）→ 精確查該單；"
                "給問題敘述 → 相似案件檢索（回 top 5，分兩層：✅已審核＝根因經"
                "人工確認、優先參考；⏳未審核＝僅供參考，引用時須自行驗證）。"
                "查案開頭與 recall 並行呼叫；jira_log 寫入前也先用它查重。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Jira 單號或問題敘述（中文）"},
                    "kind": {"type": "string", "enum": ["Crisis", "Story"],
                             "description": "敘述檢索時只查某一型（可選）"},
                    "top_k": {"type": "integer", "description": "敘述檢索回傳筆數，預設 5"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="jira_log",
            description=(
                "寫入 Jira 單結案紀錄到 HRMS_JIRA（一單一筆有效紀錄，唯一寫入口）。"
                "在 skill 流程的報告輸出前呼叫：摘要根因、解決方式、修改程式後寫入。\n"
                "kind 二選一：Crisis=維運單 / Story=改程式與 bug fix（changed_files 必填）。\n"
                "同單已有紀錄會擋下並回傳既有內容；本次是修正結論時帶 supersedes=舊ID"
                "（新筆取代舊筆）。跨單可泛化的知識請另用 remember，兩者互補。\n"
                "新紀錄一律為 ⏳未審核；人工審核（verified/rejected）由 DB 端維護，"
                "MCP 無權寫入審核欄。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "jira_key": {"type": "string",
                                 "description": "Jira 單號（EHRMSONE-32543 或純數字）"},
                    "kind": {"type": "string", "enum": ["Crisis", "Story"],
                             "description": "Crisis=維運單 / Story=改程式與 bug fix"},
                    "title": {"type": "string", "description": "一句話案件摘要（檢索錨）"},
                    "root_cause": {"type": "string", "description": "發生根因"},
                    "resolution": {"type": "string", "description": "解決方式"},
                    "changed_files": {"type": "string",
                                      "description": "修改程式：一行一筆「repo 相對路徑 :: 函式」，如 VB/EHRMS/xx.cls :: CalcOT；客製 repo 保留前綴如 CUSTOM_GIT/23019591_Skhb/…（Story 必填；勿用本機絕對路徑）"},
                    "keywords": {"type": "string",
                                 "description": "關鍵字，逗號分隔 3~8 個（強烈建議填）"},
                    "supersedes": {"type": "string",
                                   "description": "要取代的舊紀錄 ID（修正結論用；僅限同單號）"},
                },
                "required": ["jira_key", "kind", "title", "root_cause", "resolution"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    if name == "recall":
        return _text(mc.recall(
            arguments["question"], arguments.get("kind"), arguments.get("top_k", 3)))
    if name == "remember":
        return _text(mc.remember(
            arguments["kind"], arguments["topic"], arguments["content"],
            keywords=arguments.get("keywords", ""),
            func_path=arguments.get("func_path", ""),
            entry_path=arguments.get("entry_path", ""),
            entry_method=arguments.get("entry_method", ""),
            ref_key=arguments.get("ref_key", ""),
            source=arguments.get("source", ""),
            supersedes=arguments.get("supersedes", ""),
            force=bool(arguments.get("force", False))))
    if name == "jira_lookup":
        return _text(jc.lookup(
            arguments["query"], arguments.get("kind"), arguments.get("top_k", 5)))
    if name == "jira_log":
        return _text(jc.log_case(
            arguments["jira_key"], arguments["kind"], arguments["title"],
            arguments["root_cause"], arguments["resolution"],
            changed_files=arguments.get("changed_files", ""),
            keywords=arguments.get("keywords", ""),
            supersedes=arguments.get("supersedes", "")))
    raise ValueError(f"Unknown tool: {name}")


async def main_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main_stdio())
