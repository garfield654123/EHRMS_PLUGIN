# -*- coding: utf-8 -*-
"""工作筆記：kb_note（寫入 HRMS_MEMORY_NOTE）

SPEC.md 決策 17：維運單常常是 AI 的答案能解決問題就關單，沒有人走到「確認結論、寫入記憶」那一步。
工作筆記讓 AI 不必等確認，就把這次處理中「知識庫以外」得到的結論留下來：
- finding   ：查問題過程中另外查出來的新結論（例如去看程式碼得到的）
- correction：使用者對 AI 的修正
結論完全來自知識庫查詢結果的不用寫（知識庫裡本來就有）。

工作筆記不是正式記憶：這裡只新增，沒有任何讀取的程式，查詢工具（kb_search／kb_get）查不到。
之後由審核 agent 確認結論正確才寫進 HRMS_MEMORY；處理結果欄位（STATUS 等）不由這裡寫，用資料表的預設值 open。

不做重複檢查，也不與既有記憶比對：筆記的用途是先累積，判斷留給審核。
"""
import anyio

from .dispatch import ToolResult
from .tools_core import registry
from . import tools_query as tq  # noqa: F401  確保 servers/MEMORY 已加進路徑

import pathnorm  # noqa: E402

KINDS = ("finding", "correction")
KIND_LABELS = {"finding": "新結論", "correction": "使用者的修正"}
LIMITS = (("topic", 100), ("content", 4000), ("evidence", 2000), ("keywords", 300),
          ("entry_path", 500), ("entry_method", 300), ("ref_key", 50))
FIELDS = ("kind",) + tuple(f for f, _ in LIMITS)


def _reject(message):
    return ToolResult(f"⚠️ {message}", write_outcome="rejected", error=message)


@registry.tool(
    "kb_note",
    "寫一筆工作筆記：把這次處理中「知識庫以外」得到的結論留下來，不需要先向使用者確認結論。\n"
    "什麼時候要寫：① 查問題過程中另外查出來的新結論，例如去看程式碼、查資料表結構、實際測試得到的（kind=finding）；"
    "② 使用者糾正了你的說法或補充了你不知道的事（kind=correction）。回覆使用者之前寫，一個結論一筆。\n"
    "什麼不用寫：結論完全來自 kb_search／kb_get 查詢結果的（知識庫裡本來就有）；已經用 remember 寫入的；"
    "沒有新結論時不用寫，也不必向使用者說明。\n"
    "content 寫結論本身（是什麼、在什麼條件下成立），evidence 寫依據：finding 寫程式位置或查證方式，"
    "correction 寫使用者的原話。不要寫客戶的實際資料（公司名稱、統編、員工姓名、帳號、薪資等），改用一般化的描述。\n"
    "工作筆記不是正式記憶，查詢工具查不到；之後審核確認正確才會升級成團隊記憶。"
    "已確認且跨案件可通用的結論仍用 remember；要指出既有知識有錯用 kb_flag。",
    {"type": "object",
     "properties": {
         "kind": {"type": "string", "enum": list(KINDS),
                  "description": "finding＝知識庫以外查出來的新結論；correction＝使用者對 AI 的修正"},
         "topic": {"type": "string", "description": "主題短標題（100 字內）"},
         "content": {"type": "string", "description": "結論（4000 字內）"},
         "evidence": {"type": "string", "description": "依據：程式位置、查證方式，或使用者的原話（2000 字內）；盡量填"},
         "keywords": {"type": "string", "description": "關鍵字，逗號分隔（可選）"},
         "entry_path": {"type": "string", "description": "程式入口的 repo 相對路徑；看程式碼得到的結論才填"},
         "entry_method": {"type": "string", "description": "入口函式（可選）"},
         "ref_key": {"type": "string", "description": "這次處理的 Jira 單號；對話有提到才填"},
     },
     "required": ["kind", "topic", "content"]})
async def kb_note(call, args):
    a = {k: str(args.get(k) or "").strip() for k in FIELDS}
    a["ref_key"] = a["ref_key"] or str(args.get("jira_key") or "").strip()
    if a["kind"] not in KINDS:
        return _reject("kind 只能是 finding（知識庫以外查出來的新結論）或 correction（使用者對 AI 的修正）。")
    if not a["topic"] or not a["content"]:
        return _reject("topic 與 content 為必填。")
    if a["entry_path"]:
        # 能轉成 repo 相對路徑就轉；轉不了（不在已知 repo 底下的絕對路徑）照原樣留著，筆記不因此被擋下
        normalized, err = pathnorm.normalize(a["entry_path"])
        if not err:
            a["entry_path"] = normalized
    for field, limit in LIMITS:
        if len(a[field]) > limit:
            return _reject(f"{field} 超過 {limit} 字（目前 {len(a[field])} 字），請精簡後再寫入。")

    created_by = f"{call.identity.name}(AI)"
    row = {"NOTE_KIND": a["kind"], "TOPIC": a["topic"], "CONTENT": a["content"],
           "EVIDENCE": a["evidence"] or None, "KEYWORDS": a["keywords"] or None,
           "ENTRY_PATH": a["entry_path"] or None, "ENTRY_METHOD": a["entry_method"] or None,
           "REF_KEY": a["ref_key"] or None, "ORIGIN": call.ctx.origin,
           "SESSION_ID": (call.ctx.session_id or "")[:36] or None, "CREATED_BY": created_by}
    try:
        note_id = await anyio.to_thread.run_sync(call.services.store.add_note, row)
    except Exception as e:
        return ToolResult(f"⚠️ 工作筆記寫入失敗：{type(e).__name__}: {e}", write_outcome="failed",
                          jira_key=a["ref_key"] or None, error=f"{type(e).__name__}: {e}")
    return ToolResult(
        f"📝 已寫入工作筆記 note#{note_id}【{a['topic']}】（{KIND_LABELS[a['kind']]}；記錄者 {created_by}）。"
        f"工作筆記不是正式記憶、查詢不會查到，之後審核確認正確才會升級成團隊記憶。",
        write_outcome="created", write_ref=f"note#{note_id}", jira_key=a["ref_key"] or None)
