"""A documented HTTP adapter and a local fake CRM with the same contract."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
import uuid

from .store import encode
from .network import tls_context


class CRMError(Exception):
    pass


class Conflict(CRMError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPCRM:
    def __init__(self, base_url, token=""):
        self.base_url, self.token = base_url.rstrip("/"), token
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=tls_context()))

    def request(self, method, path, body=None, key=None, version=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if key:
            headers["Idempotency-Key"] = key
        if version is not None:
            headers["If-Match"] = str(version)
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=encode(body).encode() if body is not None else None,
                      method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise CRMError("CRM response exceeded the size limit")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code in {409, 412}:
                raise Conflict("CRM record changed or idempotency key conflicts; refresh review") from None
            raise CRMError(f"CRM returned HTTP {exc.code}; no automatic retry was performed") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise CRMError("CRM request failed or returned invalid JSON; retry the approved operation after checking connectivity") from None

    def list_accounts(self):
        accounts, seen, cursor = [], set(), None
        for _ in range(1000):
            page = self.request("GET", "/accounts" + ("?" + urlencode({"cursor": cursor}) if cursor else ""))
            if not isinstance(page, dict) or not isinstance(page.get("accounts"), list):
                raise CRMError("Unexpected CRM list response; verify the adapter contract")
            accounts.extend(page["accounts"])
            cursor = page.get("next_cursor")
            if not cursor:
                ids = [a["id"] for a in accounts]
                if len(ids) != len(set(ids)):
                    raise CRMError("CRM pagination returned duplicate account IDs")
                return accounts
            if cursor in seen:
                raise CRMError("CRM pagination loop detected")
            seen.add(cursor)
        raise CRMError("CRM pagination limit reached; refusing an incomplete snapshot")

    def get(self, account_id):
        return self.request("GET", "/accounts/" + quote(account_id, safe=""))

    def create(self, fields, key):
        return self.request("POST", "/accounts", fields, key=key)

    def update(self, account_id, fields, version, key):
        return self.request("PATCH", "/accounts/" + quote(account_id, safe=""), fields, key, version)


class DemoCRM:
    """Server-side only. All review writes reach this through HTTPCRM."""
    def __init__(self, store):
        self.store = store

    def seed(self, accounts):
        with self.store.connect() as db:
            if db.execute("SELECT value FROM meta WHERE key='demo_seeded'").fetchone():
                return
            for account in accounts:
                db.execute("INSERT INTO accounts VALUES (?,?)", (account["id"], encode(account)))
            db.execute("INSERT INTO meta VALUES ('demo_seeded','true')")

    def list_accounts(self):
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM accounts ORDER BY id")]

    def get(self, account_id):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            raise ValueError("Account not found")
        return json.loads(row[0])

    def mutate(self, method, account_id, fields, key, version):
        if not key or not isinstance(fields, dict):
            raise ValueError("A JSON object and idempotency key are required")
        allowed = {"name", "address", "city", "state", "zip", "care_offerings", "parent_id", "status", "note",
                   "duplicate_of_account", "chow_current_account", "external_id"}
        if set(fields) - allowed:
            raise ValueError("Unknown or protected account fields")
        if "status" in fields and fields["status"] not in {"Active", "Inactive", "Needs Review"}:
            raise ValueError("Invalid account status")
        request_fingerprint = encode([method, account_id, fields, version])
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
            if previous:
                if previous["request"] != request_fingerprint:
                    raise Conflict("Idempotency key reused with different input")
                return json.loads(previous["response"])
            if method == "POST":
                if not all(fields.get(k) for k in ("name", "address", "city", "state", "zip", "parent_id")):
                    raise ValueError("Missing required facility fields")
                account_id = "demo-" + uuid.uuid4().hex[:12]
                record = {"id": account_id, "version": 1, "account_kind": "facility", "status": "Active",
                          "note": "", "lifetime_revenue": 0, "outstanding_ar": 0,
                          "duplicate_of_account": None, "chow_current_account": None, **fields}
            else:
                row = db.execute("SELECT body FROM accounts WHERE id=?", (account_id,)).fetchone()
                if not row:
                    raise ValueError("Account not found")
                record = json.loads(row[0])
                if str(record["version"]) != str(version):
                    raise Conflict("Account changed since review")
                record.update(fields)
                record["version"] += 1
            db.execute("INSERT INTO accounts VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (account_id, encode(record)))
            db.execute("INSERT INTO idempotency VALUES (?,?,?)", (key, request_fingerprint, encode(record)))
            return record
