from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bellhaven.config import Config, ROOT
from bellhaven.crm import CRMError
from bellhaven.demo import demo_accounts
from bellhaven.matching import address_key, match
from bellhaven.sandbox import SandboxCRM, UncertainWrite
from bellhaven.scraper import scrape_demo
from bellhaven.service import Service
from bellhaven.store import Store


def raw_account(account):
    return {"account_id": account["id"], "name": account["name"] + (" (Parent Account)" if account["account_kind"] == "corporate" else ""),
            "billing_street": account["address"], "billing_city": account["city"], "billing_state": account["state"], "billing_zip": account["zip"],
            "care_type": account["care_offerings"][0], "parent_id": account["parent_id"] or "", "parent_name": "",
            "status": account["status"], "lifetime_revenue": account["lifetime_revenue"], "outstanding_ar": account["outstanding_ar"],
            "chow_current_account": account["chow_current_account"] or "", "duplicate_of_account": account["duplicate_of_account"] or "",
            "phone": "", "note": account["note"], "updated_at": "baseline", "created_by_candidate": False}


class MemorySandbox(SandboxCRM):
    def __init__(self):
        self.records = {a["id"]: raw_account(a) for a in demo_accounts()}
        self.writes = []
        self.lose_post = self.lose_patch = self.ignore_patch = False
        self.after_create = None

    def list_accounts(self):
        return [self.normalize(deepcopy(r)) for r in self.records.values()]

    def get(self, account_id):
        return self.normalize(deepcopy(self.records[account_id]))

    def write(self, method, account_id, payload):
        allowed = {"name", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type", "parent_id",
                   "status", "phone", "note", "chow_current_account", "duplicate_of_account"}
        assert not set(payload) - allowed
        self.writes.append((method, account_id, deepcopy(payload)))
        if method == "POST":
            account_id = "created-" + str(len(self.records))
            record = {"account_id": account_id, "lifetime_revenue": 0, "outstanding_ar": 0, "status": "Active",
                      "phone": "", "note": "", "parent_name": "", "chow_current_account": "", "duplicate_of_account": "",
                      "created_by_candidate": True, "updated_at": "created", **payload}
            self.records[account_id] = record
            if self.after_create:
                self.after_create(self)
            if self.lose_post:
                self.lose_post = False
                raise UncertainWrite("Response lost after committed POST")
        else:
            record = self.records[account_id]
            if not self.ignore_patch:
                record.update(payload)
                record["updated_at"] = "changed-" + str(len(self.writes))
            if self.lose_patch:
                self.lose_patch = False
                raise UncertainWrite("Response lost after committed PATCH")
        return deepcopy(record)


class LiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "live.sqlite")
        self.crm = MemorySandbox()
        self.config = Config(mode="live", db_path=self.store.path, parent_id="bellhaven-parent")
        self.service = Service(self.config, self.store, self.crm)
        self.source = scrape_demo(ROOT)
        self.patcher = patch("bellhaven.service.scrape_website", return_value=self.source)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.service.scan()

    def proposal(self, kind):
        return next(p for p in self.store.proposals() if p["kind"] == kind)

    def approve(self, kind):
        return self.service.decide(self.proposal(kind)["id"], "approve", "Contract test")

    def test_live_scan_rejection_and_missing_reviewer_never_write(self):
        self.service.scan()
        p = self.proposal("create")
        with self.assertRaises(ValueError):
            self.service.decide(p["id"], "approve", "")
        self.service.decide(p["id"], "reject", "Reviewer", "Needs more evidence")
        self.service.scan()
        self.assertEqual(self.crm.writes, [])
        self.assertEqual(self.store.proposal(p["id"])["state"], "rejected")

    def test_live_chow_preserves_every_historical_field_except_link(self):
        before = deepcopy(self.crm.records["cedar"])
        result = self.approve("chow")
        self.assertEqual(result["state"], "applied")
        old = self.crm.records["cedar"]
        for key in before:
            if key not in {"chow_current_account", "updated_at"}:
                self.assertEqual(old[key], before[key], key)
        new = self.crm.records[old["chow_current_account"]]
        self.assertEqual((new["lifetime_revenue"], new["outstanding_ar"]), (0, 0))
        self.assertEqual(new["parent_id"], "bellhaven-parent")
        self.assertEqual([w[0] for w in self.crm.writes], ["POST", "PATCH"])
        self.assertEqual(self.crm.writes[1][2], {"chow_current_account": new["account_id"]})
        self.service.scan()
        self.assertEqual(self.store.proposal(result["id"])["state"], "applied")

    def test_post_response_loss_is_reconciled_without_repeating_post(self):
        self.crm.lose_post = True
        result = self.approve("chow")
        self.assertEqual(result["state"], "uncertain")
        result = self.service.decide(result["id"], "retry", "Reviewer")
        self.assertEqual(result["state"], "applied")
        self.assertEqual([w[0] for w in self.crm.writes], ["POST", "PATCH"])

    def test_patch_response_loss_is_reconciled_without_repeating_patch(self):
        self.crm.lose_patch = True
        result = self.approve("chow")
        self.assertEqual(result["state"], "uncertain")
        result = self.service.decide(result["id"], "retry", "Reviewer")
        self.assertEqual(result["state"], "applied")
        self.assertEqual([w[0] for w in self.crm.writes], ["POST", "PATCH"])

    def test_completed_patch_can_be_reconciled_after_parent_becomes_inactive(self):
        self.crm.lose_patch = True
        result = self.approve("reparent")
        self.assertEqual(result["state"], "uncertain")
        self.crm.records["bellhaven-parent"]["status"] = "Inactive"
        result = self.service.decide(result["id"], "retry", "Reviewer")
        self.assertEqual(result["state"], "applied")
        self.assertEqual(len(self.crm.writes), 1)

    def test_missing_marker_result_never_triggers_a_second_create(self):
        self.crm.lose_post = True
        result = self.approve("chow")
        created = next(r for r in self.crm.records.values() if r["created_by_candidate"])
        created["note"] = "Marker removed by another editor"
        result = self.service.decide(result["id"], "retry", "Reviewer")
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(len(self.crm.writes), 1)

    def test_multiple_marker_matches_remain_uncertain(self):
        self.crm.lose_post = True
        result = self.approve("chow")
        created = deepcopy(next(r for r in self.crm.records.values() if r["created_by_candidate"]))
        created["account_id"] = "conflicting-copy"
        self.crm.records[created["account_id"]] = created
        self.assertEqual(self.service.decide(result["id"], "retry", "Reviewer")["state"], "uncertain")
        self.assertEqual(len(self.crm.writes), 1)

    def test_historical_account_is_rechecked_after_successor_post(self):
        self.crm.after_create = lambda crm: crm.records["cedar"].update(outstanding_ar=0)
        result = self.approve("chow")
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(self.crm.records["cedar"]["chow_current_account"], "")
        self.assertEqual(len(self.crm.writes), 1)

    def test_changed_successor_is_not_linked_on_resume(self):
        self.crm.lose_post = True
        result = self.approve("chow")
        created = next(r for r in self.crm.records.values() if r["created_by_candidate"])
        created["parent_id"] = "harborview-parent"
        self.assertEqual(self.service.decide(result["id"], "retry", "Reviewer")["state"], "uncertain")
        self.assertEqual(len(self.crm.writes), 1)

    def test_financial_change_before_approval_does_not_write(self):
        self.crm.records["cedar"]["outstanding_ar"] = 0
        self.assertEqual(self.approve("chow")["state"], "stale")
        self.assertEqual(self.crm.writes, [])

    def test_failed_patch_readback_is_never_marked_applied_or_retried(self):
        self.crm.ignore_patch = True
        result = self.approve("reparent")
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(self.service.decide(result["id"], "retry", "Reviewer")["state"], "uncertain")
        self.assertEqual(len(self.crm.writes), 1)

    def test_changed_duplicate_survivor_stops_write(self):
        self.crm.records["river"]["billing_city"] = "Columbus"
        self.assertEqual(self.approve("duplicate")["state"], "stale")
        self.assertEqual(self.crm.writes, [])

    def test_account_order_cannot_repropose_an_investigation(self):
        p = self.proposal("investigate")
        self.service.decide(p["id"], "reviewed", "Reviewer", "Awaiting ownership evidence")
        self.crm.records = dict(reversed(list(self.crm.records.items())))
        self.service.scan()
        same = [q for q in self.store.proposals() if q["title"] == p["title"]]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["state"], "reviewed")

    def test_primary_care_aliases_avoid_false_updates(self):
        facilities = deepcopy(self.source.facilities)
        facility = next(f for f in facilities if f["key"] == "brook")
        facility["care_offerings"] = ["Short-Term Rehabilitation & Nursing"]
        self.crm.records["brook"]["care_type"] = "Skilled Nursing"
        proposals, matched = match(facilities, self.crm.list_accounts(), "bellhaven-parent", True)
        self.assertTrue(any(m["account_id"] == "brook" for m in matched))
        self.assertFalse(any(p["title"] == facility["name"] for p in proposals))

    def test_payload_uses_observed_fields_and_never_writes_financials(self):
        payload = SandboxCRM.payload({"address": "1250 NW Franklin St", "care_offerings": ["Memory Support", "Assisted Living"]})
        self.assertEqual(payload, {"billing_street": "1250 NW Franklin St", "care_type": "Assisted Living"})
        with self.assertRaises(CRMError):
            SandboxCRM.payload({"outstanding_ar": 0})

    def test_direction_and_pike_aliases_keep_facility_identity(self):
        a = {"address": "1250 Northwest Franklin Street", "city": "Chesterton", "state": "IN", "zip": "46304"}
        b = {**a, "address": "1250 NW Franklin St"}
        self.assertEqual(address_key(a), address_key(b))
        self.assertEqual(address_key({**a, "address": "3313 Wilmington Pike"}), address_key({**a, "address": "3313 Wilmington Pk"}))

    def test_pagination_total_change_is_rejected(self):
        crm = SandboxCRM("https://example.invalid", "test-token")
        record = raw_account(demo_accounts()[0])
        with patch.object(crm, "request", side_effect=[{"data": [record], "page": 1, "total": 2}, {"data": [], "page": 2, "total": 1}]):
            with self.assertRaises(CRMError):
                crm.list_accounts()


if __name__ == "__main__":
    unittest.main()
