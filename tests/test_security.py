from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from urllib.request import Request

from bellhaven.crm import HTTPCRM, CRMError
from bellhaven.scraper import fetch, SameOriginRedirect, ScrapeError
from bellhaven.store import Store


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.target_requests = []
        seen = self.target_requests

        class Target(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"must not be reached")

        target = self.start_server(Target)
        target_url = f"http://127.0.0.1:{target.server_port}/private"

        class Source(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/ok":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"same-origin page")
                    return
                self.send_response(302)
                self.send_header("Location", "/ok" if self.path == "/same-origin" else target_url)
                self.end_headers()

        source = self.start_server(Source)
        self.base = f"http://127.0.0.1:{source.server_port}"

    def start_server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_website_redirect_is_blocked_before_target_is_contacted(self):
        with self.assertRaises(ScrapeError):
            fetch(self.base + "/other-origin")
        self.assertEqual(self.target_requests, [])

    def test_same_origin_website_redirect_still_works(self):
        self.assertEqual(fetch(self.base + "/same-origin"), "same-origin page")

    def test_https_downgrade_is_blocked_before_dispatch(self):
        with self.assertRaises(ScrapeError):
            SameOriginRedirect().redirect_request(
                Request("https://bellhaven.example/"), None, 302, "Found", {}, "http://bellhaven.example/")

    def test_crm_redirect_does_not_forward_bearer_token(self):
        with self.assertRaises(CRMError):
            HTTPCRM(self.base, "fictional-test-credential").request("GET", "/other-origin")
        self.assertEqual(self.target_requests, [])


class PrivateStorageTests(unittest.TestCase):
    def test_database_and_journal_files_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "data" / "records.sqlite3")
            self.assertEqual(stat.S_IMODE(store.path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            with store.connect() as db:
                db.execute("INSERT INTO meta VALUES ('test', 'private')")
                for suffix in ("-wal", "-shm"):
                    path = Path(str(store.path) + suffix)
                    self.assertTrue(path.exists())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_database_is_secured_without_changing_arbitrary_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o755)
            path = parent / "existing.sqlite3"
            path.touch(mode=0o644)
            Store(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)


if __name__ == "__main__":
    unittest.main()
