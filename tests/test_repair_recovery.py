"""Operator recovery preserves admission and refuses unverified repair content."""

import copy
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import recover_repair_binding as recovery
from auto_repair import repair_content, trigger_markdown
from linear_sync import comment_id
from readiness_policy import issue_revision
from worker_core import WorkerError
from test_remote_worker import MemoryTable


class RepairRecoveryTests(unittest.TestCase):
    def setUp(self):
        from test_repository_queue import RepositoryQueueTests
        fixture = self.fixture = RepositoryQueueTests()
        fixture.setUp()
        self.parent_key, _ = fixture.prepare_repair()
        # Stop before dispatch, then reconstruct a legacy child record. Current
        # admission uses the original issue and no longer creates this content.
        with patch.object(fixture.queue, "dispatch", side_effect=WorkerError("content binding")):
            with self.assertRaises(WorkerError):
                fixture.queue.advance(fixture.repository)
        self.table = fixture.queue.table(fixture.repository)
        parent, version = self.table.get(self.parent_key)
        self.child_key = parent["repair_key"]
        self.original = fixture.issues[parent["issue_id"]]
        context = json.loads(parent["repair_context"])
        self.repair = fixture.create_repair({"id": comment_id(self.parent_key, "auto-repair-issue"),
            "parentId": self.original["id"], "priority": 2, "stateId": fixture.route.ready_state_ids[0],
            **repair_content(context, fixture.route, self.original)})
        self.repair = fixture.issues[self.repair["id"]]
        parent.update(repair_issue_id=self.repair["id"], repair_identifier=self.repair["identifier"],
                      repair_issue_url=self.repair["url"])
        child, child_version = self.table.get(self.child_key)
        child.update(issue_id=self.repair["id"], identifier=self.repair["identifier"])
        self.table.replace(child, child_version)
        canonical = "Triggered by: " + trigger_markdown(fixture.repository, context["trigger"])
        legacy = {**self.repair, "description": self.repair["description"].replace(
            canonical, "Triggered by: " + context["trigger"], 1)}
        parent["repair_revision"] = issue_revision(legacy)
        self.table.replace(parent, version)
        receipt, version = fixture.receipts.get(self.parent_key)
        receipt["readiness_review_key"] = "review"
        fixture.receipts.replace(receipt, version)
        self.reviews = MemoryTable()
        self.reviews.create({"RowKey": "review", "status": "passed",
                             "binding": json.dumps({"issue_revision": issue_revision(self.original)})})
        self.health = {"deployment": {"digest": "tested-source"}, "missing": [],
                       "serial_admission": True, "deployment_paused": False}

    def plan(self, issue=None):
        fixture = self.fixture
        return recovery.plan_rebind(issue or self.original, fixture.route, self.table,
                                    fixture.receipts, fixture.runs, self.reviews, fixture.linear)

    def apply(self, plan, directory, enqueue=None, health=None):
        return recovery.apply_rebind(plan, self.table, self.health if health is None else health,
                                     enqueue or Mock(), directory, expected_digest="tested-source")

    def test_dry_run_accepts_parent_or_child_without_mutation(self):
        before = copy.deepcopy(self.table.rows)
        parent_plan = self.plan()
        child_plan = self.plan(self.repair)
        self.assertEqual(parent_plan.summary(), child_plan.summary())
        self.assertTrue(parent_plan.summary()["binding_change_needed"])
        self.assertEqual(self.table.rows, before)
        self.assertNotIn(self.child_key, self.fixture.receipts.rows)

    def test_apply_changes_only_binding_and_keeps_private_audit(self):
        plan = self.plan()
        before = {key: self.table.get(key)[0] for key in (self.parent_key, self.child_key, "slot")}
        receipts = copy.deepcopy(self.fixture.receipts.rows)
        enqueue = Mock()
        with tempfile.TemporaryDirectory() as directory:
            backup = self.apply(plan, Path(directory) / "private", enqueue)
            audit = json.loads(backup.read_text())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.parent.stat().st_mode), 0o700)
            self.assertTrue(audit["binding_applied"])
            self.assertTrue(audit["reconciliation_queued"])
            self.assertNotIn("repair_context", audit)
            self.assertNotIn("description", audit)
        after = self.table.get(self.parent_key)[0]
        self.assertEqual(after, {**before[self.parent_key], "repair_revision": plan.revision})
        for key in (self.child_key, "slot"):
            self.assertEqual(self.table.get(key)[0], before[key])
        self.assertEqual(self.fixture.receipts.rows, receipts)
        event = enqueue.call_args.args[0]
        self.assertEqual({k: event[k] for k in ("type", "id")}, {"type": "Project", "id": plan.parent["project_id"]})
        self.assertFalse(self.plan().summary()["binding_change_needed"])

    def test_content_and_original_review_changes_are_refused(self):
        for change in ("repair_title", "repair_description", "original_description", "unknown_binding"):
            with self.subTest(change=change):
                self.setUp()
                if change == "repair_title":
                    self.repair["title"] += " another task"
                elif change == "repair_description":
                    self.repair["description"] += "\nDeploy unreviewed work."
                elif change == "original_description":
                    self.original["description"] = "New requirements"
                else:
                    row, version = self.table.get(self.parent_key)
                    self.table.replace({**row, "repair_revision": "unrecognized"}, version)
                with self.assertRaises(WorkerError):
                    self.plan()

    def test_active_or_previously_dispatched_repairs_are_refused(self):
        for change in ("owner", "receipt", "worker", "other_issue", "canceled"):
            with self.subTest(change=change):
                self.setUp()
                target = self.original
                if change == "owner":
                    row, version = self.table.get("slot")
                    self.table.replace({**row, "owner": "123:1"}, version)
                elif change == "receipt":
                    self.fixture.receipts.create({"RowKey": self.child_key, "status": "dispatched"})
                elif change == "worker":
                    self.fixture.runs.create({"RowKey": self.child_key, "status": "failed"})
                elif change == "other_issue":
                    target = {**target, "id": "another-issue"}
                else:
                    self.repair["state"] = {"type": "canceled"}
                with self.assertRaises(WorkerError):
                    self.plan(target)

    def test_concurrent_state_change_fences_apply(self):
        plan = self.plan()
        row, version = self.table.get(self.child_key)
        self.table.replace(row, version)
        enqueue = Mock()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(WorkerError):
            self.apply(plan, directory, enqueue)
        self.assertEqual(self.table.get(self.parent_key)[0]["repair_revision"], plan.parent["repair_revision"])
        enqueue.assert_not_called()

    def test_paused_incompatible_or_unhealthy_broker_prevents_mutation(self):
        for health in ({**self.health, "deployment_paused": True},
                       {**self.health, "deployment": {"digest": "old-source"}},
                       {**self.health, "missing": ["configuration"]},
                       {**self.health, "serial_admission": False}):
            with self.subTest(health=health), tempfile.TemporaryDirectory() as directory:
                before = copy.deepcopy(self.table.rows)
                with self.assertRaises(WorkerError):
                    self.apply(self.plan(), directory, health=health)
                self.assertEqual(self.table.rows, before)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_lost_enqueue_can_retry_verified_binding_without_resetting_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                self.apply(self.plan(), directory, Mock(side_effect=RuntimeError("queue unavailable")))
            audit = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertTrue(audit["binding_applied"])
            self.assertFalse(audit["reconciliation_queued"])
            retry = self.plan()
            self.assertFalse(retry.summary()["binding_change_needed"])
            enqueue = Mock()
            self.apply(retry, directory, enqueue)
            enqueue.assert_called_once()
            self.assertNotIn(self.child_key, self.fixture.receipts.rows)


if __name__ == "__main__":
    unittest.main()
