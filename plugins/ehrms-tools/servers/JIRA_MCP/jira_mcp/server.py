"""MCP Server for Jira API integration - 支援 HTTP + SSE 和 stdio 模式"""

import asyncio
import base64
import json
import os
from contextvars import ContextVar
from typing import Any, Optional
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)
import uvicorn

from .client import JiraClient, jira_client
from .config import ServerConfig, JiraCredentials

# 儲存當次 HTTP 請求的 Jira 認證（HTTP 模式 per-request 使用）
_request_credentials: ContextVar[Optional[JiraCredentials]] = ContextVar(
    "request_credentials", default=None
)


def _get_client() -> JiraClient:
    """取得當次請求的 JiraClient：HTTP 模式優先使用 client 端認證，否則使用伺服器預設"""
    credentials = _request_credentials.get()
    if credentials:
        return JiraClient(credentials=credentials)
    return jira_client

# ── 附件回傳的防爆量參數 ─────────────────────────────────────
# Claude API 可直接檢視的影像格式；bmp/svg 等一律走 local
VIEWABLE_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_MIMES = {"application/json", "application/xml", "application/x-yaml",
              "application/javascript", "application/sql"}
TEXT_EXTS = {"txt", "log", "csv", "md", "json", "xml", "yaml", "yml", "sql",
             "ini", "cfg", "conf", "config", "properties", "html", "htm",
             "js", "css", "py", "cs", "java", "vb", "aspx"}
MAX_IMAGE_BYTES = 3 * 1024 * 1024   # ImageContent 上限（API 影像限制 5MB，留 base64 膨脹裕度）
MAX_BASE64_BYTES = 1 * 1024 * 1024  # base64 文字回傳上限（膨脹 4/3 後仍在輸出限制內）
MAX_TEXT_CHARS = 50_000             # text 模式截斷長度


def _is_text_attachment(mime_type: str, filename: str) -> bool:
    if (mime_type or "").startswith("text/") or mime_type in TEXT_MIMES:
        return True
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in TEXT_EXTS


