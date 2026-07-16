#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop hook（確定性版）：本回合呼叫過 codegraph 的 find_entry/trace、
卻沒呼叫 remember/remember_fact/learn 時，攔截一次提醒沉澱記憶。

設計原則：
- 不依賴模型自覺——直接解析 transcript JSONL 的 tool_use 名稱，確定性判斷
- stop_hook_active=True（已因本 hook 續跑過一次）→ 直接放行，避免無限迴圈
- find_entry 結果已出現「⚡ 記憶命中」→ 放行（記憶已存在，無需重複沉澱）
- 判斷不了「是否已確認出結論」→ 交給被攔下的 Claude 自行判斷：
  有結論就補記，沒結論就直接再結束（第二次 Stop 會因 stop_hook_active 放行）
"""
import json
import os
import sys

LOOKUP_SUFFIXES = ("__find_entry", "__trace")
SAVE_SUFFIXES = ("__remember", "__remember_fact", "__learn")
MEMORY_HIT_MARK = "⚡ 記憶命中"

BLOCK_REASON = (
    "【codegraph 記憶迴路檢查】本回合呼叫過 find_entry/trace 查程式入口，"
    "但尚未呼叫任何記憶工具。請現在判斷：\n"
    "1. 若已確認「問題 → 程式入口」→ 呼叫 remember（find_entry 未命中時回傳的 "
    "pending_id 可直接帶入，一行完成）\n"
    "2. 若確認了系統運作事實（排程方式、觸發時機、資料流、設計行為）→ 呼叫 remember_fact\n"
    "3. 若歸納出一整個新領域 → 呼叫 learn\n"
    "4. 若本次查詢尚無結論、或使用者訂正了先前結論但已存入錯誤記憶（需在回覆中"
    "告知該筆記憶 ID 供標記 rejected）→ 處理後即可正常結束\n"
    "此提醒每回合最多出現一次。"
)


def _iter_tool_items(obj):
    """從 transcript 單行 JSON 物件取出 message.content 內的項目。"""
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict):
            yield item


def _result_text(item):
    """tool_result 的文字內容（可能是字串或 [{type:text,...}] 列表）。"""
    c = item.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:
        return
    if data.get("stop_hook_active"):
        return  # 已提醒過一次，放行
    path = data.get("transcript_path") or ""
    if not path or not os.path.exists(path):
        return

    lookup_used = False
    memory_saved = False
    memory_already_hit = False

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # 快速預篩：整行連 codegraph 字樣都沒有就跳過，避免逐行 json.loads
            if "codegraph" not in line and MEMORY_HIT_MARK not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            for item in _iter_tool_items(obj):
                t = item.get("type")
                if t == "tool_use":
                    name = item.get("name", "")
                    if "codegraph" not in name:
                        continue
                    if name.endswith(LOOKUP_SUFFIXES):
                        lookup_used = True
                    elif name.endswith(SAVE_SUFFIXES):
                        memory_saved = True
                elif t == "tool_result":
                    if MEMORY_HIT_MARK in _result_text(item):
                        memory_already_hit = True

    if lookup_used and not memory_saved and not memory_already_hit:
        print(json.dumps({"decision": "block", "reason": BLOCK_REASON},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
