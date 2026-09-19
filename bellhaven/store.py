from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                    status TEXT NOT NULL, summary TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
                    url TEXT NOT NULL, sha256 TEXT NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                    state TEXT NOT NULL, plan TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES runs(id),
                    created_at TEXT NOT NULL, decided_at TEXT, reviewer TEXT, reason TEXT,
                    progress TEXT NOT NULL DEFAULT '{}', error TEXT);
                CREATE INDEX IF NOT EXISTS idx_proposals_state ON proposals(state);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL REFERENCES proposals(id),
                    at TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS crm_operations (
                    operation_key TEXT PRIMARY KEY, proposal_id TEXT NOT NULL REFERENCES proposals(id),
                    method TEXT NOT NULL, account_id TEXT, payload TEXT NOT NULL, baseline TEXT,
                    marker TEXT, signature TEXT NOT NULL, state TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL);
            """)
            existing = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if existing and existing[0] != "1":
                raise ValueError("Unsupported local database schema version")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
            db.execute("PRAGMA optimize")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind_namespace(self, namespace):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES ('namespace',?)", (namespace,))
            if db.execute("SELECT value FROM meta WHERE key='namespace'").fetchone()[0] != namespace:
                raise ValueError("This database belongs to another CRM/mode. Use a separate DB_PATH.")

    def proposals(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM proposals ORDER BY created_at DESC, rowid").fetchall()
        return [self.decode(row) for row in rows]

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("Proposal not found")
        result = dict(row)
        result["plan"] = json.loads(result["plan"])
        result["progress"] = json.loads(result["progress"])
        return result

    def proposal(self, proposal_id):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone())

    def event(self, db, proposal_id, event, detail):
        db.execute("INSERT INTO audit(proposal_id,at,event,detail) VALUES (?,?,?,?)",
                   (proposal_id, now(), event, encode(detail)))

    def save_progress(self, proposal_id, progress):
        with self.connect() as db:
            db.execute("UPDATE proposals SET progress=? WHERE id=?", (encode(progress), proposal_id))

    def finish(self, proposal_id, state, error=None):
        with self.connect() as db:
            db.execute("UPDATE proposals SET state=?,error=? WHERE id=?", (state, error, proposal_id))
            self.event(db, proposal_id, state, {"error": error})

    def history(self, proposal_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit WHERE proposal_id=? ORDER BY id", (proposal_id,))]
