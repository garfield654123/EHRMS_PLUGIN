#!/usr/bin/env python3
"""
MCP 服務器實現 - 支援 HTTP + SSE 和 stdio 模式
"""

import asyncio
import json
import os
from typing import Optional
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import uvicorn
import asyncio

# 導入工具服務
from tools.mssql_test import test_mssql_connection
from tools.mssql_query import execute_mssql_query
from tools.table_search import search_tables
from tools.table_joins import analyze_table_joins
from tools.table_columns import get_table_columns
# 導入新工作流程工具
from tools.db_init import db_mcp_init
from tools.db_generate_query import db_generate_query
from tools.db_validate_query import db_validate_query
from utils.formatter import format_text_response
from config import ServerConfig

# 載入環境變數
load_dotenv()


# Pydantic 請求模型
class EchoRequest(BaseModel):
    """Echo 工具的請求參數"""
    message: str


class MSSQLQueryRequest(BaseModel):
    """MSSQL 查詢工具的請求參數"""
    query: str
    server: Optional[str] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    session_id: Optional[str] = None


class MSSQLConnectionRequest(BaseModel):
    """MSSQL 連接測試工具的請求參數"""
    server: str
    database: str
    username: str
    password: str


class TableSearchRequest(BaseModel):
    """表格搜尋工具的請求參數"""
    description: str
    limit: Optional[int] = 20


class TableJoinAnalysisRequest(BaseModel):
    """表格JOIN關係分析工具的請求參數"""
    table_name: str
    session_id: Optional[str] = None
    include_common_joins: Optional[bool] = True


class TableColumnsRequest(BaseModel):
    """表格欄位資訊查詢工具的請求參數"""
    table_name: str
    session_id: Optional[str] = None


class DBInitRequest(BaseModel):
    """資料庫 MCP 初始化工具的請求參數"""
    topic: str


class DBGenerateQueryRequest(BaseModel):
    """SQL 生成工具的請求參數"""
    session_id: str
    prompt: str


class DBValidateQueryRequest(BaseModel):
    """SQL 驗證工具的請求參數"""
    session_id: str
    sql_query: str


# 建立 MCP 服務器實例
server = Server(ServerConfig.SERVER_NAME)

# 建立 FastAPI 應用
app = FastAPI(title="MCP Server", version="1.0.0")

