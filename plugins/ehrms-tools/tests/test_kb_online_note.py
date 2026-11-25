# -*- coding: utf-8 -*-
"""線上知識庫 MCP 工作筆記（kb_note）的單元測試：不連資料庫，筆記寫進測試用的 SQLite。"""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_kb_online_query as base  # noqa: E402
from kb_online import server, store as st, tools_note as tn, tools_query as tq, tools_skill as ts  # noqa: E402

CTX = base.CTX


class NoteBase(base.QueryBase):
    def notes(self):
        with self.store._lock:
            cur = self.store._conn.execute("SELECT * FROM HRMS_MEMORY_NOTE ORDER BY ID")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    async def note(self, ctx=CTX, **kw):
        args = {"kind": "finding", "topic": "颱風假加班時數的進位方式",
                "content": "颱風假當天的加班時數在 CalcOvertime 以半小時為單位無條件捨去，與一般加班不同。"}
        args.update(kw)
        return await self.d.call(ctx, "kb_note", args)


class NoteWriteTests(NoteBase):
    async def test_finding_is_stored_with_server_side_fields(self):
        ctx = base.CallContext(client_addr="10.1.2.3", session_id="sess-note-1", origin="chat")
        text = await self.note(ctx, evidence="VB/EHRMS/Overtime.cls :: CalcOvertime 第 120 行", keywords="颱風假,加班",
                               entry_path=r"C:\D\EHRMS_GIT\VB\EHRMS\Overtime.cls", entry_method="CalcOvertime",
                               ref_key="EHRMSONE-35001")
        self.assertIn("已寫入工作筆記 note#1", text)
        self.assertIn("記錄者 amy.lin(AI)", text)
        self.assertIn("查詢不會查到", text)
        row, = self.notes()
        self.assertEqual(
            (row["NOTE_KIND"], row["TOPIC"], row["EVIDENCE"], row["KEYWORDS"], row["ENTRY_PATH"], row["ENTRY_METHOD"],
             row["REF_KEY"]),
            ("finding", "颱風假加班時數的進位方式", "VB/EHRMS/Overtime.cls :: CalcOvertime 第 120 行", "颱風假,加班",
             "VB/EHRMS/Overtime.cls", "CalcOvertime", "EHRMSONE-35001"))
        self.assertIn("無條件捨去", row["CONTENT"])
        # 伺服器填的欄位：記錄者是確認過的使用者，來源與連線編號來自連線層
        self.assertEqual((row["CREATED_BY"], row["ORIGIN"], row["SESSION_ID"]), ("amy.lin(AI)", "chat", "sess-note-1"))
        self.assertTrue(row["CREATED_AT"])
        # 處理結果欄位不寫，用預設值
        self.assertEqual((row["STATUS"], row["RESOLVED_BY"], row["RESOLVED_AT"], row["RESULT_MEMORY_ID"],
                          row["RESOLVE_NOTE"], row["USAGE_LOG_ID"]), ("open", None, None, None, None, None))
        # 使用紀錄沿用既有的寫入結果值，並可用 WRITE_REF 與連線編號對回筆記
        log = self.last_log()
        self.assertEqual((log["TOOL_NAME"], log["WRITE_OUTCOME"], log["WRITE_REF"], log["JIRA_KEY"], log["ERROR_MSG"],
                          log["SESSION_ID"], log["USER_NAME"]),
                         ("kb_note", "created", "note#1", "EHRMSONE-35001", None, "sess-note-1", "amy.lin"))

    async def test_correction_with_only_required_fields(self):
        text = await self.note(kind="correction", topic="特休結轉不是每年一月執行",
                               content="特休結轉依員工到職日週年執行，不是固定每年一月。")
        self.assertIn("note#1", text)
        self.assertIn("使用者的修正", text)
        row, = self.notes()
        self.assertEqual((row["NOTE_KIND"], row["EVIDENCE"], row["KEYWORDS"], row["ENTRY_PATH"], row["ENTRY_METHOD"],
                          row["REF_KEY"], row["STATUS"]), ("correction", None, None, None, None, None, "open"))
        self.assertIsNone(self.last_log()["JIRA_KEY"])

    async def test_each_call_adds_a_row_without_dedup(self):
        await self.note()
        self.assertIn("note#2", await self.note())
        self.assertEqual(len(self.notes()), 2)

    async def test_origin_comes_from_the_connection_not_the_arguments(self):
        ctx = base.CallContext(client_addr="10.1.2.3", session_id="s" * 50, origin="web")
        await self.note(ctx, origin="schedule", created_by="someone.else", status="promoted", session_id="fake")
        row, = self.notes()
        self.assertEqual((row["ORIGIN"], row["CREATED_BY"], row["STATUS"], row["SESSION_ID"]),
                         ("web", "amy.lin(AI)", "open", "s" * 36))      # 連線編號截到欄位長度
        self.assertFalse({"origin", "created_by", "status", "session_id", "usage_log_id"}
                         & set(tn.registry.tools["kb_note"]["schema"]["properties"]))

    async def test_jira_key_is_accepted_as_ref_key(self):
        await self.note(jira_key="EHRMSONE-35002")
        self.assertEqual(self.notes()[0]["REF_KEY"], "EHRMSONE-35002")
        self.assertEqual(self.last_log()["JIRA_KEY"], "EHRMSONE-35002")

    async def test_entry_path_outside_known_repos_is_kept_as_given(self):
        await self.note(entry_path="D:/somewhere/else/x.cls")
        self.assertEqual(self.notes()[0]["ENTRY_PATH"], "D:/somewhere/else/x.cls")

    async def test_validation_rejections_are_logged_and_nothing_is_stored(self):
        cases = [
            ({"kind": "System"}, "kind 只能是"),
            ({"kind": ""}, "kind 只能是"),
            ({"topic": ""}, "必填"),
            ({"content": "  "}, "必填"),
            ({"topic": "題" * 101}, "topic 超過 100 字"),
            ({"content": "字" * 4001}, "content 超過 4000 字"),
            ({"evidence": "據" * 2001}, "evidence 超過 2000 字"),
            ({"keywords": "鍵" * 301}, "keywords 超過 300 字"),
            ({"ref_key": "K" * 51}, "ref_key 超過 50 字"),
        ]
        for kw, expect in cases:
            with self.subTest(expect=expect, kw=list(kw)):
                self.assertIn(expect, await self.note(**kw))
                log = self.last_log()
                self.assertEqual(log["WRITE_OUTCOME"], "rejected")
                self.assertTrue(log["ERROR_MSG"])
        self.assertEqual(self.notes(), [])

    async def test_limits_match_the_table(self):
        await self.note(topic="題" * 100, content="字" * 4000, evidence="據" * 2000)
        self.assertEqual(len(self.notes()), 1)

    async def test_db_failure_is_reported_and_logged(self):
        with mock.patch.object(self.store, "add_note", side_effect=RuntimeError("db down")):
            self.assertIn("工作筆記寫入失敗", await self.note(ref_key="EHRMSONE-35003"))
        log = self.last_log()
        self.assertEqual((log["WRITE_OUTCOME"], log["WRITE_REF"], log["JIRA_KEY"]), ("failed", None, "EHRMSONE-35003"))
        self.assertIn("RuntimeError", log["ERROR_MSG"])
        self.assertEqual(self.notes(), [])

    async def test_requires_identity(self):
        other = base.CallContext(client_addr="10.9.9.9", session_id="s")
        self.assertIn("尚未確認使用者身分", await self.note(other))
        self.assertEqual(self.notes(), [])

    async def test_does_not_need_the_knowledge_db_and_never_writes_memory(self):
        with mock.patch.object(tq.mdb, "db_enabled", return_value=False), \
                mock.patch.object(tq.mdb, "insert_memory", side_effect=AssertionError("工作筆記不該寫進 HRMS_MEMORY")):
            self.assertIn("note#1", await self.note())
        with self.store._lock:
            others = [self.store._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("HRMS_KB_CANDIDATE", "HRMS_KB_FLAG")]
        self.assertEqual(others, [0, 0])


