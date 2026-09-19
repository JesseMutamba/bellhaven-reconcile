from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from urllib.parse import urlsplit, unquote

from .config import ROOT
from .crm import HTTPCRM, DemoCRM, Conflict, CRMError
from .demo import demo_accounts
from .sandbox import SandboxCRM
from .service import Service
from .store import Store, encode, digest


class AppServer(ThreadingHTTPServer):
    daemon_threads = True


def start_server(config):
    store = Store(config.db_path)
    namespace = [config.mode, config.crm_url if config.mode == "live" else "demo", config.parent_id]
    if config.mode == "live":
        namespace.append(digest(config.crm_token))
    store.bind_namespace(encode(namespace))
    demo = DemoCRM(store)
    if config.mode == "demo":
        demo.seed(demo_accounts())
    review_token, crm_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    service = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json(self, status, value):
            self.send(status, encode(value))

        def body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 100_000:
                raise ValueError("Request body is missing or too large")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("Expected a JSON object")
            return value

        def route(self):
            port = self.server.server_port
            allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
            host = self.headers.get("Host")
            if host not in allowed_hosts:
                return self.json(403, {"error": "Local host required"})
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in allowed_hosts}:
                return self.json(403, {"error": "Cross-origin requests are not allowed"})
            path = urlsplit(self.path).path
            if path.startswith("/demo-crm/"):
                if config.mode != "demo" or self.headers.get("Authorization") != "Bearer " + crm_token:
                    return self.json(401, {"error": "Authentication required"})
                account_id = unquote(path[len("/demo-crm/accounts/"):]) if path.startswith("/demo-crm/accounts/") else None
                if self.command == "GET":
                    return self.json(200, demo.get(account_id) if account_id else {"accounts": demo.list_accounts(), "next_cursor": None})
                if self.command in {"POST", "PATCH"}:
                    record = demo.mutate(self.command, account_id, self.body(), self.headers.get("Idempotency-Key"), self.headers.get("If-Match"))
                    return self.json(201 if self.command == "POST" else 200, record)
            if self.command == "GET" and path == "/api/state":
                return self.json(200, {**service.state(), "review_token": review_token})
            if self.command == "GET" and path == "/api/accounts":
                return self.json(200, {"accounts": service.crm.list_accounts()})
            if self.command == "GET" and path.startswith("/api/history/"):
                return self.json(200, store.history(path.rsplit("/", 1)[-1]))
            if self.command == "POST" and path.startswith("/api/"):
                if not secrets.compare_digest(self.headers.get("X-Review-Token", ""), review_token):
                    return self.json(403, {"error": "Reload the review app before making changes"})
                data = self.body()
                if path == "/api/scan":
                    return self.json(200, service.scan())
                if path == "/api/decision":
                    result = service.decide(data.get("id"), data.get("decision"), data.get("reviewer"), data.get("reason", ""))
                    return self.json(200, result)
            if self.command == "GET" and path in {"/", "/app.js", "/style.css"}:
                filename = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                mime = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[filename]
                return self.send(200, (ROOT / "dist" / filename).read_bytes(), mime)
            self.json(404, {"error": "Not found"})

        def dispatch(self):
            try:
                self.route()
            except Conflict as exc:
                self.json(409, {"error": str(exc)})
            except (ValueError, KeyError, TypeError) as exc:
                self.json(400, {"error": str(exc)})
            except CRMError as exc:
                self.json(502, {"error": str(exc)})
            except Exception:
                self.json(500, {"error": "Operation failed; inspect the last run or audit record"})

        do_GET = do_POST = do_PATCH = dispatch

    server = AppServer(("127.0.0.1", config.port), Handler)
    crm = HTTPCRM(f"http://127.0.0.1:{server.server_port}/demo-crm", crm_token) if config.mode == "demo" else SandboxCRM(config.crm_url, config.crm_token)
    service = Service(config, store, crm)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, service
