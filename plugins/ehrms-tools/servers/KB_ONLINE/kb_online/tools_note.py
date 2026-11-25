# -*- coding: utf-8 -*-
"""工作筆記：kb_note（寫入 HRMS_MEMORY_NOTE）

SPEC.md 決策 17、23：維運單常常是 AI 的答案能解決問題就關單，沒有人走到「確認結論、寫入記憶」那一步。
工作筆記讓 AI 不必等確認，就把這次處理留下來。寫不寫看「做了什麼動作」，不看「有沒有結論」：
這輪只要讀過程式碼、查過資料表結構、讀過 Jira 單，或被使用者糾正，就寫；對錯不論，寫錯由審核處理。
- process   ：過程紀錄（做了什麼、查到什麼、卡在哪），沒有結論也能寫；kind 沒給時的預設
- finding   ：知識庫以外查出來的結論（例如去看程式碼得到的）
- correction：使用者對 AI 的修正
只查了知識庫、沒有做上述動作的不用寫（知識庫裡本來就有）。

工作筆記不是正式記憶：這裡只新增，沒有任何讀取的程式，查詢工具（kb_search／kb_get）查不到。
之後由審核 agent 確認結論正確才寫進 HRMS_MEMORY；處理結果欄位（STATUS 等）不由這裡寫，用資料表的預設值 open。

不做重複檢查，也不與既有記憶比對：筆記的用途是先累積，判斷留給審核。
kb_search 的 note 參數也經由這裡的 save() 寫入（查詢當下順手記一筆過程紀錄）。
"""
import anyio

from .dispatch import ToolResult
from .tools_core import registry
from . import tools_query as tq  # noqa: F401  確保 servers/MEMORY 已加進路徑

import pathnorm  # noqa: E402

KINDS = ("process", "finding", "correction")
DEFAULT_KIND = "process"
KIND_LABELS = {"process": "過程紀錄", "finding": "結論", "correction": "使用者的修正"}
LIMITS = (("topic", 100), ("content", 4000), ("evidence", 2000), ("keywords", 300),
          ("entry_path", 500), ("entry_method", 300), ("ref_key", 50))
FIELDS = ("kind",) + tuple(f for f, _ in LIMITS)


def _reject(message):
    return ToolResult(f"⚠️ {message}", write_outcome="rejected", error=message)


async def save(call, a):
    """寫一筆工作筆記，回傳編號；a 的鍵見 FIELDS，內容已由呼叫端檢查過。寫入失敗時丟出例外。"""
    row = {"NOTE_KIND": a["kind"], "TOPIC": a["topic"], "CONTENT": a["content"],
           "EVIDENCE": a.get("evidence") or None, "KEYWORDS": a.get("keywords") or None,
           "ENTRY_PATH": a.get("entry_path") or None, "ENTRY_METHOD": a.get("entry_method") or None,
           "REF_KEY": a.get("ref_key") or None, "ORIGIN": call.ctx.origin,
           "SESSION_ID": (call.ctx.session_id or "")[:36] or None, "CREATED_BY": f"{call.identity.name}(AI)"}
    note_id = await anyio.to_thread.run_sync(call.services.store.add_note, row)
    call.services.activity(call.ctx.session_id)["notes"] += 1
    return note_id


@registry.tool(
    "kb_note",
    "寫一筆工作筆記，記下這次處理的過程或結論。**不需要確認，對錯不論，寫錯會由審核處理**，所以不用猶豫。\n"
    "什麼時候寫（看做了什麼動作，不用判斷有沒有結論）：這輪只要 ① 讀過程式碼、② 查過資料表結構、③ 讀過 Jira 單，"
    "或 ④ 被使用者糾正或補充，就寫。查到就當場寫，不要等到回覆前；一件事一筆，同一輪可以寫很多筆。\n"
    "只要填 topic 與 content。kind 可不填（預設 process）：process＝過程紀錄（做了什麼、查到什麼、卡在哪、目前的假設），"
    "沒有結論也能寫；finding＝查出來的結論；correction＝使用者的修正（evidence 寫使用者的原話）。\n"
    "唯一不用寫的情況：這輪只查了知識庫（kb_search／kb_get），沒有做上面任何一個動作。\n"
    "不要寫客戶的實際資料（公司名稱、統編、員工姓名、帳號、薪資等），改用一般化的描述。\n"
    "工作筆記不是正式記憶，查詢工具查不到；審核確認正確才會升級成團隊記憶。remember 才需要「結論已確認」，兩者不要混在一起。",
    {"type": "object",
     "properties": {
         "topic": {"type": "string", "description": "主題短標題（100 字內）"},
         "content": {"type": "string", "description": "內容（4000 字內）：做了什麼、查到什麼、卡在哪，或結論是什麼"},
         "kind": {"type": "string", "enum": list(KINDS),
                  "description": "可不填，預設 process（過程紀錄）；finding＝查出來的結論；correction＝使用者的修正"},
         "evidence": {"type": "string", "description": "依據：程式位置、查證方式，或使用者的原話（可選，2000 字內）"},
         "keywords": {"type": "string", "description": "關鍵字，逗號分隔（可選）"},
         "entry_path": {"type": "string", "description": "程式入口的 repo 相對路徑（可選）"},
         "entry_method": {"type": "string", "description": "入口函式（可選）"},
         "ref_key": {"type": "string", "description": "這次處理的 Jira 單號；對話有提到才填"},
     },
     "required": ["topic", "content"]})
async def kb_note(call, args):
    a = {k: str(args.get(k) or "").strip() for k in FIELDS}
    a["kind"] = a["kind"] or DEFAULT_KIND
    a["ref_key"] = a["ref_key"] or str(args.get("jira_key") or "").strip()
    if a["kind"] not in KINDS:
        return _reject("kind 只能是 process（過程紀錄，預設）、finding（查出來的結論）或 correction（使用者的修正）；不確定就不要填。")
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

    try:
        note_id = await save(call, a)
    except Exception as e:
        return ToolResult(f"⚠️ 工作筆記寫入失敗：{type(e).__name__}: {e}", write_outcome="failed",
                          jira_key=a["ref_key"] or None, error=f"{type(e).__name__}: {e}")
    return ToolResult(
        f"📝 已寫入工作筆記 note#{note_id}【{a['topic']}】（{KIND_LABELS[a['kind']]}；記錄者 {call.identity.name}(AI)）。"
        f"之後每查出一件事就再記一筆。工作筆記不是正式記憶、查詢不會查到，審核確認正確才會升級。",
        write_outcome="created", write_ref=f"note#{note_id}", jira_key=a["ref_key"] or None)