def _decode_attachment_text(content: bytes) -> str:
    # 客戶端文字檔常見 UTF-8（含 BOM）與 Big5/CP950；latin1 只當最後保底
    for enc in ("utf-8-sig", "cp950"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin1", errors="ignore")


# 建立 MCP 伺服器實例
mcp_server = Server("jira-mcp-server")

# 建立 SSE 傳輸層（MCP 標準 SSE 協議）
sse_transport = SseServerTransport("/messages")


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用的工具"""
    return [
        Tool(
            name="get_issue",
            description="取得單張 Issue 的完整內容：精簡欄位＋**完整描述**（ADF 轉純文字）＋**最新 N 筆評論**（新→舊，預設 10）。查單筆全文用這個；要更早的評論調大 comments_limit 或用 get_comments。",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue 的 Key，例如: PROJ-123"
                    },
                    "comments_limit": {
                        "type": "number",
                        "description": "評論筆數（預設 10，最新優先；0=不帶評論）",
                        "default": 10
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="get_issue_summary",
            description="取得 Issue 的基本摘要資訊（輕量查詢），僅回傳 key、summary、status、assignee、priority、issue_type、created、updated。",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue 的 Key，例如: PROJ-123"
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="search_issues",
            description="使用 JQL 搜尋 Jira Issues。預設回精簡清單（每筆只有 key、summary、status、assignee、updated）——掃清單找目標用，單筆細節請用 get_issue；detail=\"full\" 才回 14 個欄位＋描述截斷。fields 可追加額外欄位（如 customfield_12722），追加欄位轉純文字並截斷。單頁上限 50 筆；回傳含 next_page_token 時帶回原參數可取下一頁。",
            inputSchema={
                "type": "object",
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": 'JQL 查詢字串，例如: "project = MYPROJ AND status = Open"'
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["list", "full"],
                        "description": "list（預設）：最小清單欄位；full：14 欄＋描述截斷",
                        "default": "list"
                    },
                    "max_results": {
                        "type": "number",
                        "description": "單頁筆數（預設 50，上限 50）",
                        "default": 50
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要「追加」的欄位清單，例如: [\"customfield_12722\"]；*all 無效"
                    },
                    "next_page_token": {
                        "type": "string",
                        "description": "上一頁回傳的翻頁 token（取下一頁時帶入）"
                    }
                },
                "required": ["jql"]
            }
        ),
        Tool(
            name="get_comments",
            description="取得 Issue 最新的 N 筆評論（新→舊，預設 20；body 由 ADF 轉純文字精簡輸出）。要更早的討論調大 limit。",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue 的 Key，例如: PROJ-123"
                    },
                    "limit": {
                        "type": "number",
                        "description": "評論筆數（預設 20，最新優先，上限 500）",
                        "default": 20
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="get_attachment",
            description="下載並取得附件內容。auto 模式（預設）依類型自動處理：圖片（png/jpg/gif/webp）以可檢視影像回傳（AI 可直接看圖）、文字檔轉純文字（自動嘗試 UTF-8/CP950 編碼）、其他類型存到本地資料夾。內建大小防護，超過上限會提示改用 output=local。",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue 的 Key，例如: PROJ-123"
                    },
                    "filename": {
                        "type": "string",
                        "description": "附件檔名"
                    },
                    "output": {
                        "type": "string",
                        "enum": ["auto", "image", "text", "base64", "local"],
                        "description": "auto（預設）：依附件類型自動選擇；也可強制指定 image / text / base64 / local",
                        "default": "auto"
                    },
                    "download_folder": {
                        "type": "string",
                        "description": "output=local 時的存放路徑，預設 downloads"
                    }
                },
                "required": ["issue_key", "filename"]
            }
        ),
        Tool(
            name="get_issue_changelog",
            description="取得 Issue 的變更歷史，精簡輸出為 (時間, 操作者, 欄位 from→to)，新→舊排序，預設最新 50 筆。fields 可只看特定欄位的流轉（如 [\"status\"] 或 [\"assignee\"]）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue 的 Key，例如: PROJ-123"
                    },
                    "limit": {
                        "type": "number",
                        "description": "回傳筆數（預設 50，最新優先）",
                        "default": 50
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "只看這些欄位的變更，例如 [\"status\", \"assignee\"]（選填，欄位名不分大小寫）"
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="get_user_info",
            description="取得用戶資訊。不填 username 回傳當前用戶；填入 email 或顯示名稱則模糊搜尋（回傳最多 10 筆 accountId/displayName/email）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "email 或顯示名稱關鍵字（選填，不填則取得當前用戶）"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="list_custom_fields",
            description="搜尋 Jira 自訂欄位定義（EHRMSONE 有數百個自訂欄位，請帶 query 關鍵字過濾，例如「Engineer」「工號」；回傳 id、name、type）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "欄位名稱或 id 的關鍵字（不分大小寫）。不填只回傳總數統計"
                    }
                }
            }
        ),
        Tool(
            name="add_comment",
            description="對指定的 Jira Issue 新增評論",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue 的 Key，例如: PROJ-123"
                    },
                    "body": {
                        "type": "string",
                        "description": "評論內容（純文字）"
                    }
                },
                "required": ["issue_key", "body"]
            }
        ),
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent | ImageContent | EmbeddedResource]:
    """處理工具調用"""

    try:
        client = _get_client()

        if name == "get_issue":
            issue_key = arguments["issue_key"]
            comments_limit = int(arguments.get("comments_limit", 10))
            result = await client.get_issue_basic(issue_key, comments_limit=comments_limit)
            return [TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2)
            )]

        elif name == "get_issue_summary":
            issue_key = arguments["issue_key"]
            result = await client.get_issue_summary(issue_key)
            return [TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2)
            )]

        elif name == "search_issues":
            jql = arguments["jql"]
            max_results = arguments.get("max_results", 50)
            fields = arguments.get("fields")
            next_page_token = arguments.get("next_page_token")
            detail = arguments.get("detail", "list")
            result = await client.search_issues(
                jql=jql, fields=fields, max_results=max_results,
                next_page_token=next_page_token, detail=detail)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_comments":
            issue_key = arguments["issue_key"]
            limit = int(arguments.get("limit", 20))
            result = await client.get_issue_comments(issue_key, limit=limit)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_attachment":
            issue_key = arguments["issue_key"]
            filename = arguments["filename"]
            output = arguments.get("output", "auto")
            download_folder = arguments.get("download_folder", "downloads")

            issue = await client.get_issue(issue_key, fields=["attachment"], plain_text=False)
            attachments = issue.get("fields", {}).get("attachment", [])
            target_attachment = next((att for att in attachments if att["filename"] == filename), None)

            if not target_attachment:
                names = [a.get("filename", "") for a in attachments]
                return [TextContent(
                    type="text",
                    text=f"找不到檔名為 '{filename}' 的附件。此單的附件清單：{names}")]

            content_url = target_attachment["content"]
            mime_type = target_attachment.get("mimeType", "application/octet-stream")
            size = target_attachment.get("size") or 0

            if output == "auto":
                if mime_type in VIEWABLE_IMAGE_MIMES:
                    output = "image"
                elif _is_text_attachment(mime_type, filename):
                    output = "text"
                else:
                    output = "local"

            if output == "image":
                if mime_type not in VIEWABLE_IMAGE_MIMES:
                    return [TextContent(
                        type="text",
                        text=f"'{filename}'（{mime_type}）不是可直接檢視的圖片格式，請改用 output=local 下載")]
                if size > MAX_IMAGE_BYTES:
                    return [TextContent(
                        type="text",
                        text=f"圖片過大（{size:,} bytes，上限 {MAX_IMAGE_BYTES:,}），請改用 output=local 下載後以 Read 工具檢視")]
                content = await client.download_attachment_content(content_url)
                return [
                    ImageContent(
                        type="image",
                        data=base64.b64encode(content).decode("utf-8"),
                        mimeType=mime_type),
                    TextContent(
                        type="text",
                        text=f"{filename}（{mime_type}，{len(content):,} bytes）"),
                ]

            elif output == "base64":
                if size > MAX_BASE64_BYTES:
                    return [TextContent(
                        type="text",
                        text=f"附件過大（{size:,} bytes，base64 回傳上限 {MAX_BASE64_BYTES:,}），請改用 output=local")]
                content = await client.download_attachment_content(content_url)
                if len(content) > MAX_BASE64_BYTES:
                    return [TextContent(
                        type="text",
                        text=f"附件過大（{len(content):,} bytes，base64 回傳上限 {MAX_BASE64_BYTES:,}），請改用 output=local")]
                base64_content = base64.b64encode(content).decode("utf-8")
                return [TextContent(type="text", text=json.dumps({
                    "filename": filename,
                    "mimeType": mime_type,
                    "size": len(content),
                    "encoding": "base64",
                    "content": base64_content
                }, indent=2, ensure_ascii=False))]

            elif output == "local":
                result = await client.download_attachment_to_local(content_url, filename, download_folder)
                if result["success"]:
                    return [TextContent(type="text", text=json.dumps({
                        "filename": filename,
                        "file_path": result["file_path"],
                        "file_size": result["file_size"]
                    }, indent=2, ensure_ascii=False))]
                else:
                    return [TextContent(type="text", text=f"下載失敗: {result['error']}")]

            elif output == "text":
                content = await client.download_attachment_content(content_url)
                text = _decode_attachment_text(content)
                if len(text) > MAX_TEXT_CHARS:
                    text = (text[:MAX_TEXT_CHARS]
                            + f"\n…（已截斷，全文 {len(text):,} 字，完整檔案請改用 output=local）")
                return [TextContent(type="text", text=text)]

            else:
                return [TextContent(type="text", text=f"不支援的 output 參數: {output}")]

        elif name == "get_issue_changelog":
            issue_key = arguments["issue_key"]
            limit = int(arguments.get("limit", 50))
            fields = arguments.get("fields")
            result = await client.get_issue_changelog(issue_key, limit=limit, fields=fields)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_user_info":
            username = arguments.get("username")
            result = await client.get_user_info(username)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "add_comment":
            issue_key = arguments["issue_key"]
            body = arguments["body"]
            result = await client.add_comment(issue_key, body)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "list_custom_fields":
            query = (arguments.get("query") or "").strip().lower()
            all_fields = await client.get_custom_fields()
            customs = [f for f in all_fields if f["id"].startswith("customfield_")]
            if not query:
                return [TextContent(
                    type="text",
                    text=f"共 {len(customs)} 個自訂欄位。請帶 query 關鍵字（欄位名稱或 id）過濾，避免一次回傳全部。")]
            hits = [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "type": (f.get("schema") or {}).get("type", ""),
                }
                for f in customs
                if query in f["name"].lower() or query in f["id"].lower()
            ]
            out: dict = {"count": len(hits), "fields": hits[:30]}
            if len(hits) > 30:
                out["note"] = f"符合 {len(hits)} 個，僅回傳前 30 個，請縮小關鍵字"
            return [TextContent(
                type="text",
                text=json.dumps(out, indent=2, ensure_ascii=False)
            )]

        else:
            return [TextContent(
                type="text",
                text=f"未知的工具: {name}"
            )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=f"錯誤: {str(e)}"
        )]


# ============================================================
# HTTP 模式 - 使用 MCP SDK 標準 SSE 傳輸
# ============================================================

async def handle_sse(request):
    """
    SSE 端點 - MCP 標準 SSE 傳輸
    客戶端先 GET /sse 建立 SSE 連線，
    伺服器回傳 endpoint event 告知 POST URL，
    之後客戶端透過 POST /messages 傳送 JSON-RPC，
    伺服器透過 SSE 串流回傳結果。
    """
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0],
            streams[1],
            mcp_server.create_initialization_options()
        )


async def handle_messages(request):
    """處理 MCP 客戶端 POST 的 JSON-RPC 消息"""
    credentials = JiraCredentials.from_headers(dict(request.headers))
    token = _request_credentials.set(credentials)
    try:
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )
    finally:
        _request_credentials.reset(token)


async def health_check(request):
    """健康檢查端點"""
    from starlette.responses import JSONResponse as SJSONResponse
    return SJSONResponse({
        "status": "ok",
        "server": ServerConfig.SERVER_NAME
    })


async def root_info(request):
    """根端點 - 顯示伺服器資訊和工具列表"""
    from starlette.responses import JSONResponse as SJSONResponse
    try:
        tools = await list_tools()
        tools_list = [
            {"name": getattr(t, "name", ""), "description": getattr(t, "description", "")}
            for t in tools
            if getattr(t, "name", None)
        ]
        return SJSONResponse({
            "status": "ok",
            "server": ServerConfig.SERVER_NAME,
            "version": "1.0.0",
            "transport": "SSE (MCP standard)",
            "endpoints": {
                "health": "GET  /health",
                "sse": "GET  /sse  (MCP client entry point)",
                "messages": "POST /messages  (managed by SSE transport)",
                "tools": "GET  /tools  (convenience)"
            },
            "tools_count": len(tools_list),
            "tools": tools_list,
        })
    except Exception as e:
        return SJSONResponse(
            {"status": "error", "error": str(e)}, status_code=500
        )


async def get_tools_endpoint(request):
    """GET 端點 - 獲取工具列表（便捷查詢用）"""
    from starlette.responses import JSONResponse as SJSONResponse
    try:
        tools = await list_tools()
        tools_list = []
        for tool in tools:
            td = {
                "name": getattr(tool, "name", None),
                "description": getattr(tool, "description", None),
                "inputSchema": getattr(tool, "inputSchema", {})
            }
            if td["name"] and td["description"] and td["inputSchema"]:
                tools_list.append(td)
        return SJSONResponse({
            "jsonrpc": "2.0",
            "result": {"tools": tools_list}
        })
    except Exception as e:
        return SJSONResponse({
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
        }, status_code=500)


def create_starlette_app():
    """建立 Starlette 應用，整合 MCP SSE 傳輸 + 便捷端點"""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    middleware = []
    if ServerConfig.ENABLE_CORS:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=ServerConfig.get_cors_origins(),
                allow_credentials=True,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
            )
        )

    routes = [
        # MCP 標準 SSE 傳輸端點
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"]),
        # 便捷端點
        Route("/health", endpoint=health_check),
        Route("/tools", endpoint=get_tools_endpoint),
        Route("/", endpoint=root_info),
    ]

    return Starlette(routes=routes, middleware=middleware)


# ============================================================
# 啟動函式
# ============================================================

async def main_stdio():
    """主函式 - stdio 模式"""
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options()
        )


async def main_http():
    """主函式 - HTTP 模式（MCP 標準 SSE 傳輸）"""
    starlette_app = create_starlette_app()

    print("=" * 50)
    print("Jira MCP HTTP Server")
    print("=" * 50)
    print(f"Listen:     http://{ServerConfig.HOST}:{ServerConfig.PORT}")
    print(f"MCP SSE:    http://{ServerConfig.HOST}:{ServerConfig.PORT}/sse")
    print(f"Messages:   http://{ServerConfig.HOST}:{ServerConfig.PORT}/messages")
    print(f"Health:     http://{ServerConfig.HOST}:{ServerConfig.PORT}/health")
    print(f"Tools:      http://{ServerConfig.HOST}:{ServerConfig.PORT}/tools")
    print("=" * 50)

    if ServerConfig.REQUIRE_AUTH and ServerConfig.API_KEY:
        print("Auth: enabled (API Key)")
    else:
        print("Auth: disabled")
    print()

    uvi_config = uvicorn.Config(
        starlette_app,
        host=ServerConfig.HOST,
        port=ServerConfig.PORT,
        log_level="info"
    )
    server_instance = uvicorn.Server(uvi_config)
    await server_instance.serve()


async def main():
    """根據 TRANSPORT 環境變數啟動對應模式"""
    mode = ServerConfig.TRANSPORT
    if mode == "stdio":
        print("Jira MCP server running (stdio mode)")
        await main_stdio()
    else:
        await main_http()


if __name__ == "__main__":
    asyncio.run(main())
