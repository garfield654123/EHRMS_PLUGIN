# -*- coding: utf-8 -*-
"""本機 ehrms-memory 的工作筆記（note_log）單元測試（不連資料庫）。"""
from pathlib import Path
import asyncio
import inspect
import os
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "MEMORY"))

import memory_core  # noqa: E402
import memory_db  # noqa: E402
import note_core as nc  # noqa: E402
import server  # noqa: E402

REAL_CREATED_BY = memory_db.created_by


class FakeCursor:
    def __init__(self, new_id=7):
        self.calls, self.new_id = [], new_id

    def execute(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        return self

    def fetchone(self):
        return [self.new_id]


class NoteLogBase(unittest.TestCase):
    def setUp(self):
        self.rows = []

        def fake_insert(row):
            self.rows.append(row)
            return 40 + len(self.rows)
        for p in (mock.patch.object(nc.db, "db_enabled", return_value=True),
                  mock.patch.object(nc.db, "created_by", return_value="ziping.zhou"),
                  mock.patch.object(nc, "_insert", side_effect=fake_insert)):
            p.start()
            self.addCleanup(p.stop)

    def log(self, **kw):
        args = {"kind": "finding", "topic": "颱風假加班時數的進位方式",
                "content": "颱風假當天的加班時數在 CalcOvertime 以半小時為單位無條件捨去。"}
        args.update(kw)
        return nc.log_note(**args)


class NoteLogTests(NoteLogBase):
    def test_finding_is_stored_with_local_identity(self):
        text = self.log(evidence="VB/EHRMS/Overtime.cls :: CalcOvertime", keywords="颱風假,加班",
                        entry_path=r"C:\D\EHRMS_GIT\VB\EHRMS\Overtime.cls", entry_method="CalcOvertime",
                        ref_key="EHRMSONE-35001")
        self.assertIn("已寫入工作筆記 note#41", text)
        self.assertIn("記錄者 ziping.zhou(AI)", text)
        self.assertIn("查詢不會查到", text)
        row, = self.rows
        self.assertEqual(row, {
            "NOTE_KIND": "finding", "TOPIC": "颱風假加班時數的進位方式",
            "CONTENT": "颱風假當天的加班時數在 CalcOvertime 以半小時為單位無條件捨去。",
            "EVIDENCE": "VB/EHRMS/Overtime.cls :: CalcOvertime", "KEYWORDS": "颱風假,加班",
            "ENTRY_PATH": "VB/EHRMS/Overtime.cls", "ENTRY_METHOD": "CalcOvertime", "REF_KEY": "EHRMSONE-35001",
            "ORIGIN": "chat", "SESSION_ID": nc.SESSION_ID, "CREATED_BY": "ziping.zhou(AI)"})
        self.assertEqual(len(nc.SESSION_ID), 32)                 # 欄位是 VARCHAR(36)

    def test_correction_with_only_required_fields(self):
        self.assertIn("使用者的修正", self.log(kind="correction"))
        row, = self.rows
        self.assertEqual([row[c] for c in ("EVIDENCE", "KEYWORDS", "ENTRY_PATH", "ENTRY_METHOD", "REF_KEY")], [None] * 5)

    def test_recorder_follows_memory_user(self):
        with mock.patch.object(nc.db, "created_by", side_effect=REAL_CREATED_BY), \
                mock.patch.dict(os.environ, {"MEMORY_USER": "amy.lin"}):
            self.log()
        self.assertEqual(self.rows[0]["CREATED_BY"], "amy.lin(AI)")

    def test_no_dedup_and_no_reads(self):
        with mock.patch.object(nc.db, "load_all", side_effect=AssertionError("工作筆記不該讀記憶表")), \
                mock.patch.object(nc.db, "insert_memory", side_effect=AssertionError("工作筆記不該寫進 HRMS_MEMORY")):
            self.log()
            self.assertIn("note#42", self.log())
        self.assertEqual(len(self.rows), 2)

    def test_entry_path_outside_known_repos_is_kept_as_given(self):
        self.log(entry_path="D:/somewhere/else/x.cls")
        self.assertEqual(self.rows[0]["ENTRY_PATH"], "D:/somewhere/else/x.cls")

    def test_validation(self):
        cases = [
            ({"kind": "System"}, "kind 只能是"), ({"kind": ""}, "kind 只能是"),
            ({"topic": ""}, "必填"), ({"content": "  "}, "必填"),
            ({"topic": "題" * 101}, "topic 超過 100 字"), ({"content": "字" * 4001}, "content 超過 4000 字"),
            ({"evidence": "據" * 2001}, "evidence 超過 2000 字"), ({"keywords": "鍵" * 301}, "keywords 超過 300 字"),
            ({"entry_path": "p" * 501}, "entry_path 超過 500 字"), ({"entry_method": "m" * 301}, "entry_method 超過 300 字"),
            ({"ref_key": "K" * 51}, "ref_key 超過 50 字"),
        ]
        for kw, expect in cases:
            with self.subTest(expect=expect, kw=list(kw)):
                self.assertIn(expect, self.log(**kw))
        self.assertEqual(self.rows, [])
        self.log(topic="題" * 100, content="字" * 4000, evidence="據" * 2000)   # 剛好等於欄位長度可以寫
        self.assertEqual(len(self.rows), 1)

    def test_db_disabled_or_failing_is_reported(self):
        with mock.patch.object(nc.db, "db_enabled", return_value=False):
            self.assertIn("未設定 MSSQL_*", self.log())
        with mock.patch.object(nc, "_insert", side_effect=RuntimeError("db down")):
            self.assertIn("工作筆記寫入失敗：db down", self.log())
        self.assertEqual(self.rows, [])


class NoteSqlTests(unittest.TestCase):
    def test_insert_only_touches_note_columns(self):
        cur = FakeCursor(new_id=9)
        conn = mock.Mock()
        conn.cursor.return_value = cur
        with mock.patch.object(nc.db, "_get_conn", return_value=conn):
            new_id = nc._insert({c: f"v-{c}" for c in nc.COLUMNS})
        self.assertEqual(new_id, 9)
        (sql, params), = cur.calls
        self.assertIn("INSERT INTO dbo.HRMS_MEMORY_NOTE(", sql)
        self.assertEqual(params, [f"v-{c}" for c in nc.COLUMNS])
        self.assertNotIn("UPDATE", sql.upper())
        self.assertNotIn("DELETE", sql.upper())
        # 處理結果欄位與 USAGE_LOG_ID 不寫
        for col in ("STATUS", "RESOLVED_BY", "RESOLVED_AT", "RESULT_MEMORY_ID", "RESOLVE_NOTE", "USAGE_LOG_ID"):
            self.assertNotIn(col, nc.COLUMNS)
            self.assertNotIn(col, sql)

    def test_nothing_reads_the_note_table(self):
        self.assertNotIn("SELECT", inspect.getsource(nc).replace("SELECT CAST(SCOPE_IDENTITY() AS INT)", "").upper()
                         .replace("只發 SELECT", ""))
        memory_dir = ROOT / "servers" / "MEMORY"
        users = sorted(p.name for p in memory_dir.glob("*.py") if "HRMS_MEMORY_NOTE" in p.read_text(encoding="utf-8"))
        self.assertEqual(users, ["note_core.py", "server.py"])   # server.py 只在工具說明裡提到表名

    def test_recall_does_not_return_notes(self):
        memories = [{"id": 1, "type": "System", "topic": "特休時數不足的判斷", "content": "先看特休產生批次是否已執行。",
                     "keywords": "特休,時數不足", "func_path": "", "entry_path": "", "entry_method": "",
                     "supersedes": None, "ref_key": "", "source": "", "created_by": "amy.lin(AI)",
                     "created_at": "2026-11-01", "hits": 0, "last_hit": "", "review": "pending", "meta": {}}]
        with mock.patch.object(memory_db, "db_enabled", return_value=True), \
                mock.patch.object(memory_db, "load_all", return_value=memories), \
                mock.patch.object(memory_db, "bump_hits"), \
                mock.patch.object(nc, "_insert", return_value=5), \
                mock.patch.object(nc.db, "created_by", return_value="ziping.zhou"):
            nc.log_note("finding", "特休時數不足的判斷：筆記獨有", "特休時數不足 筆記獨有字樣甲乙丙丁", keywords="特休,時數不足")
            found = memory_core.recall("特休時數不足 筆記獨有字樣甲乙丙丁")
        self.assertIn("特休產生批次", found)
        self.assertNotIn("甲乙丙丁", found.split("\n", 1)[1])     # 第一行是查詢敘述本身
        self.assertNotIn("筆記獨有", found.split("\n", 1)[1])


class ServerWiringTests(unittest.TestCase):
    def test_tool_is_listed_with_guidance(self):
        tool = {t.name: t for t in asyncio.run(server.list_tools())}["note_log"]
        for token in ("知識庫以外", "讀程式碼", "糾正", "不用寫", "remember 或 jira_log", "不要寫客戶的實際資料",
                      "不是正式記憶", "不需要等結論確認"):
            self.assertIn(token, tool.description)
        self.assertEqual(tool.inputSchema["required"], ["kind", "topic", "content"])
        self.assertEqual(tool.inputSchema["properties"]["kind"]["enum"], ["finding", "correction"])
        self.assertEqual(set(tool.inputSchema["properties"]),
                         {"kind", "topic", "content", "evidence", "keywords", "entry_path", "entry_method", "ref_key"})

    def test_call_passes_arguments_through(self):
        with mock.patch.object(server.nc, "log_note", return_value="ok") as log:
            out = asyncio.run(server.call_tool("note_log", {
                "kind": "correction", "topic": "t", "content": "c", "evidence": "e", "ref_key": "EHRMSONE-1"}))
        self.assertEqual(out, [{"type": "text", "text": "ok"}])
        log.assert_called_once_with("correction", "t", "c", evidence="e", keywords="", entry_path="",
                                    entry_method="", ref_key="EHRMSONE-1")

    def test_missing_required_arguments_are_rejected_not_crashing(self):
        with mock.patch.object(nc, "_insert", side_effect=AssertionError("不該寫入")):
            out = asyncio.run(server.call_tool("note_log", {"kind": "finding"}))
        self.assertIn("必填", out[0]["text"])

    def test_context_and_skill_tell_the_ai_to_write_notes(self):
        context = (ROOT / "context.md").read_text(encoding="utf-8")
        skill = (ROOT / "skills" / "crisis-triage" / "SKILL.md").read_text(encoding="utf-8")
        for text in (context, skill):
            for token in ("note_log", "工作筆記", "已確認", "例外", "kind=", "correction"):
                self.assertIn(token, text)
        self.assertIn("正式知識（以下各項）只記錄**已確認**的結論", context)


if __name__ == "__main__":
    unittest.main()
