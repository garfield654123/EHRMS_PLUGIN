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
from .config import config, ServerConfig, JiraCredentials

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
            description="取得 Issue 的基本欄位與評論（輕量查詢）。一次回傳 key、summary、status、assignee、priority、issue_type、created、updated 與所有評論（純文字），不抓取全部欄位以節省資源。",
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
            description="使用 JQL 搜尋 Jira Issues。永遠回傳精簡結構（key、summary、status、assignee、reporter、priority、issue_type、created、updated、duedate、labels、components、description[截斷]、attachments、parent），描述已轉純文字並截斷——需要單筆全文請改用 get_issue。fields 可追加額外欄位（如 customfield_12722），追加欄位也會轉純文字並截斷，不會回傳原始 JSON。單頁上限 50 筆；回傳含 next_page_token 時帶回原參數可取下一頁。",
            inputSchema={
                "type": "object",
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": 'JQL 查詢字串，例如: "project = MYPROJ AND status = Open"'
                    },
                    "max_results": {
                        "type": "number",
                        "description": "單頁筆數（預設 50，上限 50）",
                        "default": 50
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要「追加」的欄位清單，例如: [\"customfield_12722\"]。基本精簡欄位一律回傳；*all 無效"
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
            name="get_my_issues",
            description=f"取得指定使用者的 Issues（預設使用者: {config.default_user}）",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_email": {
                        "type": "string",
                        "description": f"使用者 Email（預設: {config.default_user}）"
                    },
                    "status": {
                        "type": "string",
                        "description": "過濾特定狀態，例如: Open, In Progress, Done（選填）"
                    },
                    "max_results": {
                        "type": "number",
                        "description": "最大結果數量（預設: 50）",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="get_comments",
            description="取得 Issue 的所有評論",
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
            name="get_attachment",
            description="下載並取得附件內容，支援 base64、local、text 三種回傳模式。合併了原本的 download_attachment_as_base64、download_image_as_base64、download_attachment_to_local。",
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
                        "description": "base64（預設）/ local / text",
                        "default": "base64"
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
            name="get_issue_transitions",
            description="取得 Issue 的可用轉換狀態 (符合 Jira REST API v3 標準)",
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
            name="get_issue_changelog",
            description="取得 Issue 的完整變更歷史（自動翻頁），精簡輸出為 (時間, 操作者, 欄位 from→to) 列表。",
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
            description="列出 Jira 中所有的自訂欄位定義",
            inputSchema={
                "type": "object",
                "properties": {}
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
            result = await client.get_issue_basic(issue_key)
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
            result = await client.search_issues(
                jql=jql, fields=fields, max_results=max_results,
                next_page_token=next_page_token)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_my_issues":
            user_email = arguments.get("user_email")
            status = arguments.get("status")
            max_results = arguments.get("max_results", 50)
            result = await client.get_user_issues(
                user_email=user_email,
                status=status,
                max_results=max_results
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_comments":
            issue_key = arguments["issue_key"]
            result = await client.get_issue_comments(issue_key)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_attachment":
            issue_key = arguments["issue_key"]
            filename = arguments["filename"]
            output = arguments.get("output", "base64")
            download_folder = arguments.get("download_folder", "downloads")

            issue = await client.get_issue(issue_key, fields=["attachment"], plain_text=False)
            attachments = issue.get("fields", {}).get("attachment", [])
            target_attachment = next((att for att in attachments if att["filename"] == filename), None)

            if not target_attachment:
                return [TextContent(type="text", text=f"找不到檔名為 '{filename}' 的附件")]

            content_url = target_attachment["content"]
            mime_type = target_attachment["mimeType"]

            if output == "base64":
                content = await client.download_attachment_content(content_url)
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
                try:
                    text = content.decode("utf-8")
                except Exception:
                    text = content.decode("latin1", errors="ignore")
                return [TextContent(type="text", text=text)]

            else:
                return [TextContent(type="text", text=f"不支援的 output 參數: {output}")]

        elif name == "get_issue_transitions":
            issue_key = arguments["issue_key"]
            result = await client.get_issue_transitions(issue_key)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False)
            )]

        elif name == "get_issue_changelog":
            issue_key = arguments["issue_key"]
            result = await client.get_issue_changelog(issue_key)
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
            all_fields = await client.get_custom_fields()
            custom_fields = [
                {
                    "id": field["id"],
                    "name": field["name"],
                    "custom": field.get("custom", False),
                    "schema": field.get("schema", {})
                }
                for field in all_fields
                if field["id"].startswith("customfield_")
            ]
            return [TextContent(
                type="text",
                text=json.dumps(custom_fields, indent=2, ensure_ascii=False)
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