class NoteIsNotSearchableTests(NoteBase):
    async def test_query_tools_cannot_find_a_note(self):
        await self.note(topic="特休時數不足的判斷：只有筆記才有的說法", keywords="特休,時數不足",
                        content="員工請特休跳出時數不足，筆記獨有字樣甲乙丙丁。")
        found = await self.d.call(CTX, "kb_search", {"query": "特休時數不足 筆記獨有字樣甲乙丙丁"})
        self.assertIn("[memory#1]", found)                        # 正式記憶照常查得到
        self.assertNotIn("甲乙丙丁", found.split("\n", 1)[1])     # 第一行是查詢敘述本身
        self.assertNotIn("note#", found)
        refs = self.last_log()["RESULT_REFS"]
        self.assertNotIn("note", refs)
        # kb_get 沒有 note 這個類型，也不能用記憶的編號讀到筆記
        self.assertNotIn("note", tq.GET_TYPES + tq.SOURCES)
        self.assertIn("type 只能是", await self.d.call(CTX, "kb_get", {"type": "note", "id": "1"}))
        self.assertIn("找不到", await self.d.call(CTX, "kb_get", {"type": "memory", "id": "901"}))
        self.assertIn("type 只能是", await self.d.call(CTX, "kb_flag", {"type": "note", "id": "1", "reason": "x",
                                                                    "evidence": "y"}))

    def test_store_has_no_way_to_read_notes(self):
        for cls in (st.SqliteStore, st.MssqlStore):
            methods = [m for m in dir(cls) if "note" in m.lower()]
            self.assertEqual(methods, ["add_note"], cls.__name__)
        # 處理結果欄位與 USAGE_LOG_ID 不在寫入欄位裡
        self.assertFalse({"STATUS", "RESOLVED_BY", "RESOLVED_AT", "RESULT_MEMORY_ID", "RESOLVE_NOTE", "USAGE_LOG_ID"}
                         & set(st.NOTE_COLUMNS))


