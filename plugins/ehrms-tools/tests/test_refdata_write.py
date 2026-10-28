# -*- coding: utf-8 -*-
"""refdata_write（faq_log／task_log／sop_log）與 AI 寫入者標記的單元測試（不連資料庫）。"""
from pathlib import Path
import os
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "MEMORY"))

import memory_db  # noqa: E402
import refdata_core  # noqa: E402
import refdata_write as rw  # noqa: E402

SOP_CONTENT = """# 客戶要求調整特休到期日

## 適用情境
客戶要延長某員工的特休到期日。

## 前置確認
確認該年度特休尚未結算。
"""

GOOD_SQL = """-- 解鎖假單（替換員工編號與日期）
BEGIN TRAN
UPDATE HRMS_LEAVE SET IS_LOCK = 0
WHERE EMPLOYEE_ID = 123 AND LEAVE_DATE = '2026-10-01'
-- COMMIT
ROLLBACK
"""

DISTINCT = {
    ("HRMS_FAQ", "MODULE"): ["M1", "M2", "WF"],
    ("HRMS_TASK", "module"): ["M1", "M2"],
    ("HRMS_TASK", "category"): ["資料修改", "資料查詢"],
    ("HRMS_FAQ_SOP", "module"): ["COMMON", "M1"],
}


def _faq(id_, question, answer, author="長承"):
    return {"id": id_, "question": question, "answer": answer, "category": "M1",
            "keywords": "", "author": author}


class FakeDb:
    """取代 refdata_db 的寫入與查詢函式。"""

    def __init__(self, rows=None, authors=None):
        self.rows = rows or {"faq": [], "task": [], "sop": []}
        self.authors = authors or {}
        self.inserted, self.updated = [], []

    def patches(self):
        return [
            mock.patch.object(rw.db, "db_enabled", return_value=True),
            mock.patch.object(rw.db, "load_all", side_effect=lambda k: self.rows[k]),
            mock.patch.object(rw.db, "distinct_values", side_effect=lambda t, c: DISTINCT[(t, c)]),
            mock.patch.object(rw.db, "author_of",
                              side_effect=lambda t, i, a, rid: self.authors.get(rid)),
            mock.patch.object(rw.db, "insert_row", side_effect=self._insert),
            mock.patch.object(rw.db, "update_row", side_effect=self._update),
            mock.patch.object(rw.jira_db, "load_all", return_value=[{"key": "EHRMSONE-32599"}]),
            mock.patch.object(rw.mdb, "created_by", return_value="ziping.zhou"),
        ]

    def _insert(self, table, values, now_cols=()):
        self.inserted.append((table, dict(values), tuple(now_cols)))
        return 900

    def _update(self, table, id_col, row_id, values, touch_col):
        self.updated.append((table, row_id, dict(values), touch_col))


class WriteTestCase(unittest.TestCase):
    def use(self, fake):
        for p in fake.patches():
            p.start()
            self.addCleanup(p.stop)
        return fake


class CreatedByTest(unittest.TestCase):
    def test_ai_suffix(self):
        with mock.patch.dict(os.environ, {"MEMORY_USER": "ziping.zhou"}):
            self.assertEqual(memory_db.created_by_ai(), "ziping.zhou(AI)")


class FaqLogTest(WriteTestCase):
    def test_insert_marks_ai_and_appends_refs(self):
        fake = self.use(FakeDb())
        out = rw.faq_log("特休可以延長嗎", "可以在特休設定調整到期日。", "M1", "記憶 #15")
        self.assertIn("ID 900", out)
        table, values, _ = fake.inserted[0]
        self.assertEqual(table, "HRMS_FAQ")
        self.assertEqual(values["RESPONDENT"], "ziping.zhou(AI)")
        self.assertTrue(values["ANSWER"].endswith("（AI 撰寫；依據：記憶 #15）"))

    def test_refs_required(self):
        self.use(FakeDb())
        self.assertIn("refs", rw.faq_log("q", "a", "M1", " "))

    def test_unknown_module_lists_candidates(self):
        self.use(FakeDb())
        out = rw.faq_log("特休可以延長嗎", "可以。", "假勤", "記憶 #15")
        self.assertIn("M1、M2、WF", out)

    def test_duplicate_blocked(self):
        q, a = "特休可以延長嗎", "可以在特休設定調整到期日。"
        fake = self.use(FakeDb(rows={"faq": [_faq(7, q, a)], "task": [], "sop": []}))
        out = rw.faq_log(q, a, "M1", "記憶 #15")
        self.assertIn("ID 7", out)
        self.assertIn("人工", out)
        self.assertEqual(fake.inserted, [])

    def test_cannot_update_human_row(self):
        fake = self.use(FakeDb(authors={7: "長承"}))
        out = rw.faq_log("q", "a", "M1", "記憶 #15", row_id=7)
        self.assertIn("人工資料", out)
        self.assertEqual(fake.updated, [])

    def test_update_ai_row_replaces_footer(self):
        old = "舊答案\n\n（AI 撰寫；依據：記憶 #1）"
        fake = self.use(FakeDb(rows={"faq": [_faq(7, "q", old, "ziping.zhou(AI)")],
                                     "task": [], "sop": []},
                               authors={7: "ziping.zhou(AI)"}))
        out = rw.faq_log("特休可以延長嗎", old, "M1", "記憶 #15", row_id=7)
        self.assertIn("已更新", out)
        values = fake.updated[0][2]
        self.assertEqual(values["ANSWER"], "舊答案\n\n（AI 撰寫；依據：記憶 #15）")
        self.assertEqual(fake.updated[0][3], "UPDATE_DATE")


