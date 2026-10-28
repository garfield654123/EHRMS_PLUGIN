#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EHRMS memory MCP server（stdio）——團隊共用記憶＋Jira 結案紀錄＋人工維護參考資料
① recall / remember：跨單可泛化知識（HRMS_MEMORY）
② jira_lookup / jira_log：單一案件結案紀錄（HRMS_JIRA，一單一筆有效紀錄）
③ faq_lookup / bulletin_lookup / task_lookup / sop_lookup：人工維護為主的參考資料
   （HRMS_FAQ／HRMS_FAQ_BULLETIN／HRMS_TASK／HRMS_FAQ_SOP，直接查原表）
   version_bulletins：整批列出版更公告（不做相似度篩選），比對已知版更問題用
   faq_log / task_log / sop_log：AI 寫入口，寫入者記為「使用者(AI)」，不可改人工資料
④ wiki_lookup：系統原則與大規則（HRMS_WIKI，由 eHRMS-Wiki 發佈，純查詢）
伺服器說明（instructions）載入 plugin 根目錄的 context.md，作為常駐的知識庫使用說明。
"""
import asyncio
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

import memory_core as mc
import jira_core as jc
import refdata_core as rc
import refdata_write as rw
import wiki_core as wc
import custom_sa_core as sc
import custom_core as cc

_CONTEXT_FILE = Path(__file__).resolve().parents[2] / "context.md"


def _load_instructions():
    """讀 context.md 當伺服器說明；檔案不存在或讀取失敗時不影響工具運作。"""
    try:
        return _CONTEXT_FILE.read_text(encoding="utf-8")
    except OSError:
        return None


server = Server("ehrms-memory", instructions=_load_instructions())


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
        Tool(
            name="faq_lookup",
            description=(
                "查 HRMS_FAQ（人工維護的常見問題集，非 AI 產生，精度較高）。"
                "查維運單初期與 recall／jira_lookup 並行呼叫：若問題已有現成 FAQ 解答，"
                "可直接引用加速結案；未命中則繼續原本追碼流程。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "問題敘述（中文）"},
                    "category": {"type": "string", "description": "限定分類（可選）"},
                    "top_k": {"type": "integer", "description": "回傳筆數，預設 5"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="bulletin_lookup",
            description=(
                "查 HRMS_FAQ_BULLETIN（人工維護的公告：已知問題／版本異動等，非 AI 產生）。"
                "查維運單初期與 recall／jira_lookup／faq_lookup 並行呼叫，"
                "僅包含未刪除且顯示中（IS_DELETE=0、SHOW=1）的公告。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "問題敘述（中文）"},
                    "category": {"type": "string", "description": "限定分類（可選）"},
                    "top_k": {"type": "integer", "description": "回傳筆數，預設 5"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="version_bulletins",
            description=(
                "整批列出 HRMS_FAQ_BULLETIN 裡所有有效的版更公告（類型：版本更新／version update／規格說明），"
                "含已知版更問題、更新程式連結、修正版號、適用版本或客戶名單。不做相似度篩選，"
                "用來比對使用者回報的錯誤／異常是不是已知版更問題；資料調整需求或單純操作詢問不需要呼叫。"
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="task_lookup",
            description=(
                "查 HRMS_TASK（人工整理的 FAE 修改資料罐頭語法：SQL 範本＋參數＋風險等級＋注意事項）。"
                "僅在診斷結論為『需要修正客戶資料』時才呼叫。回傳的 SQL 範本僅供參考，"
                "仍須人工確認參數與實際影響範圍後才可執行——AI 不可自動執行此 SQL。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "需要修正資料的情境敘述（中文）"},
                    "top_k": {"type": "integer", "description": "回傳筆數，預設 5"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="sop_lookup",
            description=(
                "查 HRMS_FAQ_SOP（客戶需求處理 SOP，人工維護）。一種客戶需求一筆："
                "前置確認（例：解鎖假勤前先確認是否已計薪）、處理方式（操作路徑或套用哪個 Task）、"
                "需要客戶補的資料、處理後的提醒。客戶提出資料調整或操作需求、"
                "或疑似異常但資訊不足時呼叫。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "客戶需求或問題敘述（中文）"},
                    "top_k": {"type": "integer", "description": "回傳筆數，預設 3"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="faq_log",
            description=(
                "新增或更新一筆 HRMS_FAQ（AI 撰寫，回答者記為「使用者(AI)」）。"
                "結論已確認、可直接回覆客戶，且 faq_lookup 查無相同問題時呼叫。"
                "answer 寫給客戶看的回覆（不放程式路徑、SQL）。"
                "同表高相似會擋下並回傳既有 ID；人工 FAQ 不可修改。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "客戶的問題（用客戶會問的說法）"},
                    "answer": {"type": "string", "description": "回覆內容（客戶看得懂的操作與說明）"},
                    "module": {"type": "string", "description": "模組，須為既有值（如 M1、M2、WF）"},
                    "sub_category": {"type": "string", "description": "子分類（可選，50 字內）"},
                    "refs": {"type": "string", "description": "依據：Jira 單號、記憶 #、Wiki 頁 Rn、FAQ／Task ID 等（必填，會附在內容尾端）"},
                    "id": {"type": "integer",
                           "description": "要更新的既有 ID（只限 AI 寫的筆；未給則新增）"},
                },
                "required": ["question", "answer", "module", "refs"],
            },
        ),
        Tool(
            name="task_log",
            description=(
                "新增或更新一筆 HRMS_TASK 罐頭語法（AI 撰寫，作者記為「使用者(AI)」）。"
                "只在實際案件已用此語法修正資料並確認無誤後呼叫；refs 須含已寫入 HRMS_JIRA 的單號。"
                "syntax 會檢查：SQL Server 2008 相容、UPDATE／DELETE 必有 WHERE、"
                "異動須包 BEGIN TRAN 且 COMMIT 註解掉。人工 Task 不可修改。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_name": {"type": "string", "description": "語法名稱（情境＋動作，如『解鎖已關帳假單』）"},
                    "task_description": {"type": "string", "description": "適用情境與前置確認"},
                    "syntax": {"type": "string", "description": "SQL 範本；參數處加註解說明要替換什麼"},
                    "notes": {"type": "string", "description": "執行前確認事項與影響範圍"},
                    "module": {"type": "string", "description": "模組，須為既有值"},
                    "category": {"type": "string", "description": "分類，須為既有值（如 資料修改、資料刪除、資料查詢）"},
                    "related_faq_id": {"type": "integer", "description": "相關 FAQ ID（可選）"},
                    "refs": {"type": "string", "description": "依據：Jira 單號、記憶 #、Wiki 頁 Rn、FAQ／Task ID 等（必填，會附在內容尾端）；須含 HRMS_JIRA 已有的單號"},
                    "id": {"type": "integer",
                           "description": "要更新的既有 ID（只限 AI 寫的筆；未給則新增）"},
                },
                "required": ["task_name", "task_description", "syntax", "notes", "module", "category", "refs"],
            },
        ),
        Tool(
            name="sop_log",
            description=(
                "新增或更新一筆 HRMS_FAQ_SOP 客戶需求處理 SOP（AI 撰寫，作者記為「使用者(AI)」）。"
                "同一種客戶需求處理過、步驟已確認，且 sop_lookup 查無時呼叫。"
                "content 依模板：# 標題、## 適用情境，再至少一段 ## 前置確認／## 處理方式／"
                "## 需要客戶補的資料（可加 ## 處理後、## 相關）。人工 SOP 不可修改。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "客戶需求（如『客戶要求解鎖假勤』）"},
                    "content": {"type": "string", "description": "SOP 內文（Markdown，依模板）"},
                    "module": {"type": "string", "description": "模組，須為既有值（如 M1、COMMON）"},
                    "keywords": {"type": "string", "description": "關鍵字，逗號分隔（可選）"},
                    "description": {"type": "string", "description": "一句話摘要（可選）"},
                    "refs": {"type": "string", "description": "依據：Jira 單號、記憶 #、Wiki 頁 Rn、FAQ／Task ID 等（必填，會附在內容尾端）"},
                    "id": {"type": "integer",
                           "description": "要更新的既有 ID（只限 AI 寫的筆；未給則新增）"},
                },
                "required": ["title", "content", "module", "refs"],
            },
        ),
        Tool(
            name="wiki_lookup",
            description=(
                "查 EHRMS Wiki（HRMS_WIKI：系統原則、大規則、選單位置、程式所在資料夾、關鍵資料表與設定）。"
                "兩種用法：給 query 搜尋，回傳頁面摘要與命中的規則（Rn）；"
                "給 page（主題代碼、路徑或標題）加 section（規則編號如 R7、章節名如 程式地圖、或 上半／下半／全文）讀原文。"
                "規則引用的記憶若已被取代，會標示新編號。未給 customer_code 時不回傳客製頁。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "問題敘述（搜尋模式）"},
                    "page": {"type": "string", "description": "主題代碼、GIT_PATH 或頁面標題（章節模式）"},
                    "section": {"type": "string",
                                "description": "規則編號（R7）、章節名、上半／下半／全文；未給則回傳該頁目錄"},
                    "customer_code": {"type": "string", "description": "客戶統編（要看客製頁時才給）"},
                    "top_k": {"type": "integer", "description": "搜尋模式回傳頁數，預設 3"},
                },
            },
        ),
        Tool(
            name="custom_sa_log",
            description=(
                "寫入/更新一筆 HRMS_CUSTOM_SA（客製分支 SA 規格文件索引，唯一寫入口）。"
                "由 custom-sa-analyze skill 逐文件呼叫，每處理完一份文件立刻寫入。"
                "依 (branch_name, doc_path) upsert，重跑會覆蓋同一份文件的舊結果——"
                "一次性建檔工具，無 supersede 訂正鏈。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tax_id": {"type": "string", "description": "客戶統編（8 碼）"},
                    "branch_name": {"type": "string", "description": "CUSTOM_GIT 分支名稱"},
                    "doc_path": {"type": "string", "description": "SA 文件在分支內的相對路徑"},
                    "doc_format": {"type": "string", "description": "doc/docx/pdf/xls/xlsx"},
                    "doc_type": {"type": "string", "enum": ["spec", "confirm", "accept", "install", "other"],
                                 "description": "依檔名關鍵字分類（可選）"},
                    "version_label": {"type": "string", "description": "檔名擷取的版本字串，如 V2.1（可選）"},
                    "is_latest": {"type": "boolean", "description": "同版本家族中是否為最新版（判斷不出來留空）"},
                    "analyzed": {"type": "boolean", "description": "是否已完成步驟4深度分析"},
                    "summary": {"type": "string", "description": "深度分析摘要（僅 analyzed=true 時填）"},
                    "mapped_paths": {"type": "string",
                                      "description": "對應到的程式碼路徑，一行一筆 repo 相對路徑；客製 repo 保留前綴如 CUSTOM_GIT/23019591_Skhb/…"},
                    "mapping_status": {"type": "string", "enum": ["unmapped", "partial", "mapped"],
                                        "description": "預設 unmapped"},
                    "parse_issue": {"type": "string", "description": "文件格式無法解析等例外註記（可選）"},
                    "branch_commit": {"type": "string", "description": "掃描當下的分支 commit SHA（可選）"},
                },
                "required": ["tax_id", "branch_name", "doc_path", "doc_format"],
            },
        ),
        Tool(
            name="custom_log",
            description=(
                "寫入/更新一筆 HRMS_CUSTOM（客製分支檔案層級深度分析，唯一寫入口）。"
                "由兩階段掃描 skill 對候選客製檔案深讀＋跟標準版比對後呼叫，每處理完一個"
                "檔案立刻寫入。依 (branch_name, custom_path) upsert，重跑會覆蓋同一檔案的"
                "舊結果——一次性建檔工具，無 supersede 訂正鏈。\n"
                "sa_doc_path 有給值時，會同步回填 HRMS_CUSTOM_SA 該筆文件的 mapped_paths"
                "（雙向關聯；找不到對應文件時回填會失敗但不影響本次寫入）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "company_sno": {"type": "string", "description": "客戶統編（8 碼）"},
                    "branch_name": {"type": "string", "description": "CUSTOM_GIT 分支名稱"},
                    "custom_path": {"type": "string", "description": "客製版檔案路徑（repo 相對，CUSTOM_GIT 內）"},
                    "custom_type": {"type": "string", "enum": ["standard", "pure", "version_lag", "std_attached"],
                                     "description": "standard=標準客製(有標準版對應且邏輯不同) / pure=純客製(該客戶專屬，通常在plugin目錄) / version_lag=非真客製，只是標準版舊拷貝、版本落後 / std_attached=標準附屬，純粹是標準檔案為了讓客製元件能編譯而存在，完全不在客製範圍內"},
                    "std_baseline": {"type": "string", "description": "本次比對用的標準版分支，如 '202607_000'（每半年更新，必填）"},
                    "standard_path": {"type": "string", "description": "標準版對應路徑（repo 相對）；純客製無標準對應可留空"},
                    "description": {"type": "string", "description": "詳細記錄客製了什麼邏輯；version_lag/std_attached 可留空或記說明"},
                    "sa_doc_path": {"type": "string", "description": "對應 HRMS_CUSTOM_SA.DOC_PATH（找到規格書時填，會回填該文件的 mapped_paths；找不到留空）"},
                    "source": {"type": "string", "description": "產生途徑，預設 'deep_scan'（可選）"},
                    "branch_commit": {"type": "string", "description": "掃描當下的分支 commit SHA（可選）"},
                },
                "required": ["company_sno", "branch_name", "custom_path", "custom_type", "std_baseline"],
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
    if name == "faq_lookup":
        return _text(rc.lookup(
            "faq", arguments["query"], arguments.get("category"), arguments.get("top_k", 5)))
    if name == "bulletin_lookup":
        return _text(rc.lookup(
            "bulletin", arguments["query"], arguments.get("category"), arguments.get("top_k", 5)))
    if name == "version_bulletins":
        return _text(rc.version_bulletins())
    if name == "task_lookup":
        return _text(rc.lookup(
            "task", arguments["query"], None, arguments.get("top_k", 5)))
    if name == "sop_lookup":
        return _text(rc.lookup(
            "sop", arguments["query"], None, arguments.get("top_k", 3)))
    if name == "faq_log":
        return _text(rw.faq_log(
            arguments["question"], arguments["answer"], arguments["module"], arguments["refs"],
            sub_category=arguments.get("sub_category", ""), row_id=arguments.get("id")))
    if name == "task_log":
        return _text(rw.task_log(
            arguments["task_name"], arguments["task_description"], arguments["syntax"],
            arguments["notes"], arguments["module"], arguments["category"], arguments["refs"],
            related_faq_id=arguments.get("related_faq_id"), row_id=arguments.get("id")))
    if name == "sop_log":
        return _text(rw.sop_log(
            arguments["title"], arguments["content"], arguments["module"], arguments["refs"],
            keywords=arguments.get("keywords", ""), description=arguments.get("description", ""),
            row_id=arguments.get("id")))
    if name == "wiki_lookup":
        return _text(wc.lookup(
            query=arguments.get("query"), page=arguments.get("page"),
            section=arguments.get("section"), customer_code=arguments.get("customer_code"),
            top_k=arguments.get("top_k", 3)))
    if name == "custom_sa_log":
        return _text(sc.log_doc(
            arguments["tax_id"], arguments["branch_name"], arguments["doc_path"],
            arguments["doc_format"],
            doc_type=arguments.get("doc_type"),
            version_label=arguments.get("version_label", ""),
            is_latest=arguments.get("is_latest"),
            analyzed=bool(arguments.get("analyzed", False)),
            summary=arguments.get("summary", ""),
            mapped_paths=arguments.get("mapped_paths", ""),
            mapping_status=arguments.get("mapping_status", "unmapped"),
            parse_issue=arguments.get("parse_issue", ""),
            branch_commit=arguments.get("branch_commit", "")))
    if name == "custom_log":
        return _text(cc.log_file(
            arguments["company_sno"], arguments["branch_name"], arguments["custom_path"],
            arguments["custom_type"], arguments["std_baseline"],
            standard_path=arguments.get("standard_path", ""),
            description=arguments.get("description", ""),
            sa_doc_path=arguments.get("sa_doc_path", ""),
            source=arguments.get("source", "deep_scan"),
            branch_commit=arguments.get("branch_commit", "")))
    raise ValueError(f"Unknown tool: {name}")


async def main_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main_stdio())
