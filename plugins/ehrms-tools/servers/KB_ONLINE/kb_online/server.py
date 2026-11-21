#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EHRMS 線上知識庫 MCP（HTTP）

同事只要連這個網址就能使用知識庫，不用安裝 plugin；身分確認與使用紀錄都在伺服器端做。
設計依據：FAQ_web 的 memory_review/討論清單_線上知識庫MCP.md

啟動（在 plugins/ehrms-tools 目錄下）：
    py -3 servers/KB_ONLINE/run_server.py
用戶端連線網址：http://<主機>:<KB_PORT>/mcp
健康檢查：GET /health

連線層只做三件事：取出來源資訊（電腦位址、連線編號、用戶端名稱）、交給 Dispatcher、回傳文字。
"""
import contextlib
import logging
import uuid

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import config
from .dispatch import CallContext, Dispatcher
from .identity import IdentityService, JiraDirectory
from .store import make_store
from .tools_core import registry
from . import tools_query  # noqa: F401  匯入即註冊 kb_search、kb_get
from . import tools_write  # noqa: F401  匯入即註冊 remember、kb_flag
from .usage_log import UsageLogger

logger = logging.getLogger("kb_online")

INSTRUCTIONS = """\
EHRMS 線上知識庫。

身分：工具回覆「尚未確認使用者身分」時，先詢問使用者的公司信箱（不可猜測或代填），再呼叫 confirm_identity。
確認一次後同一台電腦不用再確認。每次工具呼叫都會留下使用紀錄（使用者、查詢內容、回傳的知識編號）。

查詢：先用 kb_search 一次查全部來源，再用 kb_get 讀要引用的那幾筆全文；不要只憑摘要下結論。
處理 Jira 單時把單號帶進 kb_search。回報的是錯誤或異常時，加 version_bulletins=true 比對已知版更問題。
來源互相矛盾時的優先順序：有程式碼證據的記憶 ＞ Wiki ＞ 人工 FAQ／公告／SOP／Task ＞ AI 撰寫的 FAQ／SOP／Task
＞ Jira 結案紀錄（僅供參考）。標示「有疑慮」的知識引用前要另外查證。
公司資料庫沒有客戶資料；Task 的 SQL 只是範本，須人工確認，不可自動執行。

寫回：結論確認後、回覆使用者前，判斷這次有沒有跨案件可通用的新知識；有就用 remember 寫入（先用 kb_search 確認
沒有相同內容，有對應 Jira 單要填 ref_key）。只寫已確認的結論，不寫推測，也不寫客戶的實際資料。
線上版不能取代既有知識：發現既有知識可能有錯時用 kb_flag 提出疑慮並附依據。寫入或標記後，在回覆中列出編號。
"""


def client_addr_of(request, trust_forwarded):
    if request is None:
        return "unknown"
    if trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def build_app(settings=None, store=None, directory=None):
    """組出 ASGI 應用程式。測試時可以傳入假的 store 與 directory。"""
    settings = settings or config.Settings()
    store = store or make_store(settings)
    directory = directory or JiraDirectory(settings.jira_base_url, settings.jira_email, settings.jira_token)
    usage = UsageLogger(store, settings.spool_path)
    dispatcher = Dispatcher(registry, IdentityService(store, directory, settings.email_domains), usage,
                            config.SERVER_VERSION, config.SEARCH_VERSION)

    server = Server("ehrms-kb", version=config.SERVER_VERSION, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools():
        return [Tool(name=t["name"], description=t["description"], inputSchema=t["schema"])
                for t in registry.tools.values()]

    @server.call_tool()
    async def call_tool(name, arguments):
        rc = server.request_context
        request = rc.request
        session_id = (request.headers.get("mcp-session-id") if request is not None else None) or uuid.uuid4().hex
        client = getattr(rc.session, "client_params", None)
        info = getattr(client, "clientInfo", None)
        client_name = f"{info.name} {info.version}".strip() if info else ""
        ctx = CallContext(client_addr=client_addr_of(request, settings.trust_forwarded),
                          session_id=session_id, client_name=client_name)
        return [TextContent(type="text", text=await dispatcher.call(ctx, name, arguments))]

    manager = StreamableHTTPSessionManager(
        app=server, json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=settings.allowed_hosts))

    async def mcp_endpoint(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    async def health(request):
        return JSONResponse({"status": "ok", "version": config.SERVER_VERSION, "store": settings.store,
                             "tools": sorted(registry.tools)})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        try:
            sent = usage.flush_spool()
            if sent:
                logger.info("啟動時補送了 %d 筆使用紀錄", sent)
        except Exception as e:
            logger.warning("啟動時補送使用紀錄失敗：%s", e)
        async with manager.run():
            yield

    inner = Starlette(routes=[Route("/health", health)], lifespan=lifespan)

    async def app(scope, receive, send):
        # /mcp 與 /mcp/ 都直接處理，不轉址：有些用戶端的 POST 不會跟著轉址走
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            await mcp_endpoint(scope, receive, send)
        else:
            await inner(scope, receive, send)

    app.dispatcher = dispatcher
    return app


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config.load_env_file()
    settings = config.Settings()
    logger.info("線上知識庫 MCP %s 啟動：http://%s:%s/mcp（資料存放：%s）",
                config.SERVER_VERSION, settings.host, settings.port, settings.store)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
