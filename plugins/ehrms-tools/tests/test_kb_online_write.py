# -*- coding: utf-8 -*-
"""線上知識庫 MCP 寫入工具（remember／kb_flag）的單元測試：不連資料庫，HRMS_MEMORY 的寫入以替身取代。"""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_kb_online_query as base  # noqa: E402
from kb_online import tools_query as tq  # noqa: E402
from kb_online import tools_write as tw  # noqa: E402

CTX = base.CTX


class WriteBase(base.QueryBase):
    def setUp(self):
        super().setUp()
        self.inserted = []

        def fake_insert(*args, **kwargs):
            self.inserted.append((args, kwargs))
            return 900 + len(self.inserted)
        p = mock.patch.object(tq.mdb, "insert_memory", side_effect=fake_insert)
        p.start()
        self.addCleanup(p.stop)

    def table(self, name):
        with self.store._lock:
            cur = self.store._conn.execute(f"SELECT * FROM {name} ORDER BY ID")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    async def remember(self, **kw):
        args = {"kind": "System", "purpose": "general", "topic": "排班表匯入的檢核順序",
                "content": "排班表匯入時先檢核員工是否在職，再檢核班別代碼是否存在。", "keywords": "排班,匯入"}
        args.update(kw)
        return await self.d.call(CTX, "remember", args)


