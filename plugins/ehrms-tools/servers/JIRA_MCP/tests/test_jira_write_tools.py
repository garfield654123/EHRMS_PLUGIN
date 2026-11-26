"""Network-free tests for Jira write tools: update_issue_fields / transitions / worklog."""
import json
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

RealAsyncClient = httpx.AsyncClient


class FakeJira:
    """極簡假 Jira：記錄所有請求，依路徑回應，PUT/transition 會改變內部狀態。"""

    def __init__(self, status="SA Testing", labels=None, transitions=None, users=None):
        self.status = status
        self.fields = {
            "summary": "測試單", "assignee": None, "duedate": None,
            "labels": list(labels or []), "components": [],
        }
        self.transitions = transitions if transitions is not None else [
            {"id": "31", "name": "Send to CS", "to": {"name": "CS Testing"}},
            {"id": "41", "name": "Close", "to": {"name": "Closed"},
             "fields": {"resolution": {"name": "Resolution", "required": True}}},
        ]
        self.users = users or []
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.split("/rest/api/3/", 1)[-1]
        self.requests.append((request.method, path, dict(request.url.params), body))
        if request.method == "GET" and path == "issue/TEST-1":
            f = dict(self.fields, status={"name": self.status})
            f["components"] = [{"name": c} for c in self.fields["components"]]
            return httpx.Response(200, json={"key": "TEST-1", "fields": f})
        if request.method == "GET" and path == "issue/TEST-1/transitions":
            return httpx.Response(200, json={"transitions": self.transitions})
        if request.method == "POST" and path == "issue/TEST-1/transitions":
            tid = body["transition"]["id"]
            self.status = next(t["to"]["name"] for t in self.transitions if t["id"] == tid)
            return httpx.Response(204)
        if request.method == "PUT" and path == "issue/TEST-1":
            for op in (body.get("update") or {}).get("labels", []):
                if "add" in op and op["add"] not in self.fields["labels"]:
                    self.fields["labels"].append(op["add"])
                if "remove" in op and op["remove"] in self.fields["labels"]:
                    self.fields["labels"].remove(op["remove"])
            for k, v in (body.get("fields") or {}).items():
                self.fields[k] = v
            return httpx.Response(204)
        if request.method == "GET" and path == "user/search":
            return httpx.Response(200, json=self.users)
        if request.method == "POST" and path == "issue/TEST-1/worklog":
            return httpx.Response(201, json={
                "id": "9001", "timeSpent": body["timeSpent"], "started": body["started"],
                "author": {"displayName": "Tester"}})
        return httpx.Response(404, json={})

    def writes(self):
        return [r for r in self.requests if r[0] in ("POST", "PUT")]


