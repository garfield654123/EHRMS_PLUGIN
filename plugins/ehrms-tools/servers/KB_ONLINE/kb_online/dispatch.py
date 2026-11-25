# -*- coding: utf-8 -*-
"""工具的單一入口：身分把關 → 執行 → 記錄

所有工具都經過 Dispatcher.call()，所以身分檢查與使用紀錄只寫在這一處。
這個模組不依賴 MCP 的連線層，可以直接用假的 CallContext 測試。

新增工具：
    @registry.tool("kb_search", "說明", {...JSON schema...})
    async def kb_search(call, args):
        return ToolResult("回覆文字", refs=[{"type": "faq", "id": 48, "score": 0.71}], query_text=args["query"])
"""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .identity import IdentityError

_JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")

NEED_IDENTITY = (
    "尚未確認使用者身分，知識庫暫不提供服務。\n"
    "請先詢問使用者的公司信箱（不可自行猜測或代填），再呼叫 confirm_identity(email=信箱)。"
    "確認一次後，同一台電腦之後不用再確認。"
)


@dataclass
class CallContext:
    """一次工具呼叫的來源資訊，由連線層填入。"""
    client_addr: str
    session_id: str
    client_name: str = ""
    origin: str = "chat"          # chat＝真人對話；schedule／web 之後接上時由連線層指定


@dataclass
class ToolResult:
    text: str
    refs: list = field(default_factory=list)   # 回傳的知識：[{type, id, score}]，只記編號不記全文
    no_result: bool | None = None              # 查詢類：True＝查無資料
    write_outcome: str | None = None           # 寫入類：created／duplicate／candidate／flagged／rejected／failed
    write_ref: str | None = None               # 例：memory#231、candidate#12、flag#7
    query_text: str | None = None
    jira_key: str | None = None
    error: str | None = None


@dataclass
class Call:
    """交給工具處理函式的資訊。"""
    ctx: CallContext
    identity: object | None     # 已確認的 Identity；不需要身分的工具可能是 None
    services: object            # Dispatcher 本身，工具可以取用 identity_service 等


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self, name, description, schema, requires_identity=True):
        def deco(fn):
            self.tools[name] = {"name": name, "description": description, "schema": schema,
                                "requires_identity": requires_identity, "handler": fn}
            return fn
        return deco


def find_jira_key(arguments):
    """參數裡有明確的單號欄位就用它，否則從文字參數裡找第一個像單號的字串。"""
    for k in ("jira_key", "ref_key"):
        v = arguments.get(k)
        if isinstance(v, str) and _JIRA_KEY_RE.search(v.upper()):
            return _JIRA_KEY_RE.search(v.upper()).group(0)
    for v in arguments.values():
        if isinstance(v, str):
            m = _JIRA_KEY_RE.search(v)
            if m:
                return m.group(0)
    return None


class Dispatcher:
    def __init__(self, registry, identity_service, usage_logger, server_version, search_version, store=None):
        self.registry = registry
        self.store = store if store is not None else identity_service.store
        self.identity_service = identity_service
        self.usage = usage_logger
        self.server_version, self.search_version = server_version, search_version
        self._seq = {}   # session_id → 已呼叫次數
        self._activity = {}   # session_id → {lookups: 查詢次數, notes: 已寫的工作筆記數}；只在記憶體，重啟歸零

    def _next_seq(self, session_id):
        if len(self._seq) > 20000:      # 連線很多時清掉舊的計數，避免無限成長
            self._seq.clear()
        self._seq[session_id] = self._seq.get(session_id, 0) + 1
        return self._seq[session_id]

    def activity(self, session_id):
        """這個連線到目前為止查了幾次、寫了幾筆工作筆記；查詢結果結尾的提醒用這個數字。"""
        if session_id not in self._activity and len(self._activity) > 20000:
            self._activity.clear()
        return self._activity.setdefault(session_id, {"lookups": 0, "notes": 0})

    async def call(self, ctx, name, arguments):
        """執行一次工具呼叫，回傳要給用戶端的文字。任何情況都會留下一筆使用紀錄。"""
        arguments = arguments or {}
        started, event_time = time.perf_counter(), datetime.now()
        seq = self._next_seq(ctx.session_id)
        identity, result = None, None
        try:
            identity = await self.identity_service.resolve(ctx.client_addr)
            spec = self.registry.tools.get(name)
            if spec is None:
                result = ToolResult(f"沒有這個工具：{name}", error="unknown tool")
            elif spec["requires_identity"] and identity is None:
                result = ToolResult(NEED_IDENTITY, error="identity required")
            else:
                result = await spec["handler"](Call(ctx, identity, self), arguments)
                # confirm_identity 成功後，這一筆紀錄就記成剛確認的使用者
                identity = identity or await self.identity_service.resolve(ctx.client_addr)
        except IdentityError as e:
            result = ToolResult(f"⚠️ {e}", error=str(e))
        except Exception as e:
            result = ToolResult(f"⚠️ 工具執行失敗：{type(e).__name__}: {e}", error=f"{type(e).__name__}: {e}")

        await self._record(ctx, name, arguments, identity, result, seq, event_time,
                           int((time.perf_counter() - started) * 1000))
        return result.text

    async def _record(self, ctx, name, arguments, identity, result, seq, event_time, elapsed_ms):
        row = {
            "EVENT_TIME": event_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "USER_NAME": identity.name if identity else "unknown",
            "USER_EMAIL": identity.email if identity else None,
            "IDENTITY_SOURCE": identity.source if identity else "none",
            "CLIENT_ADDR": ctx.client_addr,
            "SESSION_ID": ctx.session_id,
            "CALL_SEQ": seq,
            "ORIGIN": ctx.origin,
            "CLIENT_NAME": ctx.client_name or None,
            "TOOL_NAME": name,
            "ARGUMENTS": json.dumps(arguments, ensure_ascii=False),
            "QUERY_TEXT": result.query_text,
            "JIRA_KEY": result.jira_key or find_jira_key(arguments),
            "RESULT_REFS": json.dumps(result.refs, ensure_ascii=False) if result.refs else None,
            "RESULT_COUNT": len(result.refs) if result.refs or result.no_result is not None else None,
            "NO_RESULT": None if result.no_result is None else int(result.no_result),
            "WRITE_OUTCOME": result.write_outcome,
            "WRITE_REF": result.write_ref,
            "ELAPSED_MS": elapsed_ms,
            "ERROR_MSG": result.error,
            "SERVER_VERSION": self.server_version,
            "SEARCH_VERSION": self.search_version,
        }
        try:
            await self.usage.record(row)
        except Exception:   # 紀錄本身出錯不能影響回應；UsageLogger 內部已盡量處理
            pass
