# -*- coding: utf-8 -*-
"""使用紀錄：每次工具呼叫一筆（HRMS_KB_USAGE_LOG）

- 紀錄失敗不影響工具回應：寫不進資料庫時，先附加到本機暫存檔（一行一筆 JSON）
- 之後每次寫入成功，或伺服器啟動時，把暫存檔裡的補送進資料庫
- 暫存檔保留原本的 EVENT_TIME，所以補送後時間仍然正確（CREATED_AT 才是實際寫入時間）
"""
import json
import logging
import os
import threading

import anyio

logger = logging.getLogger("kb_online.usage")

_LIMITS = {"USER_NAME": 100, "USER_EMAIL": 200, "CLIENT_ADDR": 64, "SESSION_ID": 36, "CLIENT_NAME": 100,
           "TOOL_NAME": 50, "QUERY_TEXT": 2000, "JIRA_KEY": 50, "WRITE_REF": 50, "ERROR_MSG": 1000}


def _fit(row):
    """字串欄位截到資料表的長度上限，避免整筆因為過長寫不進去。"""
    out = dict(row)
    for col, limit in _LIMITS.items():
        if isinstance(out.get(col), str) and len(out[col]) > limit:
            out[col] = out[col][:limit]
    return out


class UsageLogger:
    def __init__(self, store, spool_path):
        self.store = store
        self.spool_path = str(spool_path)
        self._spool_lock = threading.Lock()

    async def record(self, row):
        await anyio.to_thread.run_sync(self.record_sync, row)

    def record_sync(self, row):
        row = _fit(row)
        try:
            self.store.insert_usage(row)
        except Exception as e:
            logger.warning("使用紀錄寫入失敗，改放暫存檔：%s", e)
            self._spool(row)
            return
        if os.path.exists(self.spool_path):
            self.flush_spool()

    def _spool(self, row):
        try:
            with self._spool_lock:
                os.makedirs(os.path.dirname(self.spool_path) or ".", exist_ok=True)
                with open(self.spool_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:   # 連暫存檔都寫不了也不能讓工具失敗
            logger.error("使用紀錄暫存檔寫入失敗，這筆紀錄遺失：%s", e)

    def flush_spool(self):
        """把暫存檔補送進資料庫。回傳補送成功的筆數；送不進去的留在暫存檔。"""
        with self._spool_lock:
            if not os.path.exists(self.spool_path):
                return 0
            with open(self.spool_path, encoding="utf-8") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            sent, remaining = 0, []
            for i, line in enumerate(lines):
                try:
                    row = json.loads(line)
                except ValueError:
                    logger.error("暫存檔有一行不是 JSON，略過：%s", line[:200])
                    continue
                try:
                    self.store.insert_usage(row)
                    sent += 1
                except Exception:
                    remaining = lines[i:]   # 資料庫還是連不上，剩下的保留
                    break
            if remaining:
                with open(self.spool_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(remaining) + "\n")
            else:
                os.remove(self.spool_path)
            return sent
