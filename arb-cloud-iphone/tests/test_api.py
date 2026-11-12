import asyncio
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from app import main


async def request(path, method="GET", data=None, authorization=None):
    messages = []
    queue = asyncio.Queue()
    await queue.put({"type": "http.request", "body": json.dumps(data).encode() if data else b""})
    headers = [(b"content-type", b"application/json")]
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    scope = dict(type="http", asgi={"version": "3.0", "spec_version": "2.4"}, http_version="1.1",
                 method=method, scheme="http", path=path, raw_path=path.encode(),
                 query_string=b"", root_path="", headers=headers,
                 server=("test", 80), client=("127.0.0.1", 1234))
    async def send(message):
        messages.append(message)
    await asyncio.wait_for(main.app(scope, queue.get, send), timeout=10)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], body, dict(start["headers"])


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(main, "DB_PATH", os.path.join(self.temp.name, "ledger.db"))
        self.path_patch.start()
        self.env_patch = patch.dict(os.environ, {"ADMIN_PASSWORD": "test-secret", "ADMIN_USERNAME": "admin"})
        self.env_patch.start()
        main.init_db()
        self.auth = "Basic " + base64.b64encode(b"admin:test-secret").decode()

    def tearDown(self):
        self.env_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def call(self, path, **kwargs):
        return asyncio.run(request(path, **kwargs))

    def test_private_routes_and_public_health(self):
        for path in ["/", "/api/config", "/api/reports/sets.csv", "/docs", "/static/index.html"]:
            self.assertEqual(self.call(path)[0], 401)
        self.assertEqual(self.call("/api/health")[0], 200)
        self.assertEqual(self.call("/api/config", authorization=self.auth)[0], 200)
        self.assertEqual(self.call("/api/config", authorization="Basic !!!")[0], 401)

    def test_scan_open_settle_and_reports(self):
        data = dict(sport="Tennis", event="=HYPERLINK(1)", market="Winner", total_stake=125,
                    outcomes=[dict(selection=s, bookmaker=s, odds=2.1) for s in ["A", "B"]])
        with patch.object(main, "send_email", return_value=False), patch.object(main, "send_push", return_value=0):
            status, body, _ = self.call("/api/scan", method="POST", data=data, authorization=self.auth)
        self.assertEqual(status, 200)
        opportunity = json.loads(body)
        status, body, _ = self.call("/api/sets/open", method="POST", authorization=self.auth,
                                  data=dict(opportunity_id=opportunity["opportunity_id"], manual_confirmation=True))
        self.assertEqual(status, 200)
        set_id = json.loads(body)["set_id"]
        self.assertEqual(self.call(f"/api/sets/{set_id}/settle", method="POST", authorization=self.auth,
                                   data=dict(actual_pnl=-12.5))[0], 200)
        status, body, _ = self.call("/api/reports/summary", authorization=self.auth)
        self.assertEqual(json.loads(body)["realised_pnl"], -12.5)
        status, body, headers = self.call("/api/reports/sets.csv", authorization=self.auth)
        self.assertEqual(status, 200)
        self.assertIn(b"'=HYPERLINK", body)
        self.assertIn(b"-12.5", body)
        self.assertEqual(headers[b"cache-control"], b"no-store")
