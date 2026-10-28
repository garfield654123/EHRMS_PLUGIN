"""Network-free regression tests for Jira response and failure handling."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key, value in {"JIRA_BASE_URL": "https://example.invalid", "JIRA_EMAIL": "test", "JIRA_API_TOKEN": "test"}.items():
    os.environ[key] = value

import httpx
from jira_mcp.client import JiraClient


class JiraClientTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, status=200, payload=None, error=None, method="GET"):
        def handler(request):
            self.assertEqual(request.method, method)
            if error:
                raise error("simulated offline", request=request)
            return httpx.Response(status, json=payload or {"ok": True})
        session = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("jira_mcp.client.httpx.AsyncClient", return_value=session):
            return await JiraClient()._request(method, "issue/TEST-1")

    async def test_successful_get_returns_json(self):
        self.assertEqual(await self.invoke(payload={"key": "TEST-1"}), {"key": "TEST-1"})

    async def test_successful_post_returns_json(self):
        self.assertEqual(await self.invoke(201, {"id": "1"}, method="POST"), {"id": "1"})

    async def test_known_http_errors(self):
        for status in (400, 401, 403, 404, 410):
            with self.subTest(status=status), self.assertRaises(ValueError):
                await self.invoke(status)

    async def test_other_http_errors(self):
        for status in (429, 500):
            with self.subTest(status=status), self.assertRaises(httpx.HTTPStatusError):
                await self.invoke(status)

    async def test_transport_failure_keeps_cause(self):
        for error in (httpx.ConnectError, httpx.ReadTimeout):
            with self.subTest(error=error), self.assertRaises(ValueError) as caught:
                await self.invoke(error=error)
            self.assertIsInstance(caught.exception.__cause__, error)

    async def test_issue_detail_consumes_successful_response(self):
        session = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"key": "TEST-1", "fields": {"summary": "Regression case"}})))
        with patch("jira_mcp.client.httpx.AsyncClient", return_value=session):
            issue = await JiraClient().get_issue_basic("TEST-1", comments_limit=0)
        self.assertEqual(issue["key"], "TEST-1")
        self.assertEqual(issue["summary"], "Regression case")


if __name__ == "__main__":
    unittest.main()
