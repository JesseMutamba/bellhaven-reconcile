from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bellhaven.config import Config, ROOT
from bellhaven.crm import DemoCRM, CRMError
from bellhaven.demo import demo_accounts
from bellhaven.matching import match, needs_chow
from bellhaven.scraper import parse_page, scrape_demo, ScrapeError
from bellhaven.service import Service
from bellhaven.store import Store, encode


class TestCRM(DemoCRM):
    lose_create_response = False
    lose_update_response = False

    def create(self, fields, key):
        value = self.mutate("POST", None, fields, key, None)
        if self.lose_create_response:
            self.lose_create_response = False
            raise CRMError("Simulated timeout after successful create")
        return value

    def update(self, account_id, fields, version, key):
        value = self.mutate("PATCH", account_id, fields, key, version)
        if self.lose_update_response:
            self.lose_update_response = False
            raise CRMError("Simulated timeout after successful update")
        return value


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Config(db_path=Path(self.directory.name) / "test.sqlite")
        self.store = Store(self.config.db_path)
        self.crm = TestCRM(self.store)
        self.crm.seed(demo_accounts())
        self.service = Service(self.config, self.store, self.crm)
        self.service.scan()

    def proposal(self, kind):
        return next(p for p in self.store.proposals() if p["kind"] == kind)

    def approve(self, kind):
        return self.service.decide(self.proposal(kind)["id"], "approve", "Test reviewer")

    def test_scan_is_read_only_and_classifies_all_scenarios(self):
        self.assertEqual(self.crm.list_accounts(), sorted(demo_accounts(), key=lambda a: a["id"]))
        self.assertEqual({p["kind"] for p in self.store.proposals()},
                         {"chow", "reparent", "update", "create", "duplicate", "investigate", "missing"})
        self.assertEqual(self.service.state()["last_run"]["summary"]["facilities"], 8)

    def test_rejected_proposal_stays_decided_across_reruns(self):
        p = self.proposal("create")
        self.service.decide(p["id"], "reject", "Reviewer", "The evidence is insufficient")
        count = len(self.store.proposals())
        result = self.service.scan()
        self.assertEqual(result["new_proposals"], 0)
        self.assertEqual(len(self.store.proposals()), count)
        self.assertEqual(self.store.proposal(p["id"])["state"], "rejected")
        self.assertEqual(len(self.crm.list_accounts()), len(demo_accounts()))

    def test_chow_preserves_historical_account_and_new_account_has_no_balances(self):
        before = self.crm.get("cedar")
        result = self.approve("chow")
        self.assertEqual(result["state"], "applied")
        old = self.crm.get("cedar")
        new = self.crm.get(old["chow_current_account"])
        for key in before:
            if key not in {"version", "chow_current_account"}:
                self.assertEqual(old[key], before[key], key)
        self.assertEqual(new["parent_id"], "bellhaven-parent")
        self.assertEqual((new["lifetime_revenue"], new["outstanding_ar"]), (0, 0))
        self.service.scan()
        self.assertFalse(any(p["kind"] == "chow" and p["state"] == "pending" for p in self.store.proposals()))

    def test_billing_rule_truth_table(self):
        for revenue, ar, expected in [(100, 10, True), (100, 0, False), (0, 10, False), (0, 0, False)]:
            with self.subTest(revenue=revenue, ar=ar):
                self.assertEqual(needs_chow({"lifetime_revenue": revenue, "outstanding_ar": ar}), expected)
        for bad in [None, "unknown", "NaN", "Infinity"]:
            with self.assertRaises(ValueError):
                needs_chow({"lifetime_revenue": bad, "outstanding_ar": 1})

    def test_reparent_with_zero_ar_keeps_account_identity(self):
        count = len(self.crm.list_accounts())
        self.assertEqual(self.approve("reparent")["state"], "applied")
        self.assertEqual(self.crm.get("maple")["parent_id"], "bellhaven-parent")
        self.assertEqual(len(self.crm.list_accounts()), count)

    def test_changed_finances_require_fresh_review(self):
        account = self.crm.get("cedar")
        account["outstanding_ar"] = 0
        account["version"] += 1
        with self.store.connect() as db:
            db.execute("UPDATE accounts SET body=? WHERE id=?", (encode(account), account["id"]))
        self.assertEqual(self.approve("chow")["state"], "stale")
        self.assertEqual(self.crm.get("cedar")["parent_id"], "harborview-parent")
        self.assertEqual(len(self.crm.list_accounts()), len(demo_accounts()))

    def test_unknown_finances_make_investigation_not_mutation(self):
        accounts = deepcopy(demo_accounts())
        next(a for a in accounts if a["id"] == "cedar")["outstanding_ar"] = None
        proposals, _ = match(scrape_demo(ROOT).facilities, accounts, "bellhaven-parent", True)
        p = next(p for p in proposals if p["title"] == "Bellhaven Cedar Grove")
        self.assertEqual(p["kind"], "investigate")

    def test_retry_after_create_response_loss_does_not_duplicate_successor(self):
        self.crm.lose_create_response = True
        p = self.approve("chow")
        self.assertEqual(p["state"], "retryable")
        count = len(self.crm.list_accounts())
        p = self.service.decide(p["id"], "retry", "Reviewer")
        self.assertEqual(p["state"], "applied")
        self.assertEqual(len(self.crm.list_accounts()), count)
        self.assertIsNotNone(self.crm.get("cedar")["chow_current_account"])

    def test_retry_after_link_response_loss_is_idempotent(self):
        self.crm.lose_update_response = True
        p = self.approve("chow")
        self.assertEqual(p["state"], "retryable")
        before_retry = self.crm.list_accounts()
        self.assertEqual(self.service.decide(p["id"], "retry", "Reviewer")["state"], "applied")
        self.assertEqual(self.crm.list_accounts(), before_retry)

    def test_simultaneous_approval_writes_once(self):
        pid = self.proposal("create")["id"]
        def approve():
            try:
                return self.service.decide(pid, "approve", "Reviewer")["state"]
            except ValueError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: approve(), range(2)))
        self.assertEqual(sorted(outcomes), ["applied", "conflict"])
        self.assertEqual(len(self.crm.list_accounts()), len(demo_accounts()) + 1)

    def test_unknown_create_outcome_and_old_account_change_block_further_scans(self):
        self.crm.lose_create_response = True
        p = self.approve("chow")
        account = self.crm.get("cedar")
        self.crm.update("cedar", {"note": "Changed by another reviewer"}, account["version"], "external-change")
        result = self.service.decide(p["id"], "retry", "Reviewer")
        self.assertEqual(result["state"], "uncertain")
        with self.assertRaises(ValueError):
            self.service.scan()

    def test_changed_successor_is_not_linked_on_retry(self):
        self.crm.lose_create_response = True
        p = self.approve("chow")
        successor = next(a for a in self.crm.list_accounts() if a["id"].startswith("demo-"))
        self.crm.update(successor["id"], {"parent_id": "harborview-parent", "status": "Inactive"}, successor["version"], "external-successor-change")
        result = self.service.decide(p["id"], "retry", "Reviewer")
        self.assertEqual(result["state"], "uncertain")
        self.assertIsNone(self.crm.get("cedar")["chow_current_account"])

    def test_changed_duplicate_survivor_blocks_inactivation(self):
        account = self.crm.get("river")
        self.crm.update("river", {"name": "Unrelated Facility", "city": "Columbus"}, account["version"], "external-survivor-change")
        result = self.approve("duplicate")
        self.assertEqual(result["state"], "stale")
        self.assertEqual(self.crm.get("river-copy")["status"], "Active")

    def test_missing_website_account_only_flagged_for_review(self):
        self.assertEqual(self.approve("missing")["state"], "applied")
        account = self.crm.get("lake")
        self.assertEqual(account["status"], "Needs Review")
        self.assertEqual(account["parent_id"], "bellhaven-parent")

    def test_duplicate_is_linked_and_inactivated_without_deletion(self):
        self.assertEqual(self.approve("duplicate")["state"], "applied")
        account = self.crm.get("river-copy")
        self.assertEqual(account["duplicate_of_account"], "river")
        self.assertEqual(account["status"], "Inactive")
        self.assertEqual(len(self.crm.list_accounts()), len(demo_accounts()))

    def test_shared_campus_is_ambiguous(self):
        for p in self.store.proposals():
            if "Meadow" in p["title"]:
                self.assertEqual(p["kind"], "investigate")
                self.assertEqual(p["plan"]["changes"], {})

    def test_failed_crawl_preserves_existing_queue(self):
        previous = self.store.proposals()
        with patch("bellhaven.service.scrape_demo", side_effect=ScrapeError("Page failed")):
            with self.assertRaises(ScrapeError):
                self.service.scan()
        self.assertEqual(self.store.proposals(), previous)
        self.assertEqual(self.service.state()["last_run"]["status"], "failed")

    def test_real_website_detail_layout_parser(self):
        html = '<h1>Bellhaven Meadows of Findlay</h1><dl class="detail"><dt>Address</dt><dd>1800 N Blanchard St<br>Findlay, OH 45840</dd><dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span><span class="badge">Memory Support</span></dd></dl>'
        facilities, _ = parse_page(html, "https://example.com/communities/findlay")
        self.assertEqual(facilities[0]["city"], "Findlay")
        self.assertEqual(facilities[0]["care_offerings"], ["Assisted Living", "Memory Support"])

    def test_database_namespace_prevents_demo_live_mixing(self):
        self.store.bind_namespace("demo")
        with self.assertRaises(ValueError):
            self.store.bind_namespace("live")


if __name__ == "__main__":
    unittest.main()
