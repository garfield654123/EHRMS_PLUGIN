# -*- coding: utf-8 -*-
"""線上知識庫 MCP 骨架的單元測試：身分確認、把關、使用紀錄與斷線補送（不連 Jira、不連 MSSQL）。"""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "KB_ONLINE"))

from kb_online.dispatch import CallContext, Dispatcher, NEED_IDENTITY, Registry, ToolResult, find_jira_key  # noqa: E402
from kb_online.identity import IdentityError, IdentityService  # noqa: E402
from kb_online.store import SqliteStore  # noqa: E402
from kb_online.tools_core import registry as core_registry  # noqa: E402
from kb_online.usage_log import UsageLogger  # noqa: E402


def user(email="amy.lin@example.com", active=True, account_id="acc-1", account_type="atlassian"):
    return {"accountId": account_id, "displayName": "Amy", "emailAddress": email,
            "active": active, "accountType": account_type}


class FakeDirectory:
    def __init__(self, users=None, error=None, configured=True):
        self.users, self.error, self._configured, self.queries = users or [], error, configured, []

    def configured(self):
        return self._configured

    async def search(self, email):
        self.queries.append(email)
        if self.error:
            raise self.error
        return self.users


class BrokenStore:
    """模擬資料庫斷線：使用紀錄寫不進去。"""
    def __init__(self):
        self.fail, self.rows = True, []

    def insert_usage(self, row):
        if self.fail:
            raise RuntimeError("db down")
        self.rows.append(row)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SqliteStore(os.path.join(self.tmp.name, "kb.db"))
        self.addCleanup(self.store.close)   # 後註冊的先執行：先關資料庫再刪暫存資料夾
        self.spool = os.path.join(self.tmp.name, "spool.jsonl")

    def dispatcher(self, directory, registry=core_registry, store=None):
        store = store or self.store
        return Dispatcher(registry, IdentityService(self.store, directory), UsageLogger(store, self.spool),
                          "9.9.9", "keyword-test")


CTX = CallContext(client_addr="10.1.2.3", session_id="sess-a", client_name="claude-code 2.1")


class IdentityTests(Base):
    async def test_confirm_binds_address_and_is_remembered(self):
        svc = IdentityService(self.store, FakeDirectory([user()]))
        self.assertIsNone(await svc.resolve("10.1.2.3"))
        ident = await svc.confirm("10.1.2.3", " Amy.Lin@Example.com ")
        self.assertEqual((ident.name, ident.email, ident.source), ("amy.lin", "amy.lin@example.com", "jira_list"))
        # 換一個服務實例（等於伺服器重啟）仍然記得；其他位址不受影響
        again = IdentityService(self.store, FakeDirectory())
        self.assertEqual((await again.resolve("10.1.2.3")).name, "amy.lin")
        self.assertIsNone(await again.resolve("10.9.9.9"))

    async def test_latest_binding_wins_for_same_address(self):
        svc = IdentityService(self.store, FakeDirectory([user()]))
        await svc.confirm("10.1.2.3", "amy.lin@example.com")
        svc2 = IdentityService(self.store, FakeDirectory([user("bob.wu@example.com", account_id="acc-2")]))
        await svc2.confirm("10.1.2.3", "bob.wu@example.com")
        self.assertEqual((await IdentityService(self.store, FakeDirectory()).resolve("10.1.2.3")).name, "bob.wu")

    async def test_rejections(self):
        cases = [
            ("not-an-email", FakeDirectory([user()]), "格式"),
            ("amy.lin@example.com", FakeDirectory([]), "找不到"),
            ("amy.lin@example.com", FakeDirectory([user(active=False)]), "找不到"),
            ("amy.lin@example.com", FakeDirectory([user("other@example.com")]), "找不到"),
            ("amy.lin@example.com", FakeDirectory([user(account_type="app")]), "找不到"),
            ("amy.lin@example.com", FakeDirectory([user(), user(account_id="acc-9")]), "多個帳號"),
            ("amy.lin@example.com", FakeDirectory(error=RuntimeError("boom")), "失敗"),
            ("amy.lin@example.com", FakeDirectory(configured=False), "尚未設定"),
        ]
        for email, directory, expect in cases:
            with self.subTest(email=email, expect=expect):
                svc = IdentityService(self.store, directory)
                with self.assertRaises(IdentityError) as cm:
                    await svc.confirm("10.1.2.3", email)
                self.assertIn(expect, str(cm.exception))
                self.assertIsNone(await svc.resolve("10.1.2.3"))

    async def test_hidden_email_accepted_only_when_single_result(self):
        svc = IdentityService(self.store, FakeDirectory([user(email="")]))
        self.assertEqual((await svc.confirm("10.1.2.3", "amy.lin@example.com")).name, "amy.lin")
        svc2 = IdentityService(self.store, FakeDirectory([user(email=""), user(email="", account_id="acc-2")]))
        with self.assertRaises(IdentityError):
            await svc2.confirm("10.5.5.5", "amy.lin@example.com")

    async def test_domain_restriction(self):
        svc = IdentityService(self.store, FakeDirectory([user()]), email_domains=("104.com.tw",))
        with self.assertRaises(IdentityError) as cm:
            await svc.confirm("10.1.2.3", "amy.lin@example.com")
        self.assertIn("網域", str(cm.exception))
        self.assertEqual(svc.directory.queries, [])   # 網域不符就不該去查 Jira


