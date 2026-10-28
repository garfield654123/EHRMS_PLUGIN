import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ehrms_launcher", PLUGIN_ROOT / "scripts/run_mcp.py")
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)
JIRA_VALUES = {
    "JIRA_BASE_URL": "https://example.invalid",
    "JIRA_EMAIL": "test@example.invalid",
    "JIRA_API_TOKEN": "fake-token",
}
DB_VALUES = {
    "MSSQL_SERVER": "invalid.example",
    "MSSQL_DATABASE": "test",
    "MSSQL_USERNAME": "test",
    "MSSQL_PASSWORD": "fake-password",
}
EXPECTED_TOOLS = {
    "EHRMS-jira-mcp": {
        "get_issue", "get_issue_summary", "search_issues", "get_comments",
        "get_attachment", "get_issue_changelog", "get_user_info",
        "list_custom_fields", "add_comment",
    },
    "ehrms-database": {
        "echo", "mssql_query", "mssql_test_connection", "search_tables",
        "analyze_table_joins", "get_table_columns", "db_mcp_init",
        "db_generate_query", "db_validate_query",
    },
    "ehrms-memory": {
        "recall", "remember", "jira_lookup", "jira_log", "faq_lookup",
        "bulletin_lookup", "task_lookup", "sop_lookup", "wiki_lookup",
        "faq_log", "task_log", "sop_log",
        "custom_sa_log", "custom_log",
    },
}


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def write_shared(self, values):
        target = self.home / ".ehrms-tools/.env"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
        return target

    def write_legacy(self, values):
        target = self.home / ".claude/settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"env": values}), encoding="utf-8-sig")

    def test_environment_wins_over_shared_and_legacy(self):
        self.write_shared({**JIRA_VALUES, "JIRA_API_TOKEN": "shared"})
        self.write_legacy({**JIRA_VALUES, "JIRA_API_TOKEN": "legacy"})
        environ = {**JIRA_VALUES, "JIRA_API_TOKEN": "process"}
        LAUNCHER.load_environment("jira", environ, self.home)
        self.assertEqual(environ["JIRA_API_TOKEN"], "process")
        self.assertEqual(environ["TRANSPORT"], "stdio")

    def test_shared_config_works_without_claude(self):
        self.write_shared(JIRA_VALUES)
        environ = LAUNCHER.load_environment("jira", {}, self.home)
        self.assertEqual(environ["JIRA_EMAIL"], JIRA_VALUES["JIRA_EMAIL"])
        self.assertFalse((self.home / ".claude").exists())

    def test_shared_wins_and_legacy_fills_missing_values(self):
        self.write_shared({"JIRA_API_TOKEN": "shared"})
        self.write_legacy({**JIRA_VALUES, "UNRELATED_SECRET": "must-not-load"})
        environ = LAUNCHER.load_environment("jira", {}, self.home)
        self.assertEqual(environ["JIRA_API_TOKEN"], "shared")
        self.assertNotIn("UNRELATED_SECRET", environ)

    def test_explicit_dotenv_preserves_literal_dollar_characters(self):
        target = self.write_shared({**JIRA_VALUES, "JIRA_API_TOKEN": "fake-${NOT_EXPANDED}"})
        environ = LAUNCHER.load_environment("jira", {"EHRMS_ENV_FILE": str(target)}, self.home)
        self.assertEqual(environ["JIRA_API_TOKEN"], "fake-${NOT_EXPANDED}")

    def test_invalid_explicit_dotenv_fails_without_silent_fallback(self):
        self.write_legacy(JIRA_VALUES)
        with self.assertRaisesRegex(ValueError, "EHRMS_ENV_FILE"):
            LAUNCHER.load_environment("jira", {"EHRMS_ENV_FILE": str(self.home / "missing")}, self.home)

    def test_missing_credentials_only_report_names(self):
        with self.assertRaises(ValueError) as caught:
            LAUNCHER.load_environment("jira", {"JIRA_API_TOKEN": "do-not-display"}, self.home)
        self.assertIn("JIRA_EMAIL", str(caught.exception))
        self.assertNotIn("do-not-display", str(caught.exception))

    def test_unexpanded_environment_placeholder_uses_fallback(self):
        self.write_shared(JIRA_VALUES)
        environ = LAUNCHER.load_environment("jira", {"JIRA_EMAIL": "${JIRA_EMAIL}"}, self.home)
        self.assertEqual(environ["JIRA_EMAIL"], JIRA_VALUES["JIRA_EMAIL"])

    def test_memory_loads_database_and_identity_without_jira_token(self):
        self.write_shared({**DB_VALUES, "JIRA_EMAIL": "author@example.invalid"})
        environ = LAUNCHER.load_environment("memory", {}, self.home)
        self.assertEqual(environ["JIRA_EMAIL"], "author@example.invalid")
        self.assertNotIn("JIRA_API_TOKEN", environ)

    def test_invalid_legacy_shape_reports_missing_settings(self):
        self.write_legacy(["invalid"])
        with self.assertRaisesRegex(ValueError, "Missing EHRMS settings"):
            LAUNCHER.load_environment("jira", {}, self.home)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def test_manifests_share_identity_version_and_servers(self):
        manifests = [
            json.loads((PLUGIN_ROOT / folder / "plugin.json").read_text(encoding="utf-8"))
            for folder in (".claude-plugin", ".codex-plugin")
        ]
        self.assertEqual(manifests[0]["name"], manifests[1]["name"])
        self.assertEqual(manifests[0]["version"], manifests[1]["version"])
        for manifest in manifests:
            config = json.loads((PLUGIN_ROOT / manifest["mcpServers"]).read_text(encoding="utf-8"))
            self.assertEqual(set(config["mcpServers"]), set(EXPECTED_TOOLS))

    async def test_both_platform_configs_handshake_after_relocation(self):
        with tempfile.TemporaryDirectory(prefix="ehrms plugin ") as directory:
            temporary = Path(directory)
            moved = temporary / "快取 路徑" / "ehrms-tools"
            shutil.copytree(PLUGIN_ROOT, moved, ignore=shutil.ignore_patterns("__pycache__"))
            credentials = temporary / "test.env"
            values = {**JIRA_VALUES, **DB_VALUES, "MEMORY_USER": "offline-test"}
            credentials.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
            for filename in (".mcp.json", "mcp.claude.json"):
                config = json.loads((moved / filename).read_text(encoding="utf-8"))
                for name, server in config["mcpServers"].items():
                    with self.subTest(platform=filename, server=name):
                        params = StdioServerParameters(
                            command=server["command"],
                            args=[value.replace("${CLAUDE_PLUGIN_ROOT}", str(moved)) for value in server["args"]],
                            cwd=str(moved / server["cwd"]) if "cwd" in server else str(temporary),
                            env={"EHRMS_ENV_FILE": str(credentials), "PYTHONDONTWRITEBYTECODE": "1"},
                        )
                        await asyncio.wait_for(self.check_server(params, name), timeout=25)

    async def check_server(self, params, name):
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init = await session.initialize()
                result = await session.list_tools()
                self.assertEqual({tool.name for tool in result.tools}, EXPECTED_TOOLS[name])
                if name == "ehrms-memory":
                    # context.md 以伺服器說明常駐載入（搬移路徑後仍要讀得到）
                    self.assertIn("EHRMS 知識庫使用說明", init.instructions or "")
                if name == "ehrms-database":
                    result = await session.call_tool("echo", {"message": "offline-ping"})
                    self.assertFalse(result.isError)
                    self.assertIn("offline-ping", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