class RememberTests(WriteBase):
    async def test_new_memory_is_written_as_the_confirmed_user(self):
        text = await self.remember(purpose="procedure", ref_key="EHRMSONE-34001")
        self.assertIn("已記住 memory#901", text)
        (args, kwargs), = self.inserted
        self.assertEqual(args[:3], ("System", "排班表匯入的檢核順序", "排班表匯入時先檢核員工是否在職，再檢核班別代碼是否存在。"))
        self.assertEqual(kwargs["created_by"], "amy.lin(AI)")      # 伺服器確認的使用者，不是伺服器本機的帳號
        self.assertEqual((kwargs["source"], kwargs["meta"], kwargs["ref_key"]),
                         ("kb-online", {"purpose": "procedure"}, "EHRMSONE-34001"))
        self.assertNotIn("supersedes_id", kwargs)
        log = self.last_log()
        self.assertEqual((log["WRITE_OUTCOME"], log["WRITE_REF"], log["JIRA_KEY"], log["ERROR_MSG"]),
                         ("created", "memory#901", "EHRMSONE-34001", None))
        self.assertEqual((self.table("HRMS_KB_CANDIDATE"), self.table("HRMS_KB_FLAG")), ([], []))

    async def test_near_identical_content_is_a_duplicate(self):
        m = base.MEMORIES[0]
        text = await self.remember(topic=m["topic"], content=m["content"], keywords=m["keywords"])
        self.assertIn("已有幾乎相同的記憶 memory#1", text)
        self.assertEqual(self.inserted, [])
        self.assertEqual(self.table("HRMS_KB_CANDIDATE"), [])
        log = self.last_log()
        self.assertEqual((log["WRITE_OUTCOME"], log["WRITE_REF"]), ("duplicate", "memory#1"))

    async def test_similar_but_different_statement_becomes_candidate_with_auto_flag(self):
        # 與 memory#1 主題、關鍵字相同，但結論相反
        text = await self.remember(topic="特休時數不足的判斷", keywords="特休,時數不足", purpose="reply",
                                   content="員工請特休跳出時數不足，與特休產生批次無關，一律是班別設定造成。",
                                   ref_key="EHRMSONE-34002")
        self.assertIn("已記錄為待確認（候選 #1）", text)
        self.assertEqual(self.inserted, [])                        # 沒有進正式記憶
        cand, = self.table("HRMS_KB_CANDIDATE")
        self.assertEqual((cand["MEM_TYPE"], cand["PURPOSE"], cand["CREATED_BY"], cand["REF_KEY"], cand["STATUS"]),
                         ("System", "reply", "amy.lin(AI)", "EHRMSONE-34002", "open"))
        self.assertIn("一律是班別設定造成", cand["CONTENT"])       # 完整內容有存下來
        flag, = self.table("HRMS_KB_FLAG")
        self.assertEqual((flag["TARGET_TYPE"], flag["TARGET_ID"], flag["FLAG_SOURCE"], flag["CANDIDATE_ID"]),
                         ("memory", str(cand["CONFLICT_MEMORY_ID"]), "auto_conflict", cand["ID"]))
        log = self.last_log()
        self.assertEqual((log["WRITE_OUTCOME"], log["WRITE_REF"]), ("candidate", "candidate#1"))
        # 之後查到被撞到的那筆記憶會顯示有疑慮；候選本身查不到
        found = await self.d.call(CTX, "kb_search", {"query": "特休時數不足", "sources": ["memory"]})
        self.assertIn("⚠ 有疑慮：有新的不同說法（候選 #1）", found)
        self.assertNotIn("一律是班別設定造成", found)

    async def test_engineer_same_topic_different_entry_becomes_candidate(self):
        m = base.MEMORIES[1]
        text = await self.remember(kind="Engineer", topic=m["topic"], content=m["content"], keywords=m["keywords"],
                                   entry_path="VB/EHRMS/Other.cls")
        self.assertIn("已記錄為待確認", text)
        self.assertEqual(self.table("HRMS_KB_CANDIDATE")[0]["ENTRY_PATH"], "VB/EHRMS/Other.cls")

    async def test_force_and_supersedes_are_ignored(self):
        await self.remember(topic="特休時數不足的判斷", keywords="特休,時數不足",
                            content="員工請特休跳出時數不足，與特休產生批次無關，一律是班別設定造成。",
                            force=True, supersedes="1")
        self.assertEqual(self.inserted, [])                        # 帶了也不能繞過
        self.assertEqual(len(self.table("HRMS_KB_CANDIDATE")), 1)
        props = tw.registry.tools["remember"]["schema"]["properties"]
        self.assertFalse({"force", "supersedes"} & set(props))

    async def test_validation_rejections_are_logged(self):
        cases = [
            ({"kind": "Other"}, "kind 只能是"),
            ({"purpose": ""}, "purpose 必填"),
            ({"purpose": "faq"}, "purpose 必填"),
            ({"content": ""}, "必填"),
            ({"kind": "Engineer"}, "entry_path"),
            ({"kind": "Engineer", "entry_path": "C:/somewhere/else/x.cls"}, "repo 相對路徑"),
            ({"topic": "題" * 101}, "topic 超過 100 字"),
            ({"content": "字" * 2001}, "content 超過 2000 字"),
        ]
        for kw, expect in cases:
            with self.subTest(expect=expect, kw=list(kw)):
                self.assertIn(expect, await self.remember(**kw))
                log = self.last_log()
                self.assertEqual(log["WRITE_OUTCOME"], "rejected")
                self.assertTrue(log["ERROR_MSG"])
        self.assertEqual(self.inserted, [])

    async def test_absolute_entry_path_inside_repo_is_normalised(self):
        await self.remember(kind="Engineer", entry_path=r"C:\D\EHRMS_GIT\VB\EHRMS\Shift.cls")
        self.assertEqual(self.inserted[0][0][5], "VB/EHRMS/Shift.cls")

    async def test_data_fix_sql_block_is_checked(self):
        bad = "解鎖假單：\n```sql\nUPDATE HRMS_LEAVE SET IS_LOCK = 0\n```"
        text = await self.remember(purpose="data_fix", content=bad)
        self.assertIn("SQL 未通過檢查", text)
        self.assertIn("缺少 WHERE", text)
        self.assertEqual(self.inserted, [])

        good = ("解鎖假單，替換員工編號：\n```sql\nBEGIN TRAN\nUPDATE HRMS_LEAVE SET IS_LOCK = 0 WHERE EMPLOYEE_ID = 1\n"
                "-- COMMIT\nROLLBACK\n```")
        self.assertIn("已記住", await self.remember(purpose="data_fix", content=good))
        # 同樣的 SQL 若標成一般知識則不檢查（只有 data_fix 會檢查）
        self.assertIn("已記住", await self.remember(purpose="general", topic="另一個主題：假單鎖定欄位", content=bad,
                                                 keywords="假單,鎖定"))

    async def test_db_failure_is_reported_and_logged(self):
        with mock.patch.object(tq.mdb, "insert_memory", side_effect=RuntimeError("db down")):
            self.assertIn("寫入失敗", await self.remember())
        log = self.last_log()
        self.assertEqual(log["WRITE_OUTCOME"], "failed")
        self.assertIn("RuntimeError", log["ERROR_MSG"])

    async def test_requires_identity(self):
        other = base.CallContext(client_addr="10.9.9.9", session_id="s")
        self.assertIn("尚未確認使用者身分", await self.d.call(other, "remember", {"kind": "System"}))
        self.assertEqual(self.inserted, [])