class DispatchTests(Base):
    def registry_with_search(self):
        reg = Registry()
        reg.tools.update(core_registry.tools)

        @reg.tool("kb_search", "測試用", {"type": "object"})
        async def kb_search(call, args):
            return ToolResult(f"hi {call.identity.name}", refs=[{"type": "faq", "id": 48, "score": 0.71}],
                              no_result=False, query_text=args["query"])

        @reg.tool("boom", "測試用", {"type": "object"})
        async def boom(call, args):
            raise ValueError("壞掉了")
        return reg

    async def test_gate_blocks_until_identity_confirmed(self):
        d = self.dispatcher(FakeDirectory([user()]), self.registry_with_search())
        self.assertEqual(await d.call(CTX, "kb_search", {"query": "特休"}), NEED_IDENTITY)
        self.assertIn("已確認身分", await d.call(CTX, "confirm_identity", {"email": "amy.lin@example.com"}))
        self.assertEqual(await d.call(CTX, "kb_search", {"query": "EHRMSONE-31192 特休時數不足"}), "hi amy.lin")

        rows = self.store.usage_rows()
        self.assertEqual([r["CALL_SEQ"] for r in rows], [1, 2, 3])
        self.assertEqual([r["TOOL_NAME"] for r in rows], ["kb_search", "confirm_identity", "kb_search"])
        blocked, confirmed, searched = rows
        self.assertEqual((blocked["USER_NAME"], blocked["IDENTITY_SOURCE"], blocked["ERROR_MSG"]),
                         ("unknown", "none", "identity required"))
        self.assertEqual((confirmed["USER_NAME"], confirmed["IDENTITY_SOURCE"]), ("amy.lin", "jira_list"))
        self.assertEqual(searched["USER_EMAIL"], "amy.lin@example.com")
        self.assertEqual(searched["QUERY_TEXT"], "EHRMSONE-31192 特休時數不足")
        self.assertEqual(searched["JIRA_KEY"], "EHRMSONE-31192")
        self.assertEqual(json.loads(searched["RESULT_REFS"]), [{"type": "faq", "id": 48, "score": 0.71}])
        self.assertEqual((searched["RESULT_COUNT"], searched["NO_RESULT"]), (1, 0))
        self.assertEqual((searched["SESSION_ID"], searched["CLIENT_ADDR"], searched["ORIGIN"], searched["CLIENT_NAME"]),
                         ("sess-a", "10.1.2.3", "chat", "claude-code 2.1"))
        self.assertEqual((searched["SERVER_VERSION"], searched["SEARCH_VERSION"]), ("9.9.9", "keyword-test"))

    async def test_failed_confirm_is_logged_and_does_not_unlock(self):
        d = self.dispatcher(FakeDirectory([]), self.registry_with_search())
        self.assertIn("找不到", await d.call(CTX, "confirm_identity", {"email": "ghost@example.com"}))
        self.assertEqual(await d.call(CTX, "kb_search", {"query": "x"}), NEED_IDENTITY)
        first = self.store.usage_rows()[0]
        self.assertEqual(first["USER_NAME"], "unknown")
        self.assertIn("找不到", first["ERROR_MSG"])

    async def test_tool_error_and_unknown_tool_are_logged_not_raised(self):
        d = self.dispatcher(FakeDirectory([user()]), self.registry_with_search())
        await d.call(CTX, "confirm_identity", {"email": "amy.lin@example.com"})
        self.assertIn("壞掉了", await d.call(CTX, "boom", {}))
        self.assertIn("沒有這個工具", await d.call(CTX, "nope", {}))
        rows = self.store.usage_rows()
        self.assertIn("ValueError", rows[1]["ERROR_MSG"])
        self.assertEqual(rows[2]["ERROR_MSG"], "unknown tool")

    async def test_kb_info_works_without_identity(self):
        d = self.dispatcher(FakeDirectory())
        text = await d.call(CTX, "kb_info", {})
        self.assertIn("9.9.9", text)
        self.assertIn("尚未確認", text)

    async def test_sequence_is_per_session(self):
        d = self.dispatcher(FakeDirectory())
        other = CallContext(client_addr="10.1.2.3", session_id="sess-b")
        for ctx in (CTX, other, CTX):
            await d.call(ctx, "kb_info", {})
        self.assertEqual([(r["SESSION_ID"], r["CALL_SEQ"]) for r in self.store.usage_rows()],
                         [("sess-a", 1), ("sess-b", 1), ("sess-a", 2)])

    def test_find_jira_key(self):
        self.assertEqual(find_jira_key({"ref_key": "ehrmsone-123"}), "EHRMSONE-123")
        self.assertEqual(find_jira_key({"query": "看一下 EHRMS-77 的問題"}), "EHRMS-77")
        self.assertIsNone(find_jira_key({"query": "特休 SLC01 設定", "top_k": 3}))


