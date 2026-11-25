# -*- coding: utf-8 -*-
"""骨架階段的兩個工具：確認身分、查看服務狀態

知識查詢（kb_search／kb_get）、寫入（remember）、疑慮標記（kb_flag）、工作筆記（kb_note）、skill（skill_list／skill_get）
之後各自加在自己的模組，用同一個 registry 註冊。
"""
from .dispatch import Registry, ToolResult

registry = Registry()


@registry.tool(
    "confirm_identity",
    "確認使用者身分。第一次使用知識庫、或工具回覆「尚未確認使用者身分」時呼叫。"
    "email 必須是使用者本人親口提供的公司信箱，不可猜測、不可從其他地方代填。"
    "伺服器會比對 Jira 名單，成功後同一台電腦之後不用再確認。",
    {"type": "object",
     "properties": {"email": {"type": "string", "description": "使用者提供的公司信箱"}},
     "required": ["email"]},
    requires_identity=False)
async def confirm_identity(call, args):
    ident = await call.services.identity_service.confirm(call.ctx.client_addr, args.get("email", ""))
    return ToolResult(f"✅ 已確認身分：{ident.name}（{ident.email}）。這台電腦之後不用再確認。")


@registry.tool(
    "kb_info",
    "查看知識庫服務狀態：伺服器版本、目前辨識到的使用者、可用的工具。不需要先確認身分。",
    {"type": "object", "properties": {}},
    requires_identity=False)
async def kb_info(call, args):
    d = call.services
    who = f"{call.identity.name}（{call.identity.email}）" if call.identity else "尚未確認（請呼叫 confirm_identity）"
    tools = "、".join(sorted(d.registry.tools))
    return ToolResult(
        f"EHRMS 線上知識庫 MCP\n- 伺服器版本：{d.server_version}\n- 檢索方式版本：{d.search_version}\n"
        f"- 目前使用者：{who}\n- 可用工具：{tools}")
