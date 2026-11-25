# -*- coding: utf-8 -*-
"""線上知識庫 MCP 查詢工具（kb_search／kb_get）的單元測試：資料庫存取以替身取代。"""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "KB_ONLINE"))

from kb_online import tools_query as tq  # noqa: E402
from kb_online.dispatch import CallContext, Dispatcher  # noqa: E402
from kb_online.identity import IdentityService  # noqa: E402
from kb_online.store import SqliteStore  # noqa: E402
from kb_online.tools_core import registry  # noqa: E402
from kb_online.usage_log import UsageLogger  # noqa: E402

CARD = (Path(__file__).resolve().parent / "fixtures" / "wiki_card.md").read_text(encoding="utf-8")


def mem(id, type, topic, content, **kw):
    row = {"id": id, "type": type, "topic": topic, "content": content, "keywords": "", "func_path": "",
           "entry_path": "", "entry_method": "", "supersedes": None, "ref_key": "", "source": "",
           "created_by": "amy.lin(AI)", "created_at": "2026-11-01", "hits": 0, "last_hit": "",
           "review": "pending", "meta": {}}
    row.update(kw)
    return row


MEMORIES = [
    mem(1, "System", "特休時數不足的判斷", "員工請特休跳出時數不足，要先看特休產生批次是否已執行，再看結轉設定。",
        keywords="特休,時數不足"),
    mem(2, "Engineer", "特休時數檢核入口", "請假申請時由 CheckSpecialHours 檢核特休剩餘時數。",
        keywords="特休,時數不足,CheckSpecialHours", entry_path="VB/EHRMS/Leave.cls", entry_method="CheckSpecialHours"),
    mem(3, "System", "特休時數不足的判斷（舊）", "特休時數不足一律是結轉設定錯誤。", keywords="特休,時數不足"),
    mem(4, "System", "特休時數不足的判斷（新）", "特休時數不足要先看產生批次。", keywords="特休,時數不足", supersedes=3),
    mem(5, "System", "薪資結構建立順序", "員工資料建立後需先建薪資結構才能算薪。", keywords="薪資結構"),
]
JIRAS = [
    {"id": 10, "key": "EHRMSONE-31192", "kind": "Crisis", "title": "特休時數不足無法請假",
     "root_cause": "特休產生批次未執行", "resolution": "補跑特休產生批次", "changed_files": "",
     "keywords": "特休,時數不足", "supersedes": None, "created_by": "jira-auto-scheduler(AI)",
     "created_at": "2026-05-10", "review": "pending", "meta": {}},
]
REFDATA = {
    "faq": [{"id": 48, "question": "請特休時顯示時數不足怎麼辦", "answer": "請確認特休是否已產生。",
             "category": "M2", "keywords": "特休", "author": "顧問"}],
    "sop": [{"id": 3, "title": "客戶要求補特休時數", "description": "特休時數不足時的處理", "content":
             "# 客戶要求補特休時數\n\n## 適用情境\n特休時數不足。", "category": "M2", "keywords": "特休", "author": "amy.lin(AI)"}],
    "task": [{"id": 7, "task_name": "補特休時數", "scenario": "特休時數不足時補上時數", "sql_template":
              "BEGIN TRAN\nUPDATE X SET H = 8 WHERE ID = 1\n-- COMMIT", "params": "", "affected_table": "",
              "risk_level": "未分級", "precautions": "先確認未結算", "keywords": "M2 資料修改", "author": "顧問"}],
    "bulletin": [
        {"id": 21, "title": "2026/10 版更：特休時數不足誤判", "content": "特休時數不足誤判已於 5.2.1 修正。",
         "category": "版本更新", "keywords": "", "created": "2026-10-20"},
        {"id": 22, "title": "系統維護通知", "content": "週六凌晨維護。", "category": "維護", "keywords": "",
         "created": "2026-10-01"},
    ],
}
WIKI = [{"wiki_id": 1, "git_path": "wiki/出勤管理/打卡.md", "doc_type": "article", "topic_code": "CARD",
         "customer_code": "", "title": "打卡（刷卡）", "keywords": "打卡,刷卡,刷卡異常", "description": "",
         "content": CARD, "git_commit": "draft", "open_review_count": 0}]

CTX = CallContext(client_addr="10.1.2.3", session_id="sess-q")


class QueryBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SqliteStore(os.path.join(self.tmp.name, "kb.db"))
        self.addCleanup(self.store.close)
        self.store.add_binding("10.1.2.3", "amy.lin@example.com", "amy.lin", "acc-1")
        self.d = Dispatcher(registry, IdentityService(self.store, None),
                            UsageLogger(self.store, os.path.join(self.tmp.name, "spool.jsonl")), "9.9.9", "kw-test")
        patches = [
            mock.patch.object(tq.mdb, "db_enabled", return_value=True),
            mock.patch.object(tq.mdb, "load_all", return_value=MEMORIES),
            mock.patch.object(tq.jira_db, "load_all", return_value=JIRAS),
            mock.patch.object(tq.refdata_db, "load_all", side_effect=lambda key: REFDATA[key]),
            mock.patch.object(tq.wiki_db, "load_all", return_value=WIKI),
            mock.patch.object(tq.wiki_db, "db_enabled", return_value=True),
            mock.patch.object(tq.mdb, "bump_hits", side_effect=AssertionError("線上版不該回寫引用次數")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def last_log(self):
        return self.store.usage_rows()[-1]

    def flag(self, target_type, target_id, reason, status="open"):
        with self.store._lock:
            self.store._conn.execute(
                "INSERT INTO HRMS_KB_FLAG (TARGET_TYPE, TARGET_ID, REASON, FLAG_SOURCE, CREATED_BY, STATUS) "
                "VALUES (?, ?, ?, 'ai', 'bob.wu(AI)', ?)", (target_type, str(target_id), reason, status))
            self.store._conn.commit()


class SearchTests(QueryBase):
    async def test_searches_every_source_and_logs_refs(self):
        text = await self.d.call(CTX, "kb_search", {"query": "EHRMSONE-31192 員工請特休跳出時數不足"})
        for token in ("[memory#1]", "[memory#2]", "[memory#4]", "[faq#48]", "[sop#3]", "[task#7]",
                      "[jira#10]", "[bulletin#21]", "kb_get", "不可自動執行"):
            self.assertIn(token, text)
        self.assertNotIn("[memory#3]", text)      # 已被 #4 取代
        self.assertNotIn("[memory#5]", text)      # 不相關
        self.assertNotIn("[bulletin#22]", text)
        self.assertIn("VB/EHRMS/Leave.cls", text)
        self.assertNotIn("UPDATE X SET", text)    # 摘要不帶 SQL，要用 kb_get 才看得到
        self.assertIn("查無相符：Wiki", text)

        log = self.last_log()
        refs = json.loads(log["RESULT_REFS"])
        self.assertEqual({(r["type"], r["id"]) for r in refs},
                         {("memory", 1), ("memory", 2), ("memory", 4), ("faq", 48), ("sop", 3), ("task", 7),
                          ("jira", 10), ("bulletin", 21)})
        self.assertTrue(all(0 < r["score"] <= 1 for r in refs))
        self.assertEqual((log["NO_RESULT"], log["RESULT_COUNT"], log["JIRA_KEY"], log["USER_NAME"]),
                         (0, 8, "EHRMSONE-31192", "amy.lin"))
        self.assertEqual(log["QUERY_TEXT"], "EHRMSONE-31192 員工請特休跳出時數不足")

    async def test_wiki_hit_uses_topic_code_and_lists_rules(self):
        text = await self.d.call(CTX, "kb_search", {"query": "打卡 刷卡異常", "sources": ["wiki"]})
        self.assertIn("[wiki#CARD]", text)
        self.assertIn("命中：R", text)
        self.assertEqual(json.loads(self.last_log()["RESULT_REFS"])[0]["type"], "wiki")
        self.assertNotIn("## FAQ", text)          # 只查指定來源

    async def test_no_result_is_recorded(self):
        text = await self.d.call(CTX, "kb_search", {"query": "火星基地電力調度"})
        self.assertIn("查無相符", text)
        log = self.last_log()
        self.assertEqual((log["NO_RESULT"], log["RESULT_COUNT"], log["RESULT_REFS"]), (1, 0, None))

    async def test_top_k_limits_each_source(self):
        text = await self.d.call(CTX, "kb_search", {"query": "特休時數不足", "sources": ["memory"], "top_k": 1})
        # System 與 Engineer 各取 1 筆
        self.assertEqual(text.count("[memory#"), 2)

    async def test_failed_source_is_reported_not_hidden(self):
        with mock.patch.object(tq.jira_db, "load_all", side_effect=RuntimeError("db down")):
            text = await self.d.call(CTX, "kb_search", {"query": "特休時數不足"})
        self.assertIn("查詢失敗，不代表沒有資料", text)
        self.assertIn("Jira", text.split("查詢失敗")[1])
        self.assertIn("[faq#48]", text)           # 其他來源照常回傳
        self.assertIn("RuntimeError", self.last_log()["ERROR_MSG"])

    async def test_version_bulletins_option_lists_all_version_notices(self):
        text = await self.d.call(CTX, "kb_search", {"query": "報表匯出亂碼", "sources": ["faq"],
                                                    "version_bulletins": True})
        self.assertIn("版更公告（共 1 筆", text)
        self.assertIn("5.2.1", text)
        refs = json.loads(self.last_log()["RESULT_REFS"])
        self.assertEqual(refs, [{"type": "bulletin", "id": 21, "score": None}])

    async def test_open_flags_are_shown_and_resolved_ones_are_not(self):
        self.flag("memory", 1, "與 EHRMSONE-34000 的結論不同")
        self.flag("faq", 48, "已處理的舊疑慮", status="dismissed")
        text = await self.d.call(CTX, "kb_search", {"query": "特休時數不足"})
        self.assertIn("⚠ 有疑慮：與 EHRMSONE-34000 的結論不同", text)
        self.assertNotIn("已處理的舊疑慮", text)

    async def test_short_query_and_missing_db(self):
        self.assertIn("過短", await self.d.call(CTX, "kb_search", {"query": "?"}))
        with mock.patch.object(tq.mdb, "db_enabled", return_value=False):
            self.assertIn("未設定知識庫資料庫", await self.d.call(CTX, "kb_search", {"query": "特休時數不足"}))
        self.assertEqual(self.last_log()["ERROR_MSG"], "db not configured")

    async def test_requires_identity(self):
        other = CallContext(client_addr="10.9.9.9", session_id="sess-x")
        self.assertIn("尚未確認使用者身分", await self.d.call(other, "kb_search", {"query": "特休時數不足"}))


class GetTests(QueryBase):
    async def test_memory_full_text_and_superseded_notice(self):
        text = await self.d.call(CTX, "kb_get", {"type": "memory", "id": "2"})
        self.assertIn("請假申請時由 CheckSpecialHours 檢核特休剩餘時數。", text)
        self.assertIn("VB/EHRMS/Leave.cls", text)
        self.assertEqual(json.loads(self.last_log()["RESULT_REFS"]), [{"type": "memory", "id": 2, "score": None}])
        old = await self.d.call(CTX, "kb_get", {"type": "memory", "id": "#3"})
        self.assertIn("已被新版本取代，請改讀 memory#4", old)

    async def test_task_includes_sql_and_disclaimer(self):
        text = await self.d.call(CTX, "kb_get", {"type": "task", "id": "7"})
        self.assertIn("UPDATE X SET H = 8 WHERE ID = 1", text)
        self.assertIn("AI 不可自動執行此 SQL", text)

    async def test_jira_by_key_or_record_id(self):
        by_key = await self.d.call(CTX, "kb_get", {"type": "jira", "id": "31192"})
        by_id = await self.d.call(CTX, "kb_get", {"type": "jira", "id": "10"})
        for text in (by_key, by_id):
            self.assertIn("補跑特休產生批次", text)
            self.assertIn("僅供參考", text)

    async def test_wiki_toc_then_rule(self):
        toc = await self.d.call(CTX, "kb_get", {"type": "wiki", "id": "CARD"})
        self.assertIn("規則：", toc)
        rule = await self.d.call(CTX, "kb_get", {"type": "wiki", "id": "CARD", "section": "R1"})
        self.assertIn("R1", rule)
        self.assertNotEqual(toc, rule)

    async def test_not_found_and_bad_arguments(self):
        self.assertIn("找不到 faq#999", await self.d.call(CTX, "kb_get", {"type": "faq", "id": "999"}))
        self.assertEqual(self.last_log()["NO_RESULT"], 1)
        self.assertIn("找不到 wiki#NOPE", await self.d.call(CTX, "kb_get", {"type": "wiki", "id": "NOPE"}))
        self.assertIn("type 只能是", await self.d.call(CTX, "kb_get", {"type": "secret", "id": "1"}))

    async def test_flag_shown_on_full_text(self):
        self.flag("faq", 48, "答案與 Wiki 特休頁 R4 不一致")
        text = await self.d.call(CTX, "kb_get", {"type": "faq", "id": "48"})
        self.assertIn("答案與 Wiki 特休頁 R4 不一致", text)

    async def test_custom_passthrough(self):
        with mock.patch.object(tq.cc, "lookup", return_value="客製查詢結果") as lookup:
            text = await self.d.call(CTX, "kb_get", {"type": "custom", "id": "23019591", "section": "Leave"})
        lookup.assert_called_once_with("23019591", "Leave")
        body, _, tail = text.partition("\n\n")                    # 內容原樣回傳，最後只多一段工作筆記提醒
        self.assertEqual(body, "客製查詢結果")
        self.assertIn("kb_note", tail)


if __name__ == "__main__":
    unittest.main()
