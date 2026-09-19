"""Approval-driven writes without assuming undocumented server idempotency."""
import json

from .crm import Conflict
from .matching import address_key, needs_chow
from .sandbox import UncertainWrite, WriteRejected
from .store import digest, encode, now


class LiveExecutor:
    def __init__(self, store, crm, parent_id):
        self.store, self.crm, self.parent_id = store, crm, parent_id

    @staticmethod
    def matches(raw, payload, baseline=None):
        expected = {**(baseline or {}), **payload}
        return all(raw.get(k) == v for k, v in expected.items() if k not in {"updated_at", "parent_name"})

    def parent_active(self):
        if self.crm.get(self.parent_id).get("status") != "Active":
            raise Conflict("The proposed parent is no longer active")

    def unchanged(self, before):
        current = self.crm.get(before["id"])
        if current["_raw"] != before["_raw"]:
            raise Conflict("CRM data changed since review. Refresh the proposal before applying it.")
        return current

    def progress(self, proposal_id, **values):
        progress = self.store.proposal(proposal_id)["progress"]
        progress.update(values)
        self.store.save_progress(proposal_id, progress)

    def journal(self, key):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM crm_operations WHERE operation_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def finish_operation(self, key, record):
        with self.store.connect() as db:
            db.execute("UPDATE crm_operations SET state='applied',result=?,updated_at=? WHERE operation_key=?",
                       (encode(record["_raw"]), now(), key))
        return record

    def reconcile(self, operation):
        payload = json.loads(operation["payload"])
        baseline = json.loads(operation["baseline"]) if operation["baseline"] else None
        if operation["method"] == "POST":
            marker = operation["marker"]
            candidates = [a for a in self.crm.list_accounts() if marker in a.get("note", "")]
            if len(candidates) != 1:
                raise UncertainWrite("The earlier create has no unique confirmed result. No second account was created; investigate before resuming.")
            record = self.crm.get(candidates[0]["id"])
        else:
            record = self.crm.get(operation["account_id"])
        if not self.matches(record["_raw"], payload, baseline):
            raise UncertainWrite("The CRM does not match the approved operation. Its outcome remains uncertain; no request was repeated.")
        return self.finish_operation(operation["operation_key"], record)

    def operation(self, proposal_id, step, method, payload, guard, before=None):
        key = proposal_id + ":" + step
        account_id = before["id"] if before else None
        baseline = before["_raw"] if before else None
        marker = "[Bellhaven operation " + key + "]" if method == "POST" else None
        payload = dict(payload)
        if marker:
            payload["note"] = "\n".join(filter(None, [payload.get("note", ""), marker]))
        signature = digest([method, account_id, payload, baseline])
        existing = self.journal(key)
        if existing and existing["state"] in {"sent", "applied"}:
            if existing["signature"] != signature:
                raise UncertainWrite("An earlier operation has different recorded inputs; reconcile it before changing the plan")
            return self.reconcile(existing)
        with self.store.connect() as db:
            db.execute("""INSERT INTO crm_operations(operation_key,proposal_id,method,account_id,payload,baseline,marker,signature,state,updated_at)
                          VALUES (?,?,?,?,?,?,?,?,'prepared',?)
                          ON CONFLICT(operation_key) DO UPDATE SET payload=excluded.payload,baseline=excluded.baseline,
                          signature=excluded.signature,state='prepared',updated_at=excluded.updated_at""",
                       (key, proposal_id, method, account_id, encode(payload), encode(baseline) if baseline else None, marker, signature, now()))
        guard()
        with self.store.connect() as db:
            db.execute("UPDATE crm_operations SET state='sent',updated_at=? WHERE operation_key=?", (now(), key))
            self.store.event(db, proposal_id, "crm_request_started", {"operation": key, "method": method, "account_id": account_id})
        self.progress(proposal_id, started=True)
        try:
            response = self.crm.write(method, account_id, payload)
        except WriteRejected:
            with self.store.connect() as db:
                db.execute("UPDATE crm_operations SET state='rejected',updated_at=? WHERE operation_key=?", (now(), key))
            raise
        if method == "POST":
            if isinstance(response, dict) and isinstance(response.get("account_id"), str):
                record = self.crm.get(response["account_id"])
                if not self.matches(record["_raw"], payload):
                    raise UncertainWrite("Created account failed read-back verification; check the result before continuing")
            else:
                return self.reconcile(self.journal(key))
        else:
            record = self.crm.get(account_id)
            if not self.matches(record["_raw"], payload, baseline):
                raise UncertainWrite("Updated account failed read-back verification; the write will not be repeated")
        return self.finish_operation(key, record)

    def apply(self, proposal):
        pid, kind, plan = proposal["id"], proposal["kind"], proposal["plan"]
        before, changes = plan["before"], plan["changes"]
        def create_guard():
            self.parent_active()
            if before:
                current = self.unchanged(before)
                if not needs_chow(current) or current.get("chow_current_account"):
                    raise Conflict("The historical account no longer qualifies for a successor")
            for candidate in self.crm.list_accounts():
                if before and candidate["id"] == before["id"]:
                    continue
                if (candidate.get("account_kind") != "corporate" and address_key(candidate) == address_key(plan["facility"])
                        and not candidate.get("duplicate_of_account") and not candidate.get("chow_current_account")):
                    raise Conflict("A potential facility account appeared at this address; review before creating another")

        def update_guard():
            self.parent_active()
            self.unchanged(before)
            for dependency in plan["dependencies"]:
                survivor = self.unchanged(dependency)
                if survivor.get("status") != "Active" or survivor.get("duplicate_of_account") or survivor.get("chow_current_account"):
                    raise Conflict("The surviving account is no longer eligible")

        if kind in {"create", "chow"}:
            payload = self.crm.payload(changes)
            created = self.operation(pid, "create", "POST", payload, create_guard)
            self.progress(pid, created_id=created["id"])
            if kind == "chow":
                successor = self.crm.get(created["id"])
                if (not self.matches(successor["_raw"], payload)
                        or successor.get("chow_current_account") or successor.get("duplicate_of_account")):
                    raise Conflict("The successor is no longer eligible for the ownership link")
                def link_guard():
                    self.parent_active()
                    old = self.unchanged(before)
                    if not needs_chow(old):
                        raise Conflict("Billing changed while creating the successor; investigate before linking")
                    successor = self.crm.get(created["id"])
                    if (not self.matches(successor["_raw"], payload)
                            or successor.get("chow_current_account") or successor.get("duplicate_of_account")):
                        raise Conflict("The successor changed before linkage; investigate before continuing")
                self.operation(pid, "link", "PATCH", {"chow_current_account": created["id"]}, link_guard, before)
        else:
            self.operation(pid, "update", "PATCH", self.crm.payload(changes, before), update_guard, before)
