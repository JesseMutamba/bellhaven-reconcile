from contextlib import contextmanager
import fcntl
import json
import os

from .config import ROOT
from .crm import CRMError, Conflict
from .matching import match, address_key, needs_chow, material
from .scraper import scrape_demo, scrape_website
from .store import encode, now
from .sandbox import WriteRejected


class Service:
    def __init__(self, config, store, crm):
        self.config, self.store, self.crm = config, store, crm

    @contextmanager
    def lock(self):
        descriptor = os.open(str(self.store.path) + ".lock", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another scan or approval is running; try again shortly") from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def scan(self):
        with self.lock():
            if any(p["state"] in {"applying", "retryable", "uncertain"} for p in self.store.proposals()):
                raise ValueError("Resolve the unfinished approved operation before starting another scan")
            with self.store.connect() as db:
                run_id = db.execute("INSERT INTO runs(started_at,status) VALUES (?, 'running')", (now(),)).lastrowid
            try:
                source = scrape_demo(ROOT) if self.config.mode == "demo" else scrape_website(self.config)
                accounts = self.crm.list_accounts()
                parents = [a for a in accounts if a["id"] == self.config.parent_id]
                if not parents:
                    raise ValueError("Configured Bellhaven parent account was not found in the complete CRM snapshot")
                proposals, matched = match(source.facilities, accounts, self.config.parent_id, source.absence_allowed)
                if self.config.mode == "live":
                    for p in proposals:
                        if p["kind"] != "investigate":
                            p["plan"]["crm_fields"] = self.crm.payload(p["plan"]["changes"], p["plan"]["before"] if p["kind"] not in {"create", "chow"} else None)
                            if "care_type" in p["plan"]["crm_fields"]:
                                p["plan"]["evidence"].append("The CRM stores one primary care type: " + p["plan"]["crm_fields"]["care_type"] + ". All website offerings remain in the source evidence.")
                            if p["kind"] in {"create", "chow"}:
                                p["plan"]["evidence"].append("The new account's note will include a reconciliation reference so interrupted creates can be recovered without duplication.")
                added = 0
                with self.store.connect() as db:
                    for page in source.pages:
                        db.execute("INSERT INTO snapshots(run_id,url,sha256,content) VALUES (?,?,?,?)",
                                   (run_id, page["url"], page["sha256"], page["content"]))
                    for p in proposals:
                        cursor = db.execute("INSERT OR IGNORE INTO proposals(id,kind,title,state,plan,run_id,created_at) VALUES (?,?,?,'pending',?,?,?)",
                                            (p["id"], p["kind"], p["title"], encode(p["plan"]), run_id, now()))
                        added += cursor.rowcount
                        db.execute("UPDATE proposals SET state='pending',plan=?,run_id=?,error=NULL,decided_at=NULL,reviewer=NULL,reason=NULL,progress='{}' WHERE id=? AND state='stale'",
                                   (encode(p["plan"]), run_id, p["id"]))
                    current = {p["id"] for p in proposals}
                    for row in db.execute("SELECT id FROM proposals WHERE state='pending'").fetchall():
                        if row["id"] not in current:
                            db.execute("UPDATE proposals SET state='superseded' WHERE id=?", (row["id"],))
                            self.store.event(db, row["id"], "superseded", {"run_id": run_id})
                    summary = {"facilities": len(source.facilities), "accounts": len(accounts), "pages": len(source.pages),
                               "new_proposals": added, "matched": matched, "absence_review_enabled": source.absence_allowed}
                    db.execute("UPDATE runs SET status='complete',finished_at=?,summary=? WHERE id=?", (now(), encode(summary), run_id))
                return {"run_id": run_id, **summary}
            except Exception as exc:
                with self.store.connect() as db:
                    db.execute("UPDATE runs SET status='failed',finished_at=?,error=? WHERE id=?", (now(), str(exc), run_id))
                raise

    def state(self):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        last_run = dict(row) if row else None
        if last_run and last_run["summary"]:
            last_run["summary"] = json.loads(last_run["summary"])
        return {"mode": self.config.mode, "parent_id": self.config.parent_id,
                "proposals": self.store.proposals(), "last_run": last_run,
                "writes_available": True}

    def decide(self, proposal_id, decision, reviewer, reason=""):
        if decision not in {"approve", "reject", "reviewed", "retry"}:
            raise ValueError("Unknown decision")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 120:
            raise ValueError("Enter your name to record the decision")
        if not isinstance(reason, str) or len(reason) > 2000:
            raise ValueError("Review note must be at most 2,000 characters")
        with self.lock():
            p = self.store.proposal(proposal_id)
            if decision == "retry":
                allowed = {"retryable", "applying", "uncertain"} if self.config.mode == "live" else {"retryable", "applying"}
                if p["state"] not in allowed:
                    raise ValueError("This proposal cannot be retried automatically")
            elif p["state"] != "pending":
                raise ValueError("This proposal has already been decided or superseded")
            if decision in {"reject", "reviewed"}:
                if not reason.strip():
                    raise ValueError("Add a reason so future reviewers understand the decision")
                if decision == "reviewed" and p["kind"] != "investigate":
                    raise ValueError("Only investigation items can be marked reviewed")
                state = "rejected" if decision == "reject" else "reviewed"
                with self.store.connect() as db:
                    db.execute("UPDATE proposals SET state=?,reviewer=?,reason=?,decided_at=? WHERE id=?",
                               (state, reviewer.strip(), reason.strip(), now(), proposal_id))
                    self.store.event(db, proposal_id, state, {"reviewer": reviewer.strip(), "reason": reason.strip()})
                return self.store.proposal(proposal_id)
            if p["kind"] == "investigate":
                raise ValueError("This item needs investigation and has no executable CRM mutation")
            with self.store.connect() as db:
                db.execute("UPDATE proposals SET state='applying',reviewer=?,reason=?,decided_at=?,error=NULL WHERE id=?",
                           (reviewer.strip(), reason.strip(), now(), proposal_id))
                self.store.event(db, proposal_id, "retry" if decision == "retry" else "approved",
                                 {"reviewer": reviewer.strip(), "reason": reason.strip()})
            try:
                self.apply(p)
            except Conflict as exc:
                # Partial CHOW creation must be reconciled, not silently proposed again.
                progress = self.store.proposal(proposal_id)["progress"]
                state = "uncertain" if progress.get("started") and (self.config.mode == "live" or p["kind"] in {"create", "chow"}) else "stale"
                self.store.finish(proposal_id, state, str(exc))
            except WriteRejected as exc:
                self.store.finish(proposal_id, "retryable", str(exc))
            except CRMError as exc:
                self.store.finish(proposal_id, "uncertain" if self.config.mode == "live" else "retryable", str(exc))
            except Exception:
                self.store.finish(proposal_id, "uncertain", "Unexpected failure; inspect the audit record before taking further action")
                raise
            else:
                self.store.finish(proposal_id, "applied")
            return self.store.proposal(proposal_id)

    def apply(self, proposal):
        if self.config.mode == "live":
            from .live import LiveExecutor
            return LiveExecutor(self.store, self.crm, self.config.parent_id).apply(proposal)
        pid, kind, plan = proposal["id"], proposal["kind"], proposal["plan"]
        progress = dict(proposal["progress"])
        before, changes = plan["before"], plan["changes"]
        parent = self.crm.get(self.config.parent_id)
        if parent.get("status") != "Active":
            raise Conflict("The proposed parent is no longer active")
        for dependency in plan["dependencies"]:
            current = self.crm.get(dependency["id"])
            if current != dependency or current.get("duplicate_of_account") or current.get("chow_current_account") or current.get("status") != "Active":
                raise Conflict("The proposed surviving duplicate account changed; scan again")
        if before:
            current = self.crm.get(before["id"])
            if current != before:
                expected = dict(before)
                if kind == "chow" and progress.get("created_id"):
                    expected["chow_current_account"] = progress["created_id"]
                elif kind != "chow":
                    expected.update(changes)
                if not progress.get("started") or material(current) != material(expected):
                    raise Conflict("CRM data changed after the proposal was reviewed. Run a new scan.")
            if kind == "chow" and not needs_chow(current):
                raise Conflict("Billing conditions changed; the ownership change must be reviewed again")
        if kind in {"create", "chow"} and not progress.get("started"):
            existing = self.crm.list_accounts()
            for account in existing:
                if before and account["id"] == before["id"]:
                    continue
                if address_key(account) == address_key(plan["facility"]) and not account.get("duplicate_of_account") and not account.get("chow_current_account"):
                    raise Conflict("A possible current account exists at this address; review before creating another")
        progress["started"] = True
        self.store.save_progress(pid, progress)
        if kind in {"create", "chow"}:
            created = self.crm.create(changes, pid + ":create")
            progress["created_id"] = created["id"]
            self.store.save_progress(pid, progress)
            if kind == "chow":
                successor = self.crm.get(created["id"])
                if (any(successor.get(k) != v for k, v in changes.items())
                        or successor.get("duplicate_of_account") or successor.get("chow_current_account")):
                    raise Conflict("The successor account changed after creation; reconcile it before linking the historical account")
                # Only this pointer changes on the historical account; balances and parent stay intact.
                self.crm.update(before["id"], {"chow_current_account": created["id"]}, before["version"], pid + ":link")
        else:
            self.crm.update(before["id"], changes, before["version"], pid + ":update")