class TaskLogTest(WriteTestCase):
    def call(self, syntax=GOOD_SQL, refs="EHRMSONE-32599"):
        return rw.task_log("解鎖假單", "假單已鎖定需解鎖", syntax, "先確認未計薪",
                           "M1", "資料修改", refs)

    def test_insert_fills_update_date(self):
        fake = self.use(FakeDb())
        self.assertIn("ID 900", self.call())
        table, values, now_cols = fake.inserted[0]
        self.assertEqual(table, "HRMS_TASK")
        self.assertEqual(values["author"], "ziping.zhou(AI)")
        self.assertEqual(now_cols, ("update_date",))

    def test_refs_must_have_logged_jira(self):
        self.use(FakeDb())
        self.assertIn("HRMS_JIRA", self.call(refs="EHRMSONE-1"))

    def test_bad_sql_rejected(self):
        self.use(FakeDb())
        out = self.call(syntax="BEGIN TRAN\nDELETE FROM HRMS_LEAVE\nCOMMIT")
        self.assertIn("缺少 WHERE", out)
        self.assertIn("COMMIT", out)


class CheckSqlTest(unittest.TestCase):
    def test_good(self):
        self.assertEqual(rw.check_sql(GOOD_SQL), [])

    def test_select_only_needs_no_tran(self):
        self.assertEqual(rw.check_sql("SELECT * FROM HRMS_LEAVE WHERE ID = 1"), [])

    def test_forbidden_function(self):
        errs = rw.check_sql("SELECT STRING_AGG(NAME, ',') FROM T WHERE 1=1")
        self.assertTrue(any("STRING_AGG" in e for e in errs))

    def test_comment_and_string_ignored(self):
        sql = "-- UPDATE T SET A=1\nSELECT N'DELETE FROM T' AS X FROM T WHERE ID = 1"
        self.assertEqual(rw.check_sql(sql), [])

    def test_update_without_where(self):
        errs = rw.check_sql("BEGIN TRAN\nUPDATE T SET A = 1\nSELECT * FROM T WHERE ID = 1\n-- COMMIT")
        self.assertTrue(any("缺少 WHERE" in e for e in errs))

    def test_no_tran(self):
        errs = rw.check_sql("UPDATE T SET A = 1 WHERE ID = 1")
        self.assertTrue(any("BEGIN TRAN" in e for e in errs))

    def test_drop_rejected(self):
        self.assertTrue(rw.check_sql("DROP TABLE T"))


class SopLogTest(WriteTestCase):
    def test_template_required(self):
        self.use(FakeDb())
        out = rw.sop_log("特休到期日", "隨便寫", "M1", "記憶 #15")
        self.assertIn("## 適用情境", out)

    def test_insert(self):
        fake = self.use(FakeDb())
        out = rw.sop_log("客戶要求調整特休到期日", SOP_CONTENT, "COMMON", "FAQ #783")
        self.assertIn("ID 900", out)
        table, values, _ = fake.inserted[0]
        self.assertEqual(table, "HRMS_FAQ_SOP")
        self.assertEqual(values["author"], "ziping.zhou(AI)")
        self.assertEqual(values["is_active"], 1)


class LookupLabelTest(unittest.TestCase):
    def test_ai_row_tagged(self):
        lines = refdata_core._fmt("faq", _faq(1, "q", "a", "ziping.zhou(AI)"))
        self.assertIn("🤖 AI 撰寫", lines[0])
        lines = refdata_core._fmt("faq", _faq(1, "q", "a"))
        self.assertNotIn("AI", lines[0])


if __name__ == "__main__":
    unittest.main()
