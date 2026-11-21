# -*- coding: utf-8 -*-
"""線上知識庫 MCP 的 skill 提供（skill_list／skill_get）與常駐說明的單元測試。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_kb_online_query as base  # noqa: E402
from kb_online import server, tools_skill as ts  # noqa: E402

CTX = base.CTX


class RealManifestTests(base.QueryBase):
    """用 repo 裡真正的清單與 SKILL.md：清單寫錯或 skill 被改名時這裡會失敗。"""

    def test_every_listed_skill_exists(self):
        listed = [e["name"] for e in json.loads(ts.MANIFEST.read_text(encoding="utf-8"))["skills"]]
        self.assertTrue(listed)
        self.assertEqual(list(ts._load_manifest()), listed)

    async def test_list_and_get_crisis_triage(self):
        listing = await self.d.call(CTX, "skill_list", {})
        self.assertIn("**crisis-triage**", listing)
        self.assertEqual(json.loads(self.last_log()["RESULT_REFS"]), [{"type": "skill", "id": "crisis-triage", "score": None}])

        text = await self.d.call(CTX, "skill_get", {"name": "Crisis-Triage"})
        self.assertIn("線上知識庫使用須知", text)
        self.assertIn("`kb_search`", text)                     # 工具對照表
        self.assertIn("這個流程在線上使用時的補充", text)
        self.assertIn("維運單不等於 bug", text)                  # 流程本文
        self.assertNotIn("\nname: crisis-triage", text)        # frontmatter 已去掉
        self.assertLess(text.index("線上知識庫使用須知"), text.index("核心規則"))
        log = self.last_log()
        self.assertEqual((log["TOOL_NAME"], log["NO_RESULT"], log["USER_NAME"]), ("skill_get", 0, "amy.lin"))

    def test_instructions_cover_identity_query_writeback_and_skill_index(self):
        text = server.INSTRUCTIONS + ts.index_text()
        for token in ("confirm_identity", "kb_search", "kb_get", "remember", "kb_flag", "version_bulletins",
                      "為什麼這次沒有寫入", 'skill_get(name="crisis-triage")'):
            self.assertIn(token, text)
        self.assertLess(len(text), 2500)                       # 常駐說明每次都會載入，要保持精簡


class ManifestHandlingTests(base.QueryBase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        (root / "skills" / "demo-flow").mkdir(parents=True)
        (root / "skills" / "demo-flow" / "SKILL.md").write_text(
            "---\nname: demo-flow\ndescription: 示範流程的說明\n---\n\n# 示範流程\n\n第一步。\n", encoding="utf-8")
        self.manifest = root / "online_skills.json"
        for target, value in (("SKILLS_DIR", root / "skills"), ("MANIFEST", self.manifest)):
            p = mock.patch.object(ts, target, value)
            p.start()
            self.addCleanup(p.stop)

    def write(self, skills):
        self.manifest.write_text(json.dumps({"skills": skills}, ensure_ascii=False), encoding="utf-8")

    async def test_new_skill_is_served_after_adding_one_manifest_entry(self):
        self.write([{"name": "demo-flow", "when": "示範情境時"}])
        self.assertIn("- 示範情境時 → skill_get(name=\"demo-flow\")", ts.index_text())
        text = await self.d.call(CTX, "skill_get", {"name": "demo-flow"})
        self.assertIn("# 示範流程", text)
        self.assertNotIn("這個流程在線上使用時的補充", text)   # 沒寫 note 就不顯示

    async def test_description_used_when_no_when(self):
        self.write([{"name": "demo-flow"}])
        self.assertIn("示範流程的說明", await self.d.call(CTX, "skill_list", {}))

    async def test_invalid_entries_are_skipped_not_served(self):
        self.write([{"name": "missing-skill", "when": "x"}, {"name": "../../secrets", "when": "x"},
                    {"name": "demo-flow", "when": "示範情境時"}])
        self.assertEqual(list(ts._load_manifest()), ["demo-flow"])
        for name in ("missing-skill", "../../secrets", "..", ""):
            with self.subTest(name=name):
                text = await self.d.call(CTX, "skill_get", {"name": name})
                self.assertIn("沒有提供", text)
                self.assertIn("demo-flow", text)
                self.assertEqual(self.last_log()["NO_RESULT"], 1)

    async def test_missing_or_broken_manifest_means_no_skills(self):
        self.assertEqual(ts.index_text(), "")                  # 清單檔不存在
        self.manifest.write_text("{ not json", encoding="utf-8")
        self.assertEqual(ts.index_text(), "")
        self.assertIn("目前沒有提供任何流程", await self.d.call(CTX, "skill_list", {}))

    async def test_requires_identity(self):
        self.write([{"name": "demo-flow"}])
        other = base.CallContext(client_addr="10.9.9.9", session_id="s")
        self.assertIn("尚未確認使用者身分", await self.d.call(other, "skill_get", {"name": "demo-flow"}))


if __name__ == "__main__":
    unittest.main()
