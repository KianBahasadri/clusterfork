"""Pool credentials stay on preflight's VM; agents keep one immutable account."""

from contextlib import contextmanager, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import worker
from dispatch_core import Registry
from readiness_policy import PROTOCOL
from worker_core import ACCOUNT_HANDOFF_SECONDS, WorkerError, authorize_claims
import test_remote_worker as fixtures


class AccountIsolationTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.service, self.github, self.issue = fixtures.WorkerAuthorizationTests.service(self)
        self.now = fixtures.NOW
        self.service.clock = self.service.accounts.clock = lambda: self.now
        self.first = self.service.accounts.enroll(fixtures.auth())
        self.second = self.service.accounts.enroll(fixtures.auth("account-b", "access-b"))
        self.selector = fixtures.selector_claims()

    def call(self, action, *, identity=None, **data):
        return self.service.handle(action, identity or fixtures.claims(),
                                   {"dispatch_key": fixtures.KEY, "protocol": PROTOCOL, **data})

    def preflight(self):
        return self.call("start", identity=self.selector)

    def bind(self, account=None):
        return self.call("bind_account", identity=self.selector, account_id=account or self.first)

    def denied(self, action, **data):
        with self.assertRaises(WorkerError) as caught:
            self.call(action, **data)
        self.assertEqual(caught.exception.status, 403)

    def test_only_the_exact_signed_selector_workflow_and_revision_can_inspect_the_pool(self):
        self.preflight()
        self.assertEqual(self.call("accounts", identity=self.selector)["accounts"], [self.first, self.second])
        for extra in ({}, {"selector": True}, {"account_selection": True}, {"preflight": True}):
            self.denied("accounts", **extra)
            self.denied("claim", account_id=self.first, **extra)
            self.denied("bind_account", account_id=self.first, **extra)
        for field, value in (("job_workflow_sha", "c" * 40),
                             ("job_workflow_sha", None),
                             ("job_workflow_ref", self.selector["job_workflow_ref"].replace("main", "other")),
                             ("workflow_ref", self.selector["job_workflow_ref"])):
            with self.subTest(field=field, value=value), self.assertRaises(WorkerError):
                authorize_claims({**self.selector, field: value}, self.registry, fixtures.receipt())
        for action in ("checkout", "github", "progress", "review", "steering_open"):
            self.denied(action, identity=self.selector)

    def test_binding_fences_both_roles_even_after_release_expiry_and_start_retry(self):
        self.preflight()
        lease = self.call("claim", identity=self.selector, account_id=self.first)
        self.call("save", identity=self.selector, account_id=self.first, lease=lease["lease"],
                  auth=fixtures.auth(token="refreshed-in-preflight"), release=True)
        self.bind()
        config = self.call("start")
        self.assertEqual(config["account_id"], self.first)
        self.assertIs(config["account_selection"], False)
        self.assertEqual(self.call("accounts"), {"accounts": [self.first]})
        for identity in (fixtures.claims(), self.selector):
            self.assertEqual(self.call("accounts", identity=identity), {"accounts": [self.first]})
            for action in ("claim", "save", "bind_account"):
                self.denied(action, identity=identity, account_id=self.second)
        leased = self.call("claim", account_id=self.first)
        self.assertEqual(leased["auth"]["tokens"]["access_token"], "refreshed-in-preflight")
        self.call("save", account_id=self.first, lease=leased["lease"], release=True)
        self.now += 121
        self.assertEqual(self.call("start")["account_id"], self.first)
        self.denied("claim", account_id=self.second)
        self.call("claim", account_id=self.first)
        self.call("finish", status="failed")
        with self.assertRaises(WorkerError):
            self.preflight()
        self.assertEqual(self.service.runs.get(fixtures.KEY)[0]["assigned_account_id"], self.first)

    def test_concurrent_binding_cannot_overwrite_the_winning_assignment(self):
        self.preflight()
        original = self.service.runs.replace

        def concurrent_bind(row, etag):
            if row.get("assigned_account_id") == self.first:
                current, version = self.service.runs.get(fixtures.KEY)
                original({**current, "assigned_account_id": self.second}, version)
            original(row, etag)

        with patch.object(self.service.runs, "replace", side_effect=concurrent_bind), \
             self.assertRaises(WorkerError):
            self.bind()
        self.assertEqual(self.call("start")["account_id"], self.second)
        self.denied("claim", account_id=self.first)

    def test_a_finish_racing_with_start_cannot_clear_the_binding(self):
        self.preflight()
        self.bind()

        def finish(repository):
            self.call("finish", status="failed")
            return False

        with patch.object(self.github, "workflow_changes_allowed", side_effect=finish), \
             self.assertRaises(WorkerError):
            self.call("start")
        row = self.service.runs.get(fixtures.KEY)[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["assigned_account_id"], self.first)

    def test_a_new_attempt_requires_fresh_preflight_and_cannot_inherit_pool_access(self):
        self.preflight()
        self.bind()
        self.call("start")
        self.call("finish", status="failed")
        newer = {**fixtures.claims(), "run_attempt": "2"}
        with patch.object(self.github, "worker_result", return_value={"status": "completed"}):
            config = self.call("start", identity=newer)
            self.assertIsNone(config["account_id"])
            self.denied("accounts", identity=newer)
            self.denied("claim", identity=newer, account_id=self.first)
            self.call("start", identity=fixtures.selector_claims(newer))
            self.call("bind_account", identity=fixtures.selector_claims(newer), account_id=self.second)
            self.assertEqual(self.call("start", identity=newer)["account_id"], self.second)
            self.denied("claim", identity=newer, account_id=self.first)

    def test_account_access_rechecks_owner_after_a_concurrent_attempt_replacement(self):
        self.preflight()
        self.bind()
        original = self.service.runs.get
        reads = 0

        def superseded(key):
            nonlocal reads
            row, version = original(key)
            reads += 1
            if reads == 2:
                row.update(owner="456:1", assigned_account_id=self.second)
            return row, version

        with patch.object(self.service.runs, "get", side_effect=superseded), \
             patch.object(self.service.accounts, "claim") as claim:
            self.denied("claim", account_id=self.second)
        claim.assert_not_called()

    def test_handoff_allows_a_fresh_vm_and_does_not_lease_all_accounts_while_queued(self):
        self.preflight()
        self.bind()
        self.assertEqual(self.service.runs.get(fixtures.KEY)[0]["lease_until"],
                         self.now + ACCOUNT_HANDOFF_SECONDS)
        self.call("heartbeat", identity=self.selector)
        self.now += 180
        self.assertEqual(self.call("start")["account_id"], self.first)
        self.assertTrue(all(not row["owner"] for row in self.service.accounts.table.list()))

    def run_preflight(self, used):
        broker = SimpleNamespace(call=lambda action, **data: self.call(action, identity=self.selector, **data),
                                 publication_guard=worker.PublicationGuard({}))
        homes = []

        def inspect(home, model, effort):
            homes.append(home)
            return {}, {"rateLimits": {"primary": {"usedPercent": used, "resetsAt": self.now + 1000}}}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"DISPATCH_KEY": fixtures.KEY, "GITHUB_OUTPUT": str(output),
                                         "CLUSTERFORK_BROKER_URL": "https://broker.example/worker"}), \
                 patch.object(worker, "Broker", return_value=broker), \
                 patch.object(worker, "inspect_account", side_effect=inspect), \
                 patch.object(worker.time, "time", return_value=self.now), \
                 patch.object(worker, "execute") as execute, \
                 patch.object(worker, "review_readiness") as review, \
                 patch.object(worker, "run_agent") as agent, redirect_stdout(io.StringIO()):
                self.assertEqual(worker.main(select_only=True), 0)
            execute.assert_not_called()
            review.assert_not_called()
            agent.assert_not_called()
            self.assertEqual(len(homes), 2)
            self.assertTrue(all(not home.exists() for home in homes))
            for credential in ("account-a", "access-a", "account-b", "access-b"):
                with self.assertRaises(worker.PublicationBlocked):
                    broker.publication_guard.check(credential, static=False)
            return output.read_text()

    def test_preflight_inspects_and_cleans_credentials_and_exports_only_a_boolean(self):
        self.assertEqual(self.run_preflight(10), "selected=true\n")
        config = self.call("start")
        self.assertIn(config["account_id"], (self.first, self.second))
        self.assertTrue(all(not row["owner"] for row in self.service.accounts.table.list()))
        self.assertNotIn("auth", config)

    def test_preflight_quota_wait_skips_execution_and_schedules_retry(self):
        self.assertEqual(self.run_preflight(95), "selected=false\n")
        row = self.service.runs.get(fixtures.KEY)[0]
        self.assertEqual(row["status"], "waiting_for_quota")
        self.assertNotIn("assigned_account_id", row)
        self.assertEqual(self.service.receipts.get(fixtures.KEY)[0]["retry_after"], self.now + 900)

    def test_agent_vm_only_inspects_the_assigned_account_and_never_enumerates_the_pool(self):
        @contextmanager
        def lease(broker, identity):
            self.assertEqual(identity, self.first)
            yield Path("/unused"), SimpleNamespace(failed=threading.Event())

        config = {"account_id": self.first, "model": "gpt-6.1-sol", "effort": "max",
                  "selection": {"min_remaining_percent": 20, "max_snapshot_age_seconds": 60}}
        broker = Mock()
        with patch.object(worker, "account_lease", side_effect=lease), \
             patch.object(worker, "inspect_account", return_value=({}, {})), \
             patch.object(worker, "quota_snapshot"), redirect_stdout(io.StringIO()):
            worker.choose_account(broker, config, SimpleNamespace(failed=threading.Event()))
        broker.call.assert_not_called()

    def test_busy_or_depleted_assignment_waits_without_trying_another_account(self):
        for busy in (True, False):
            with self.subTest(busy=busy):
                @contextmanager
                def lease(broker, identity):
                    self.assertEqual(identity, self.first)
                    if busy:
                        raise HTTPError("https://broker/claim", 409, "busy", {}, None)
                    yield Path("/unused"), SimpleNamespace(failed=threading.Event())

                config = {"account_id": self.first, "model": "gpt-6.1-sol", "effort": "max",
                          "selection": {"min_remaining_percent": 20, "max_snapshot_age_seconds": 60}}
                broker = Mock()
                heartbeat = SimpleNamespace(failed=threading.Event())
                with patch.object(worker, "account_lease", side_effect=lease) as claim, \
                     patch.object(worker, "inspect_account", return_value=({}, {
                         "rateLimits": {"primary": {"usedPercent": 95, "resetsAt": self.now + 1000}}})), \
                     patch.object(worker, "run_agent") as agent, redirect_stdout(io.StringIO()):
                    with worker.run_codex(broker, config, heartbeat, Path("/unused")) as result:
                        self.assertIsNone(result)
                claim.assert_called_once_with(broker, self.first)
                agent.assert_not_called()
                broker.call.assert_called_once_with("progress", stage="selecting_account")


if __name__ == "__main__":
    unittest.main()
