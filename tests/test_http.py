import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bellhaven.config import Config
from bellhaven.server import start_server


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = Config(db_path=Path(self.directory.name) / "http.sqlite", port=0)
        self.server, self.service = start_server(config)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.service.scan()

    def request(self, path, body=None, headers=None):
        request = Request(self.base + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json", **(headers or {})})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_approval_round_trip_uses_api_and_persists_decision(self):
        state = self.request("/api/state")
        headers = {"X-Review-Token": state["review_token"]}
        p = next(p for p in state["proposals"] if p["kind"] == "chow")
        result = self.request("/api/decision", {"id": p["id"], "decision": "approve", "reviewer": "HTTP test"}, headers)
        self.assertEqual(result["state"], "applied")
        account = self.service.crm.get("cedar")
        self.assertEqual(account["parent_id"], "harborview-parent")
        self.assertEqual(account["outstanding_ar"], 9500)
        self.assertIsNotNone(account["chow_current_account"])
        self.request("/api/scan", {}, headers)
        self.assertEqual(self.service.store.proposal(p["id"])["state"], "applied")

    def test_mutation_requires_review_token(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/scan", {})
        self.assertEqual(caught.exception.code, 403)

    def test_cross_origin_request_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/state", headers={"Origin": "https://another.example"})
        self.assertEqual(caught.exception.code, 403)

    def test_demo_crm_has_separate_authentication(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/demo-crm/accounts")
        self.assertEqual(caught.exception.code, 401)

    def test_review_page_is_served_with_content_security_policy(self):
        with urlopen(self.base + "/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertIn(b"Ownership review", response.read())


if __name__ == "__main__":
    unittest.main()
