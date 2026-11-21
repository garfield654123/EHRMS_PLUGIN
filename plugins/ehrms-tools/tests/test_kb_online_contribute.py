# -*- coding: utf-8 -*-
"""線上知識庫 MCP 填寫工具（kb_contribute）的單元測試：資料表的寫入以替身取代。"""
from pathlib import Path
import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_kb_online_query as base  # noqa: E402
from kb_online import tools_contribute as tc  # noqa: E402
from kb_online import tools_query as tq  # noqa: E402

CTX = base.CTX
CHOICES = {("HRMS_FAQ", "MODULE"): ["M1", "M2"], ("HRMS_FAQ_SOP", "module"): ["M1"],
           ("HRMS_TASK", "module"): ["M2"], ("HRMS_TASK", "category"): ["資料修改", "資料查詢"],
           ("HRMS_FAQ_BULLETIN", "TYPE"): ["版本更新", "規格說明"]}
GOOD_SQL = "BEGIN TRAN\nUPDATE HRMS_LEAVE SET IS_LOCK = 0 WHERE EMPLOYEE_ID = 1\n-- COMMIT\nROLLBACK"


class ContributeBase(base.QueryBase):
    def setUp(self):
        super().setUp()
        self.inserted = []

        def fake_insert(table, values, now_cols=()):
            self.inserted.append((table, dict(values), tuple(now_cols)))
            return 500 + len(self.inserted)
        for target, kw in ((("insert_row",), {"side_effect": fake_insert}),
                           (("distinct_values",), {"side_effect": lambda t, c: CHOICES[(t, c)]})):
            p = mock.patch.object(tq.refdata_db, *target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def flags(self):
        with self.store._lock:
            cur = self.store._conn.execute("SELECT * FROM HRMS_KB_FLAG ORDER BY ID")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    async def add(self, **kw):
        return await self.d.call(CTX, "kb_contribute", kw)


class NewEntryTests(ContributeBase):
    async def test_faq_is_written_immediately_with_author(self):
        text = await self.add(type="faq", title="排班表匯入失敗要怎麼處理", content="請先確認班別代碼是否存在。",
                              module="M1", basis="顧問教育訓練文件")
        self.assertIn("已新增 FAQ faq#501", text)
        self.assertIn("立刻可以查到", text)
        (table, values, _), = self.inserted
        self.assertEqual(table, "HRMS_FAQ")
        self.assertEqual((values["QUESTION"], values["MODULE"], values["RESPONDENT"]),
                         ("排班表匯入失敗要怎麼處理", "M1", "amy.lin(AI)"))
        self.assertTrue(values["ANSWER"].startswith("請先確認班別代碼是否存在。"))
        self.assertIn("（線上填寫；填寫人：amy.lin；依據：顧問教育訓練文件）", values["ANSWER"])
        log = self.last_log()
        self.assertEqual((log["WRITE_OUTCOME"], log["WRITE_REF"], log["USER_NAME"]), ("created", "faq#501", "amy.lin"))
        self.assertEqual(self.flags(), [])

    async def test_version_bulletin(self):
        text = await self.add(type="bulletin", title="5.3.0 版更：加班單簽核流程調整", category="版本更新",
                              content="5.3.0 起加班單改為兩階段簽核，舊單不受影響。", jira_key="EHRMSONE-34900")
        self.assertIn("已新增 公告 bulletin#501", text)
        table, values, _ = self.inserted[0]
        self.assertEqual((table, values["TYPE"], values["AUTHOR"]), ("HRMS_FAQ_BULLETIN", "版本更新", "amy.lin(AI)"))
        self.assertEqual(self.last_log()["JIRA_KEY"], "EHRMSONE-34900")

    async def test_sop_and_task(self):
        sop = "# 客戶要求重算加班費\n\n## 適用情境\n加班費算錯。\n\n## 前置確認\n確認薪資未結算。"
        self.assertIn("sop#501", await self.add(type="sop", title="客戶要求重算加班費", content=sop, module="M1"))
        self.assertIn("task#502", await self.add(type="task", title="重置加班單狀態", content="加班單卡在簽核中時使用",
                                                 syntax=GOOD_SQL, notes="先確認沒有在途單", module="M2", category="資料修改"))
        (t1, v1, _), (t2, v2, now2) = self.inserted
        self.assertEqual((t1, v1["author"], v1["is_active"]), ("HRMS_FAQ_SOP", "amy.lin(AI)", 1))
        self.assertEqual((t2, v2["syntax"], now2), ("HRMS_TASK", GOOD_SQL, ("update_date",)))
        self.assertIn("填寫人：amy.lin", v2["notes"])

    async def test_validation(self):
        cases = [
            (dict(type="memory", title="x", content="y"), "type 只能是"),
            (dict(type="faq", title="x", content=""), "缺少必填欄位"),
            (dict(type="faq", title="新的問題一", content="新的回覆一", module="M9"), "不是既有值"),
            (dict(type="bulletin", title="新公告", content="內容", category="隨便"), "不是既有值"),
            (dict(type="sop", title="新流程", content="沒有照模板寫", module="M1"), "須依模板"),
            (dict(type="task", title="t", content="c", syntax="UPDATE X SET A = 1", notes="n", module="M2",
                  category="資料修改"), "未通過檢查"),
            (dict(type="faq", title="x", content="y", module="M1", updates="abc"), "updates 須為編號"),
            (dict(type="faq", title="x", content="y", module="M1", updates="999"), "找不到要更新的 faq#999"),
        ]
        for kw, expect in cases:
            with self.subTest(expect=expect):
                self.assertIn(expect, await self.add(**kw))
                self.assertEqual(self.last_log()["WRITE_OUTCOME"], "rejected")
        self.assertEqual(self.inserted, [])

    async def test_requires_identity(self):
        other = base.CallContext(client_addr="10.9.9.9", session_id="s")
        self.assertIn("尚未確認使用者身分", await self.d.call(other, "kb_contribute", {"type": "faq"}))
        self.assertEqual(self.inserted, [])

    async def test_db_failure_is_reported(self):
        with mock.patch.object(tq.refdata_db, "insert_row", side_effect=RuntimeError("db down")):
            self.assertIn("寫入失敗", await self.add(type="faq", title="全新的問題", content="全新的回覆", module="M1"))
        self.assertEqual(self.last_log()["WRITE_OUTCOME"], "failed")


class UpdateTests(ContributeBase):
    FAQ = base.REFDATA["faq"][0]     # faq#48：請特休時顯示時數不足怎麼辦

    async def test_similar_new_entry_points_to_updates(self):
        text = await self.add(type="faq", title=self.FAQ["question"], content=self.FAQ["answer"], module="M2")
        self.assertIn("已有相似的 FAQ：faq#48", text)
        self.assertIn("updates=48", text)                 # 有路可走，不是死路
        self.assertEqual(self.inserted, [])
        self.assertEqual(self.last_log()["WRITE_OUTCOME"], "duplicate")

    async def test_update_adds_new_version_keeps_old_and_flags_it(self):
        text = await self.add(type="faq", title=self.FAQ["question"], module="M2", updates="faq#48",
                              content="請先確認特休產生批次是否已執行，再確認結轉設定。", reason="原答案沒提到產生批次",
                              basis="EHRMSONE-31192")
        self.assertIn("已新增 FAQ faq#501", text)
        self.assertIn("原本的 faq#48 沒有被修改", text)
        (table, values, _), = self.inserted                # 只有新增，沒有任何更新舊資料的動作
        self.assertEqual(table, "HRMS_FAQ")
        self.assertIn("更新自 faq#48", values["ANSWER"])
        flag, = self.flags()
        self.assertEqual((flag["TARGET_TYPE"], flag["TARGET_ID"], flag["CREATED_BY"], flag["STATUS"]),
                         ("faq", "48", "amy.lin(AI)", "open"))
        self.assertIn("已有更新版本 faq#501（填寫人 amy.lin）：原答案沒提到產生批次", flag["REASON"])
        # 之後查到舊的那一筆會看到已有更新版本
        old = await self.d.call(CTX, "kb_get", {"type": "faq", "id": "48"})
        self.assertIn("請確認特休是否已產生。", old)
        self.assertIn("已有更新版本 faq#501", old)


class DisplayTests(ContributeBase):
    def refdata_with_filled(self):
        data = {k: list(v) for k, v in base.REFDATA.items()}
        data["faq"] = data["faq"] + [dict(base.REFDATA["faq"][0], id=60, author="bob.wu(AI)")]
        data["bulletin"] = data["bulletin"] + [
            {"id": 30, "title": "5.3.0 版更：特休時數不足誤判修正", "content": "特休時數不足誤判已修正。",
             "category": "版本更新", "keywords": "", "created": "2026-11-21", "author": "bob.wu(AI)"}]
        return data

    async def test_filled_rows_show_author_and_rank_after_human_rows(self):
        data = self.refdata_with_filled()
        with mock.patch.object(tq.refdata_db, "load_all", side_effect=lambda key: data[key]):
            found = await self.d.call(CTX, "kb_search", {"query": "請特休時顯示時數不足怎麼辦", "sources": ["faq"]})
            full = await self.d.call(CTX, "kb_get", {"type": "faq", "id": "60"})
            versions = await self.d.call(CTX, "kb_search", {"query": "特休時數不足", "sources": ["bulletin"],
                                                           "version_bulletins": True})
        self.assertIn("👤填寫人 bob.wu・未審核", found)
        self.assertLess(found.index("[faq#48]"), found.index("[faq#60]"))    # 內容相同時人工的排前面
        refs = {r["id"]: r["score"] for r in json.loads(self.store.usage_rows()[-3]["RESULT_REFS"])}
        self.assertAlmostEqual(refs[60], round(refs[48] * tq.AI_WEIGHT, 3), places=2)
        self.assertIn("填寫人：bob.wu（線上填寫、未審核", full)
        self.assertIn("其中線上填寫、未審核的：公告 #30（填寫人 bob.wu）", versions)

    def test_tool_is_registered_with_all_types(self):
        schema = tc.registry.tools["kb_contribute"]["schema"]
        self.assertEqual(schema["properties"]["type"]["enum"], ["faq", "sop", "task", "bulletin"])


if __name__ == "__main__":
    unittest.main()