class ConfigTests(unittest.TestCase):
    def test_env_sources_priority(self):
        from kb_online import config
        keep = {k: os.environ.get(k) for k in config.CONNECTION_KEYS + ("KB_PORT", "KB_ENV_FILE")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in keep.items()])
        for k in keep:
            os.environ.pop(k, None)
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".ehrms-tools"))
            os.makedirs(os.path.join(home, ".claude"))
            with open(os.path.join(home, ".ehrms-tools", ".env"), "w", encoding="utf-8") as f:
                f.write("KB_PORT=9001\nMSSQL_SERVER=from-env-file\n")
            with open(os.path.join(home, ".claude", "settings.json"), "w", encoding="utf-8") as f:
                json.dump({"env": {"MSSQL_SERVER": "from-settings", "MSSQL_DATABASE": "db", "KB_PORT": "9999",
                                   "OTHER_SECRET": "x"}}, f)
            os.environ["MSSQL_DATABASE"] = "already-set"
            config.load_env_file(home=home)
        self.assertEqual(os.environ["KB_PORT"], "9001")                 # 設定檔的值
        self.assertEqual(os.environ["MSSQL_SERVER"], "from-env-file")   # 設定檔優先於 settings.json
        self.assertEqual(os.environ["MSSQL_DATABASE"], "already-set")   # 已在環境裡的不覆蓋
        self.assertNotIn("OTHER_SECRET", os.environ)                    # settings.json 只補連線設定
        os.environ.pop("OTHER_SECRET", None)


class UsageLogTests(Base):
    async def test_spool_when_db_down_then_flush_in_order(self):
        broken = BrokenStore()
        d = self.dispatcher(FakeDirectory(), store=broken)
        self.assertIn("9.9.9", await d.call(CTX, "kb_info", {}))      # 紀錄失敗不影響工具回應
        await d.call(CTX, "kb_info", {})
        with open(self.spool, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 2)

        broken.fail = False
        await d.call(CTX, "kb_info", {})                               # 恢復後下一次成功寫入會順便補送
        self.assertFalse(os.path.exists(self.spool))
        self.assertEqual(sorted(r["CALL_SEQ"] for r in broken.rows), [1, 2, 3])

    def test_flush_keeps_remaining_when_still_down(self):
        broken = BrokenStore()
        logger = UsageLogger(broken, self.spool)
        for i in range(3):
            logger.record_sync({"TOOL_NAME": f"t{i}"})
        self.assertEqual(logger.flush_spool(), 0)
        with open(self.spool, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 3)
        broken.fail = False
        self.assertEqual(logger.flush_spool(), 3)
        self.assertEqual([r["TOOL_NAME"] for r in broken.rows], ["t0", "t1", "t2"])

    def test_long_values_are_truncated_to_column_limits(self):
        logger = UsageLogger(self.store, self.spool)
        row = {c: None for c in ("USER_EMAIL", "CLIENT_ADDR", "CLIENT_NAME", "ARGUMENTS", "JIRA_KEY", "RESULT_REFS",
                                 "RESULT_COUNT", "NO_RESULT", "WRITE_OUTCOME", "WRITE_REF", "ELAPSED_MS", "ERROR_MSG",
                                 "SEARCH_VERSION")}
        row.update(EVENT_TIME="2026-11-20 10:00:00.000", USER_NAME="u", IDENTITY_SOURCE="none", SESSION_ID="s",
                   CALL_SEQ=1, ORIGIN="chat", TOOL_NAME="t", QUERY_TEXT="字" * 5000, SERVER_VERSION="1")
        logger.record_sync(row)
        self.assertEqual(len(self.store.usage_rows()[0]["QUERY_TEXT"]), 2000)


if __name__ == "__main__":
    unittest.main()