class WriteToolTests(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, fake, coro_factory):
        # 每次 async with 都會關閉 client，故每次建新的
        def new_session(*args, **kwargs):
            return RealAsyncClient(transport=httpx.MockTransport(lambda r: fake.handler(r)))
        with patch("jira_mcp.client.httpx.AsyncClient", side_effect=new_session):
            return await coro_factory(JiraClient())

    # ── 204 處理 ──
    async def test_empty_204_response_returns_empty_dict(self):
        fake = FakeJira()
        result = await self.run_with(fake, lambda c: c._request("PUT", "issue/TEST-1", json_data={"fields": {}}))
        self.assertEqual(result, {})

    # ── update_issue_fields ──
    async def test_labels_use_update_add_remove_not_overwrite(self):
        fake = FakeJira(labels=["keep", "old"])
        result = await self.run_with(fake, lambda c: c.update_issue_fields(
            "TEST-1", add_labels=["new", " new "], remove_labels=["old"]))
        (method, path, _, body), = fake.writes()
        self.assertEqual((method, path), ("PUT", "issue/TEST-1"))
        self.assertEqual(body, {"update": {"labels": [{"add": "new"}, {"remove": "old"}]}})
        self.assertNotIn("fields", body)
        self.assertEqual(result["before"]["labels"], ["keep", "old"])
        self.assertEqual(result["after"]["labels"], ["keep", "new"])
        self.assertEqual(result["changed_fields"], ["labels"])

    async def test_whitelist_fields_body(self):
        fake = FakeJira(users=[{"accountId": "abc123", "displayName": "A",
                                "emailAddress": "a@example.invalid"}])
        await self.run_with(fake, lambda c: c.update_issue_fields(
            "TEST-1", add_components=["薪資"], remove_components=["假勤"],
            assignee="a@example.invalid", duedate="2026-12-31", summary=" 新標題 "))
        body = fake.writes()[0][3]
        self.assertEqual(body["update"], {"components": [
            {"add": {"name": "薪資"}}, {"remove": {"name": "假勤"}}]})
        self.assertEqual(body["fields"], {
            "summary": "新標題", "duedate": "2026-12-31",
            "assignee": {"accountId": "abc123"}})

    async def test_clear_assignee_and_duedate(self):
        fake = FakeJira()
        await self.run_with(fake, lambda c: c.update_issue_fields("TEST-1", assignee="", duedate=""))
        self.assertEqual(fake.writes()[0][3]["fields"],
                         {"duedate": None, "assignee": {"accountId": None}})
        self.assertFalse(any(r[1] == "user/search" for r in fake.requests))

    async def test_account_id_skips_lookup(self):
        fake = FakeJira()
        aid = "712020:" + "0" * 8 + "-1111-2222-3333-" + "4" * 12
        await self.run_with(fake, lambda c: c.update_issue_fields("TEST-1", assignee=aid))
        self.assertEqual(fake.writes()[0][3]["fields"]["assignee"], {"accountId": aid})
        self.assertFalse(any(r[1] == "user/search" for r in fake.requests))

    async def test_invalid_inputs_never_write(self):
        cases = [
            {},                                          # 沒有任何欄位
            {"add_labels": ["has space"]},
            {"add_labels": ["x"], "remove_labels": ["x"]},
            {"duedate": "2026/12/31"},
            {"summary": "   "},
            {"assignee": "王小明"},                       # 非 email 也非 accountId
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                fake = FakeJira()
                with self.assertRaises(ValueError):
                    await self.run_with(fake, lambda c: c.update_issue_fields("TEST-1", **kwargs))
                self.assertEqual(fake.writes(), [])

    async def test_ambiguous_or_missing_assignee_never_writes(self):
        many = [{"accountId": "a1", "displayName": "A1"}, {"accountId": "a2", "displayName": "A2"}]
        for users in ([], many):
            with self.subTest(users=len(users)):
                fake = FakeJira(users=users)
                with self.assertRaises(ValueError):
                    await self.run_with(fake, lambda c: c.update_issue_fields("TEST-1", assignee="x@example.invalid"))
                self.assertEqual(fake.writes(), [])

    # ── transitions ──
    async def test_list_transitions_is_read_only(self):
        fake = FakeJira()
        result = await self.run_with(fake, lambda c: c.list_transitions("TEST-1"))
        self.assertEqual(result["current_status"], "SA Testing")
        self.assertEqual(result["transitions"][0], {"id": "31", "name": "Send to CS", "to_status": "CS Testing"})
        self.assertEqual(result["transitions"][1]["required_fields"], ["Resolution"])
        self.assertIn("note", result)
        self.assertEqual(fake.writes(), [])
        tparams = next(r[2] for r in fake.requests if r[1].endswith("/transitions"))
        self.assertEqual(tparams.get("expand"), "transitions.fields")

    async def test_transition_executes_after_status_check(self):
        fake = FakeJira()
        result = await self.run_with(fake, lambda c: c.transition_issue("TEST-1", "31", "sa testing"))
        self.assertEqual(fake.writes(), [("POST", "issue/TEST-1/transitions", {}, {"transition": {"id": "31"}})])
        self.assertEqual((result["from_status"], result["to_status"]), ("SA Testing", "CS Testing"))
        self.assertEqual(result["transition"], "Send to CS")

    async def test_transition_rejects_stale_status(self):
        fake = FakeJira(status="CS Testing")
        with self.assertRaises(ValueError) as caught:
            await self.run_with(fake, lambda c: c.transition_issue("TEST-1", "31", "SA Testing"))
        self.assertIn("CS Testing", str(caught.exception))
        self.assertEqual(fake.writes(), [])

    async def test_transition_rejects_unavailable_id_or_missing_args(self):
        for tid, expected in (("99", "SA Testing"), ("", "SA Testing"), ("31", "")):
            with self.subTest(tid=tid, expected=expected):
                fake = FakeJira()
                with self.assertRaises(ValueError):
                    await self.run_with(fake, lambda c: c.transition_issue("TEST-1", tid, expected))
                self.assertEqual(fake.writes(), [])

    # ── worklog ──
    async def test_add_worklog_body(self):
        fake = FakeJira()
        result = await self.run_with(fake, lambda c: c.add_worklog(
            "TEST-1", " 1h   30m ", started="2026-11-26 14:05", comment="查 log\n寫 SQL"))
        (method, path, _, body), = fake.writes()
        self.assertEqual((method, path), ("POST", "issue/TEST-1/worklog"))
        self.assertEqual(body["timeSpent"], "1h 30m")
        self.assertRegex(body["started"], r"^2026-11-26T14:05:00\.000[+-]\d{4}$")
        self.assertEqual(len(body["comment"]["content"]), 2)
        self.assertEqual(result["worklog_id"], "9001")

    async def test_worklog_date_only_defaults_to_nine(self):
        self.assertRegex(JiraClient._jira_datetime("2026-11-26"), r"^2026-11-26T09:00:00\.000[+-]\d{4}$")
        self.assertRegex(JiraClient._jira_datetime(None), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000[+-]\d{4}$")

    async def test_worklog_invalid_inputs_never_write(self):
        for kwargs in ({"time_spent": "90"}, {"time_spent": "1.5小時"}, {"time_spent": ""},
                       {"time_spent": "1h", "started": "11/26"}):
            with self.subTest(kwargs=kwargs):
                fake = FakeJira()
                with self.assertRaises(ValueError):
                    await self.run_with(fake, lambda c: c.add_worklog("TEST-1", **kwargs))
                self.assertEqual(fake.writes(), [])

    async def test_add_comment_still_posts_adf(self):
        fake = FakeJira()
        fake_handler = fake.handler

        def handler(request):
            if request.url.path.endswith("/comment"):
                fake.requests.append(("POST", "comment", {}, json.loads(request.content)))
                return httpx.Response(201, json={"id": "1"})
            return fake_handler(request)
        fake.handler = handler
        await self.run_with(fake, lambda c: c.add_comment("TEST-1", "a\n\nb"))
        body = fake.writes()[0][3]["body"]
        self.assertEqual([p["content"] for p in body["content"]],
                         [[{"type": "text", "text": "a"}], [], [{"type": "text", "text": "b"}]])


if __name__ == "__main__":
    unittest.main()