class ReminderTests(NoteBase):
    async def test_search_and_get_end_with_the_note_reminder(self):
        found = await self.d.call(CTX, "kb_search", {"query": "員工請特休跳出時數不足"})
        self.assertTrue(found.endswith(tq.NOTE_REMINDER))
        self.assertIn("kb_note", tq.NOTE_REMINDER)
        nothing = await self.d.call(CTX, "kb_search", {"query": "完全不相關的外星人主題"})
        self.assertEqual(self.last_log()["NO_RESULT"], 1)
        self.assertTrue(nothing.endswith(tq.NOTE_REMINDER))       # 查無資料時更可能另外去查
        for args in ({"type": "faq", "id": "48"}, {"type": "memory", "id": "1"},
                     {"type": "wiki", "id": "CARD", "section": "R1"}):
            with self.subTest(args=args):
                self.assertTrue((await self.d.call(CTX, "kb_get", args)).endswith(tq.NOTE_REMINDER))

    async def test_no_reminder_when_nothing_was_looked_up(self):
        for name, args in (("kb_get", {"type": "faq", "id": "999"}), ("kb_get", {"type": "nope", "id": "1"}),
                           ("kb_search", {"query": "x"})):
            with self.subTest(name=name, args=args):
                self.assertNotIn("kb_note", await self.d.call(CTX, name, args))

    async def test_flagged_item_shows_flag_before_reminder(self):
        self.flag("faq", 48, "答案過時")
        text = await self.d.call(CTX, "kb_get", {"type": "faq", "id": "48"})
        self.assertLess(text.index("答案過時"), text.index(tq.NOTE_REMINDER))


class GuidanceTests(NoteBase):
    def test_tool_description_says_when_to_write_and_when_not(self):
        desc = tn.registry.tools["kb_note"]["description"]
        for token in ("知識庫以外", "看程式碼", "糾正", "不用寫", "kb_search／kb_get", "不要寫客戶的實際資料",
                      "不是正式記憶", "remember"):
            self.assertIn(token, desc)
        schema = tn.registry.tools["kb_note"]["schema"]
        self.assertEqual(schema["properties"]["kind"]["enum"], ["finding", "correction"])
        self.assertEqual(schema["required"], ["kind", "topic", "content"])

    def test_instructions_and_tool_map_mention_notes(self):
        text = server.INSTRUCTIONS + ts.index_text()
        for token in ("kb_note", "知識庫以外", "kind=finding", "kind=correction", "不是正式記憶"):
            self.assertIn(token, text)
        self.assertLess(len(text), 2500)
        self.assertIn("`kb_note`", ts.TOOL_MAP)

    async def test_kb_info_lists_the_tool(self):
        self.assertIn("kb_note", await self.d.call(CTX, "kb_info", {}))


if __name__ == "__main__":
    unittest.main()