# 設定 CORS（如果啟用）
if ServerConfig.ENABLE_CORS:
    cors_origins = ServerConfig.get_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出可用的工具"""
    return [
        Tool(
            name="echo",
            description="簡單的回音工具，回傳輸入的訊息",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "要回音的訊息"
                    }
                },
                "required": ["message"]
            }
        ),
        Tool(
            name="mssql_query",
            description="執行 MSSQL 資料庫查詢",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要執行的 SQL 查詢語句"
                    },
                    "server": {
                        "type": "string",
                        "description": "MSSQL 伺服器位址（可選，預設從環境變數讀取）"
                    },
                    "database": {
                        "type": "string",
                        "description": "資料庫名稱（可選，預設從環境變數讀取）"
                    },
                    "username": {
                        "type": "string",
                        "description": "使用者名稱（可選，預設從環境變數讀取）"
                    },
                    "password": {
                        "type": "string",
                        "description": "密碼（可選，預設從環境變數讀取）"
                    },
                    "session_id": {
                        "type": "string",
                        "description": "工作階段 ID（可選，用於記錄查詢歷史）"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="mssql_test_connection",
            description="測試 MSSQL 資料庫連接",
            inputSchema={
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "MSSQL 伺服器位址"
                    },
                    "database": {
                        "type": "string",
                        "description": "資料庫名稱"
                    },
                    "username": {
                        "type": "string",
                        "description": "使用者名稱"
                    },
                    "password": {
                        "type": "string",
                        "description": "密碼"
                    }
                },
                "required": ["server", "database", "username", "password"]
            }
        ),
        Tool(
            name="search_tables",
            description="根據關鍵字搜尋 HRMS_TABLES 中相關的表格",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "描述性文字，用於搜尋相關表格的關鍵字"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "限制返回結果數量（預設20筆）"
                    }
                },
                "required": ["description"]
            }
        ),
        Tool(
            name="analyze_table_joins",
            description="分析指定表格的JOIN關係，從 hrms_table_relation 表格讀取已定義的關聯關係，包含關聯描述和業務規則。支援可選的 session_id 參數以進行 session 驗證。",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "要分析JOIN關係的表格名稱"
                    },
                    "session_id": {
                        "type": "string",
                        "description": "工作階段 ID（可選，提供時會進行 session 驗證）"
                    },
                    "include_common_joins": {
                        "type": "boolean",
                        "description": "保留此參數以維持相容性，但新版本直接從 hrms_table_relation 讀取所有關聯"
                    }
                },
                "required": ["table_name"]
            }
        ),
        Tool(
            name="get_table_columns",
            description="查詢指定表格的欄位資訊，包含欄位名稱、中文說明、格式、長度等詳細資訊。備註中 | 後方為參數說明。支援可選的 session_id 參數以啟用快取功能。",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "要查詢欄位資訊的表格名稱"
                    },
                    "session_id": {
                        "type": "string",
                        "description": "工作階段 ID（可選，提供時會啟用快取功能）"
                    }
                },
                "required": ["table_name"]
            }
        ),
        # 新工作流程工具
        Tool(
            name="db_mcp_init",
            description="初始化 MCP session，根據關鍵字，主題名稱從 hrms_sys_rule 獲取相關表清單。這是新工作流程的第一步。",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "主題關鍵字，用於搜尋 hrms_sys_rule (Category = 'DB') 中的相關規則"
                    }
                },
                "required": ["topic"]
            }
        ),
        Tool(
            name="db_generate_query",
            description="根據 session 上下文、表結構、關聯資訊提供 SQL 生成建議。需要先使用 db_mcp_init 建立 session。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "工作階段 ID（從 db_mcp_init 取得）"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "查詢需求描述"
                    }
                },
                "required": ["session_id", "prompt"]
            }
        ),
        Tool(
            name="db_validate_query",
            description="驗證 SQL 查詢的語法、結構和安全性。需要先使用 db_mcp_init 建立 session。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "工作階段 ID（從 db_mcp_init 取得）"
                    },
                    "sql_query": {
                        "type": "string",
                        "description": "要驗證的 SQL 語句"
                    }
                },
                "required": ["session_id", "sql_query"]
            }
        )
    ]


async def stream_tools_list(request_id):
    """流式工具列表生成器"""
    import json
    
    # 發送開始消息
    yield json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            # 兼容 MCP client：tools/list 的 result 最終必須有 tools
            # 這裡的 status 為擴充欄位，不影響標準欄位
            "status": "started",
            "tools": []
        }
    }, ensure_ascii=False) + "\n"
    
    try:
        tools = await list_tools()
        tools_list = []
        
        for tool in tools:
            try:
                # 手動構建符合 MCP 標準的工具字典
                tool_dict = {
                    "name": getattr(tool, "name", None),
                    "description": getattr(tool, "description", None),
                    "inputSchema": getattr(tool, "inputSchema", {})
                }
                
                # 確保所有必要欄位都存在
                if (tool_dict.get("name") and 
                    tool_dict.get("description") and 
                    tool_dict.get("inputSchema")):
                    # 只包含必要欄位
                    clean_tool = {
                        "name": tool_dict["name"],
                        "description": tool_dict["description"],
                        "inputSchema": tool_dict["inputSchema"]
                    }
                    tools_list.append(clean_tool)
            except Exception as e:
                print(f"錯誤: 序列化工具時發生錯誤: {e}")
        
        # 發送完整結果
        yield json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "status": "completed",
                "tools": tools_list
            }
        }, ensure_ascii=False) + "\n"
        
    except Exception as e:
        yield json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }, ensure_ascii=False) + "\n"


async def stream_tool_call(tool_name: str, arguments: dict, request_id):
    """流式工具調用生成器"""
    import json
    
    # 發送開始消息
    yield json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "status": "started",
            "tool": tool_name,
            # 兼容 MCP client：tools/call 的 result 最終必須有 content
            "content": []
        }
    }, ensure_ascii=False) + "\n"
    
    try:
        # 執行工具
        result = await call_tool(tool_name, arguments)
        
        # 如果結果是列表，可以逐步發送
        if isinstance(result, list) and len(result) > 0:
            # 發送進度消息
            yield json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "status": "processing",
                    "progress": 0.5,
                    "content": []
                }
            }, ensure_ascii=False) + "\n"
            
            # 發送結果
            yield json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "status": "completed",
                    "content": result
                }
            }, ensure_ascii=False) + "\n"
        else:
            # 直接發送結果
            yield json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "status": "completed",
                    "content": result
                }
            }, ensure_ascii=False) + "\n"
            
    except Exception as e:
        # 發送錯誤
        yield json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": f"Tool execution error: {str(e)}"
            }
        }, ensure_ascii=False) + "\n"


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[dict]:
    """處理工具調用 - 簡化版本，僅負責路由"""
    # 讓例外往上拋，交給 MCP/JSON-RPC 層用標準 error 回應
    if name == "echo":
        request = EchoRequest(**arguments)
        return format_text_response(f"回音: {request.message}")
    
    if name == "mssql_test_connection":
        request = MSSQLConnectionRequest(**arguments)
        return test_mssql_connection(
            request.server,
            request.database,
            request.username,
            request.password
        )
    
    if name == "mssql_query":
        request = MSSQLQueryRequest(**arguments)
        # 支援可選的 session_id
        session_id = arguments.get("session_id")
        return execute_mssql_query(
            request.query,
            request.server,
            request.database,
            request.username,
            request.password,
            session_id=session_id
        )
    
    if name == "search_tables":
        request = TableSearchRequest(**arguments)
        return search_tables(request.description, request.limit)
    
    if name == "analyze_table_joins":
        request = TableJoinAnalysisRequest(**arguments)
        return analyze_table_joins(request.table_name, request.session_id)
    
    if name == "get_table_columns":
        request = TableColumnsRequest(**arguments)
        return get_table_columns(request.table_name, request.session_id)
    
    # 新工作流程工具
    if name == "db_mcp_init":
        request = DBInitRequest(**arguments)
        return db_mcp_init(request.topic)
    
    if name == "db_generate_query":
        request = DBGenerateQueryRequest(**arguments)
        return db_generate_query(request.session_id, request.prompt)
    
    if name == "db_validate_query":
        request = DBValidateQueryRequest(**arguments)
        return db_validate_query(request.session_id, request.sql_query)
    
    raise ValueError(f"Unknown tool: {name}")


# 認證依賴
async def verify_auth(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> bool:
    """驗證 API Key（如果啟用）"""
    if not ServerConfig.REQUIRE_AUTH:
        return True
    
    if not ServerConfig.API_KEY:
        return True  # 如果沒有設定 API Key，則不驗證
    
    # 檢查 IP 白名單
    allowed_ips = ServerConfig.get_allowed_ips_list()
    if allowed_ips:
        client_ip = request.client.host if request.client else None
        if client_ip and client_ip not in allowed_ips:
            raise HTTPException(status_code=403, detail="IP address not allowed")
    
    # 檢查 API Key
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    # 支援 Bearer token 或直接 API Key
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    else:
        token = authorization
    
    if token != ServerConfig.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return True


@app.get("/health")
async def health_check():
    """健康檢查端點"""
    return {"status": "ok", "server": ServerConfig.SERVER_NAME}


@app.get("/")
async def root():
    """根端點 - 顯示服務器資訊和工具列表"""
    try:
        tools = await list_tools()
        tools_list = []
        for tool in tools:
            try:
                if hasattr(tool, "model_dump"):
                    tool_dict = tool.model_dump()
                elif hasattr(tool, "dict"):
                    tool_dict = tool.dict()
                else:
                    tool_dict = {
                        "name": getattr(tool, "name", None),
                        "description": getattr(tool, "description", None),
                        "inputSchema": getattr(tool, "inputSchema", {})
                    }
                
                if tool_dict.get("name") and tool_dict.get("description"):
                    tools_list.append({
                        "name": tool_dict.get("name"),
                        "description": tool_dict.get("description")
                    })
            except Exception as e:
                print(f"錯誤: 序列化工具時發生錯誤: {e}")
        
        return {
            "status": "ok",
            "server": ServerConfig.SERVER_NAME,
            "version": "1.0.0",
            "endpoints": {
                "health": "/health",
                "messages": "/messages (POST only)",
                "sse": "/sse",
                "tools": "/tools"
            },
            "tools_count": len(tools_list),
            "tools": tools_list,
            "note": "使用 POST /messages 端點進行 MCP 協議通信"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "server": ServerConfig.SERVER_NAME
        }


@app.get("/tools")
async def get_tools():
    """GET 端點 - 獲取工具列表（用於測試）"""
    try:
        tools = await list_tools()
        tools_list = []
        for tool in tools:
            try:
                if hasattr(tool, "model_dump"):
                    tool_dict = tool.model_dump()
                elif hasattr(tool, "dict"):
                    tool_dict = tool.dict()
                else:
                    tool_dict = {
                        "name": getattr(tool, "name", None),
                        "description": getattr(tool, "description", None),
                        "inputSchema": getattr(tool, "inputSchema", {})
                    }
                
                if tool_dict.get("name") and tool_dict.get("description") and tool_dict.get("inputSchema"):
                    tools_list.append(tool_dict)
            except Exception as e:
                print(f"錯誤: 序列化工具時發生錯誤: {e}")
        
        return {
            "jsonrpc": "2.0",
            "result": {
                "tools": tools_list
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }


@app.post("/messages")
async def handle_mcp_message(
    request: Request,
    _: bool = Depends(verify_auth)
):
    """處理 MCP 協議消息（POST）- 支援標準和流式模式"""
    body = None
    try:
        body = await request.json()
        request_id = body.get("id")
        jsonrpc = body.get("jsonrpc")
        method = body.get("method")

        # 基本 JSON-RPC 2.0 檢查（盡量不破壞相容性）
        if jsonrpc != "2.0":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request: jsonrpc must be '2.0'"
                    }
                },
                media_type="application/json"
            )
        if not method:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request: missing method"
                    }
                },
                media_type="application/json"
            )
        
        # 檢查是否請求流式響應
        stream = body.get("params", {}).get("stream", False) or request.headers.get("X-Stream-Response", "false").lower() == "true"
        
        # 處理 MCP 協議消息
        if method == "tools/list":
            # tools/list 通常不需要流式響應，但可以支援
            if stream:
                return StreamingResponse(
                    stream_tools_list(request_id),
                    media_type="application/x-ndjson"
                )
            
            tools = await list_tools()
            # 將 Tool 對象轉換為字典（符合 MCP 標準格式）
            tools_list = []
            for tool in tools:
                try:
                    # 手動構建符合 MCP 標準的工具字典
                    # MCP 標準要求：name, description, inputSchema（必需）
                    tool_dict = {
                        "name": getattr(tool, "name", None),
                        "description": getattr(tool, "description", None),
                        "inputSchema": getattr(tool, "inputSchema", {})
                    }
                    
                    # 確保所有必要欄位都存在且不為空
                    if (tool_dict.get("name") and 
                        tool_dict.get("description") and 
                        tool_dict.get("inputSchema")):
                        # 只包含必要欄位，過濾掉 null 值和可選欄位
                        clean_tool = {
                            "name": tool_dict["name"],
                            "description": tool_dict["description"],
                            "inputSchema": tool_dict["inputSchema"]
                        }
                        tools_list.append(clean_tool)
                    else:
                        print(f"警告: 工具缺少必要欄位: name={tool_dict.get('name')}, description={bool(tool_dict.get('description'))}, inputSchema={bool(tool_dict.get('inputSchema'))}")
                except Exception as e:
                    print(f"錯誤: 序列化工具時發生錯誤: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 使用 JSONResponse 確保正確的編碼和格式
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": tools_list
                    }
                },
                media_type="application/json"
            )
        
        elif method == "tools/call":
            tool_name = body.get("params", {}).get("name")
            arguments = body.get("params", {}).get("arguments", {})
            if not tool_name:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": "Invalid params: missing params.name"
                        }
                    },
                    media_type="application/json"
                )
            
            # 檢查是否使用流式響應
            if stream:
                return StreamingResponse(
                    stream_tool_call(tool_name, arguments, request_id),
                    media_type="application/x-ndjson"
                )
            else:
                # 標準響應
                try:
                    result = await call_tool(tool_name, arguments)
                    return JSONResponse(
                        content={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "content": result
                            }
                        },
                        media_type="application/json"
                    )
                except ValueError as e:
                    # Unknown tool / routing error
                    return JSONResponse(
                        content={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32601,
                                "message": str(e)
                            }
                        },
                        media_type="application/json"
                    )
                except Exception as e:
                    return JSONResponse(
                        content={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32603,
                                "message": f"Internal error: {str(e)}"
                            }
                        },
                        media_type="application/json"
                    )
        
        elif method == "initialize":
            # 獲取工具列表以確定 capabilities
            tools = await list_tools()
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {
                                "listChanged": False
                            }
                        },
                        "serverInfo": {
                            "name": ServerConfig.SERVER_NAME,
                            "version": "1.0.0"
                        }
                    }
                },
                media_type="application/json"
            )
        
        else:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                },
                media_type="application/json"
            )
    
    except Exception as e:
        import traceback
        error_msg = str(e)
        traceback.print_exc()
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": body.get("id") if body else None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {error_msg}"
                }
            },
            media_type="application/json"
        )


@app.get("/sse")
async def sse_endpoint(
    request: Request,
    _: bool = Depends(verify_auth)
):
    """SSE 端點，用於 MCP 協議的 Server-Sent Events 傳輸"""
    
    async def event_generator():
        """生成 SSE 事件"""
        try:
            # 發送初始化消息
            yield {
                "event": "message",
                "data": json.dumps({
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {}
                })
            }
            
            # 保持連接開啟，等待客戶端消息
            # 注意：實際的 MCP SSE 實現需要雙向通信
            # 這裡提供基本框架，完整實現需要 WebSocket 或額外的 POST 端點
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1)
                # 發送心跳
                yield {
                    "event": "ping",
                    "data": json.dumps({"type": "ping"})
                }
        
        except asyncio.CancelledError:
            pass
    
    return EventSourceResponse(event_generator())


async def main_stdio():
    """主函數 - stdio 模式"""
    # 使用 stdio 傳輸運行服務器
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


async def main_http():
    """主函數 - HTTP 模式"""
    print(f"MCP HTTP server 執行中")
    print(f"監聽地址: http://{ServerConfig.HOST}:{ServerConfig.PORT}")
    print(f"健康檢查: http://{ServerConfig.HOST}:{ServerConfig.PORT}/health")
    print(f"SSE 端點: http://{ServerConfig.HOST}:{ServerConfig.PORT}/sse")
    print(f"MCP 消息端點: http://{ServerConfig.HOST}:{ServerConfig.PORT}/messages")
    print(f"流式模式: 支援 (使用 X-Stream-Response: true header 或 params.stream: true)")
    
    if ServerConfig.REQUIRE_AUTH and ServerConfig.API_KEY:
        print(f"認證已啟用: API Key 認證")
    else:
        print(f"認證: 未啟用")
    
    config = uvicorn.Config(
        app,
        host=ServerConfig.HOST,
        port=ServerConfig.PORT,
        log_level="info"
    )
    server_instance = uvicorn.Server(config)
    await server_instance.serve()


if __name__ == "__main__":
    # 根據環境變數決定使用哪種模式
    # 如果設定了 MCP_MODE=stdio，使用 stdio 模式
    # 否則使用 HTTP 模式
    mode = os.getenv("MCP_MODE", "stdio").lower()
    
    if mode == "stdio":
        print("MCP server 執行中 (stdio 模式)")
        asyncio.run(main_stdio())
    else:
        asyncio.run(main_http())