class PurposeDisplayTests(WriteBase):
    async def test_purpose_and_data_fix_note_shown_on_read(self):
        rows = base.MEMORIES + [base.mem(50, "System", "解鎖已關帳假單的語法", "解鎖已關帳假單要先確認未計薪。",
                                         keywords="解鎖,假單", meta={"purpose": "data_fix"})]
        with mock.patch.object(tq.mdb, "load_all", return_value=rows):
            found = await self.d.call(CTX, "kb_search", {"query": "解鎖已關帳假單", "sources": ["memory"]})
            full = await self.d.call(CTX, "kb_get", {"type": "memory", "id": "50"})
        self.assertIn("〔資料修正語法〕", found)
        self.assertIn("僅供參考", full)
        self.assertIn("AI 不可自動執行", full)


class FlagTests(WriteBase):
    async def test_flag_is_stored_and_shown_without_changing_content(self):
        text = await self.d.call(CTX, "kb_flag", {
            "type": "faq", "id": "48", "reason": "答案沒有提到要先看特休產生批次",
            "evidence": "memory#1、EHRMSONE-31192", "jira_key": "EHRMSONE-34003"})
        self.assertIn("已標記疑慮 #1：faq#48", text)
        flag, = self.table("HRMS_KB_FLAG")
        self.assertEqual((flag["TARGET_TYPE"], flag["TARGET_ID"], flag["FLAG_SOURCE"], flag["CREATED_BY"],
                          flag["JIRA_KEY"], flag["STATUS"], flag["CANDIDATE_ID"]),
                         ("faq", "48", "ai", "amy.lin(AI)", "EHRMSONE-34003", "open", None))
        log = self.last_log()
        self.assertEqual((log["WRITE_OUTCOME"], log["WRITE_REF"], log["JIRA_KEY"]),
                         ("flagged", "flag#1", "EHRMSONE-34003"))
        full = await self.d.call(CTX, "kb_get", {"type": "faq", "id": "48"})
        self.assertIn("請確認特休是否已產生。", full)               # 原內容不變
        self.assertIn("答案沒有提到要先看特休產生批次", full)
        self.assertEqual(self.inserted, [])

    async def test_wiki_rule_flag_shows_on_the_page(self):
        text = await self.d.call(CTX, "kb_flag", {"type": "wiki", "id": "card#r1", "reason": "與程式行為不符",
                                                  "evidence": "VB/EHRMS/Card.cls :: Match"})
        self.assertIn("wiki#CARD#R1", text)
        self.assertEqual(self.table("HRMS_KB_FLAG")[0]["TARGET_ID"], "CARD#R1")
        found = await self.d.call(CTX, "kb_search", {"query": "打卡 刷卡異常", "sources": ["wiki"]})
        self.assertIn("⚠ 有疑慮：R1：與程式行為不符", found)

    async def test_unknown_target_or_missing_evidence_is_rejected(self):
        cases = [
            ({"type": "faq", "id": "999", "reason": "x", "evidence": "y"}, "找不到 faq#999"),
            ({"type": "wiki", "id": "CARD#R99", "reason": "x", "evidence": "y"}, "找不到 wiki#CARD#R99"),
            ({"type": "wiki", "id": "NOPE", "reason": "x", "evidence": "y"}, "找不到 wiki#NOPE"),
            ({"type": "custom", "id": "1", "reason": "x", "evidence": "y"}, "type 只能是"),
            ({"type": "faq", "id": "48", "reason": "只是覺得怪", "evidence": ""}, "都必填"),
            ({"type": "faq", "id": "48", "reason": "", "evidence": "y"}, "都必填"),
        ]
        for args, expect in cases:
            with self.subTest(expect=expect):
                self.assertIn(expect, await self.d.call(CTX, "kb_flag", args))
                self.assertEqual(self.last_log()["WRITE_OUTCOME"], "rejected")
        self.assertEqual(self.table("HRMS_KB_FLAG"), [])


if __name__ == "__main__":
    unittest.main()
