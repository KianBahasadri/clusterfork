"""Once-only, App-dispatched Cursor validation with synthetic broker and ACP fixtures."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import cursor_validation as validation
import cursor_runtime as cursor
import cursor_smoke
import worker
from deployment_control import DeploymentControl
from dispatch_clients import GitHub
from dispatch_core import DispatchError, Registry
from worker_core import Accounts, ProviderAccounts, WorkerError, account_identity
from worker_reporting import usage_report
from test_cursor_accounts import FakeServer, auth, usage, NOW
import test_remote_worker as fixtures

REVISION = "b" * 40
METADATA = {"revision": REVISION, "digest": "d" * 64}


class CursorValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.table, self.account_table = fixtures.MemoryTable(), fixtures.MemoryTable()
        self.accounts = Accounts(self.account_table, lambda: NOW, provider="cursor")
        self.identity = self.accounts.enroll(auth())
        self.reference = "cursor:" + self.identity
        row, version = self.account_table.get(self.identity)
        row.update(state="staged", profile_name="21kb60", credential_format="cursor-cli-auth-v1",
                   staged_for_issue="KIAN-922", staged_for_issue_id=validation.ISSUE)
        self.account_table.replace(row, version)
        self.linear, self.github = Mock(), Mock()
        self.github.branch_sha.return_value = REVISION
        self.github.dispatch_runs.return_value = []
        self.control = DeploymentControl(fixtures.MemoryTable())
        self.control.pause("deploy", REVISION)
        self.control.resume("deploy", REVISION)
        self.service = validation.CursorValidation(self.registry, self.table, self.accounts,
            self.linear, self.github, self.control, clock=lambda: NOW)
        for name, value in (("IDENTITY", self.identity), ("REFERENCE", self.reference)):
            patcher = patch.object(validation, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(validation, "build_metadata", return_value=METADATA)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.claims = {**fixtures.claims(), "workflow_ref":
                      f"{validation.REPOSITORY}/.github/workflows/{validation.WORKFLOW}@refs/heads/main"}
        self.claims.update(job_workflow_ref=self.claims["workflow_ref"], job_workflow_sha=REVISION)
        with patch.object(cursor.time, "time", return_value=NOW):
            self.usage = usage_report(cursor.quota_snapshot(self.reference, cursor.MODEL, usage()))

    def ensure(self):
        self.service.ensure(self.control.state()[0])

    def call(self, action, **data):
        return self.service.handle("validation_" + action, self.claims, {"dispatch_key": validation.KEY, **data})

    def start(self):
        self.ensure()
        return self.call("start")

    def test_only_matching_resumed_deployment_dispatches_once_and_preserves_staged_credentials(self):
        self.control.pause("deploy", REVISION)
        self.ensure()
        self.github.dispatch.assert_not_called()
        self.control.resume("deploy", REVISION)
        self.github.branch_sha.return_value = "c" * 40
        self.ensure()
        self.github.dispatch.assert_not_called()
        self.github.branch_sha.return_value = REVISION
        self.ensure()
        self.ensure()
        self.github.dispatch.assert_called_once()
        route, inputs = self.github.dispatch.call_args.args
        self.assertEqual((route.workflow, route.ref, inputs), (validation.WORKFLOW, "main",
                         {"dispatch_key": validation.KEY, "revision": REVISION}))
        self.assertEqual(json.loads(self.account_table.get(self.identity)[0]["auth"]), auth())
        self.assertEqual(ProviderAccounts({"cursor": self.accounts}).list(), [])

    def test_coding_selector_pr_foreign_and_wrong_revision_identities_cannot_get_the_reserved_login(self):
        self.ensure()
        for claims in (fixtures.claims(), fixtures.selector_claims(), {**self.claims, "event_name": "pull_request"},
                       {**self.claims, "sha": "c" * 40}, {**self.claims, "ref": "refs/heads/feature"},
                       {**self.claims, "job_workflow_ref": fixtures.selector_claims()["job_workflow_ref"]},
                       {**self.claims, "runner_environment": "self-hosted"}):
            with self.subTest(claims=claims), self.assertRaises(WorkerError):
                self.service.handle("validation_start", claims, {"dispatch_key": validation.KEY})
        self.github.verify_worker_run.assert_not_called()
        self.assertFalse(self.account_table.get(self.identity)[0]["owner"])

    def test_validation_role_is_bound_to_one_identity_run_attempt_and_one_bounded_turn(self):
        config = self.start()
        self.assertEqual(config["account_id"], self.reference)
        self.assertNotIn("auth", config)
        self.github.verify_worker_run.assert_called_once_with(validation.REPOSITORY, self.claims,
                                                             validation.KEY, workflow=validation.WORKFLOW)
        for action in ("accounts", "claim", "bind_account", "checkout", "github", "progress", "review"):
            with self.subTest(action=action), self.assertRaises(WorkerError):
                self.service.handle(action, self.claims, {"dispatch_key": validation.KEY})
        with self.assertRaises(WorkerError):
            self.call("claim", account_id="a" * 64)
        leased = self.call("claim", account_id=self.reference)
        with self.assertRaises(WorkerError):
            self.call("claim", account_id=self.reference)
        with self.assertRaises(WorkerError):
            self.call("begin", usage_before={**self.usage, "observed_at": NOW - 61})
        self.assertEqual(self.call("begin", usage_before=self.usage), {"authorized": True, "timeout_seconds": 120})
        with self.assertRaises(WorkerError):
            self.call("begin", usage_before=self.usage)
        with self.assertRaises(WorkerError):
            self.service.handle("validation_start", {**self.claims, "run_attempt": "2"}, {"dispatch_key": validation.KEY})
        self.call("save", account_id=self.reference, lease=leased["lease"], auth=auth(refresh="latest-cloud-token"), release=True)
        self.call("finish", status="passed", reason="passed", file_verified=True, usage_after=self.usage)
        self.assertEqual(self.account_table.get(self.identity)[0]["state"], "active")
        self.assertEqual(json.loads(self.account_table.get(self.identity)[0]["auth"])["refreshToken"], "latest-cloud-token")
        self.assertEqual(ProviderAccounts({"cursor": self.accounts}).list(), [self.reference])
        with self.assertRaises(WorkerError):
            self.call("start")
        self.ensure()
        self.github.dispatch.assert_called_once()
        body = self.linear.status_comment.call_args.args[-1]
        for value in ("**passed**", "grok-4.7-high", "high", "Validation run", "cursor/api"):
            self.assertIn(value, body)
        self.assertNotIn("latest-cloud-token", body)

    def test_unknown_usage_failed_auth_and_missing_account_are_visible_failures(self):
        self.account_table.rows.clear()
        self.ensure()
        self.github.dispatch.assert_not_called()
        row = self.table.get(validation.KEY)[0]
        self.assertEqual((row["status"], row["reason"]), ("failed", "account_unavailable"))
        self.assertIn("not a passing integration result", self.linear.status_comment.call_args.args[-1])

    def test_lost_dispatch_response_is_never_retried_and_stopped_run_gets_a_failure_and_link(self):
        self.github.dispatch.side_effect = DispatchError("synthetic response loss", uncertain=True)
        self.ensure()
        self.ensure()
        self.github.dispatch.assert_called_once()
        self.assertEqual(self.table.get(validation.KEY)[0]["status"], "uncertain")
        self.github.dispatch_runs.return_value = [{"id": 123, "run_attempt": 1, "status": "completed"}]
        self.service.reconcile()
        row = self.table.get(validation.KEY)[0]
        self.assertEqual((row["status"], row["reason"], row["owner"]), ("failed", "worker_stopped", "123:1"))
        self.assertIn("actions/runs/123/attempts/1", self.linear.status_comment.call_args.args[-1])

    def test_a_passing_result_requires_saved_release_and_file_check(self):
        self.start()
        self.call("claim", account_id=self.reference)
        self.call("begin", usage_before=self.usage)
        with self.assertRaises(WorkerError):
            self.call("finish", status="passed", reason="passed", file_verified=True)
        self.call("finish", status="failed", reason="cursor_profile_unavailable")
        self.assertEqual(self.account_table.get(self.identity)[0]["state"], "staged")

    def test_lost_claim_response_cannot_authorize_another_claim_or_attempt(self):
        self.start()
        original = self.accounts.claim
        def lose_response(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic lost response")
        with patch.object(self.accounts, "claim", side_effect=lose_response), self.assertRaises(OSError):
            self.call("claim", account_id=self.reference)
        self.call("start")
        with self.assertRaises(WorkerError):
            self.call("claim", account_id=self.reference)
        self.assertTrue(self.table.get(validation.KEY)[0]["claimed"])

    def test_reporting_outage_keeps_pending_result_without_repeating_dispatch(self):
        self.linear.status_comment.side_effect = RuntimeError("synthetic reporting failure")
        self.ensure()
        self.assertTrue(self.table.get(validation.KEY)[0]["report_pending"])
        self.linear.status_comment.side_effect = None
        self.service.reconcile()
        self.assertFalse(self.table.get(validation.KEY)[0]["report_pending"])
        self.github.dispatch.assert_called_once()

    def test_late_running_comment_is_repaired_after_a_concurrent_final_result(self):
        self.start()
        bodies = []
        def write(issue, identifier, body):
            if "**running**" in body and not bodies:
                bodies.append("entered")
                self.service.update(status="failed", reason="file_check_failed")
                self.service.report()
            bodies.append(body)
        self.service.update(status="running")
        self.linear.status_comment.side_effect = write
        self.service.report()
        self.assertIn("**running**", bodies[-1])
        self.assertTrue(self.table.get(validation.KEY)[0]["report_pending"])
        self.service.reconcile()
        self.assertIn("**failed**", bodies[-1])
        self.assertFalse(self.table.get(validation.KEY)[0]["report_pending"])
        self.assertEqual(len({call.args[1] for call in self.linear.status_comment.call_args_list}), 1)

    def test_expired_validation_cannot_start_the_turn_but_can_release_its_lease(self):
        self.start()
        lease = self.call("claim", account_id=self.reference)
        self.service.clock = lambda: NOW + 361
        with self.assertRaises(WorkerError):
            self.call("begin", usage_before=self.usage)
        self.call("save", account_id=self.reference, lease=lease["lease"], release=True)
        self.assertFalse(self.account_table.get(self.identity)[0]["owner"])

    def test_github_api_verifies_app_dispatch_for_the_dedicated_workflow(self):
        run = {"event": "workflow_dispatch", "head_sha": REVISION, "run_attempt": 1,
               "repository": {"id": 12}, "head_repository": {"full_name": validation.REPOSITORY},
               "path": ".github/workflows/" + validation.WORKFLOW,
               "display_title": "KIAN-922 · " + validation.KEY, "actor": {"login": "clusterfork[bot]"}}
        github = GitHub("unused", "unused", request=Mock(return_value=run))
        with patch.object(github, "installation_token", return_value="synthetic-github-token"):
            github.verify_worker_run(validation.REPOSITORY, self.claims, validation.KEY, workflow=validation.WORKFLOW)
            for field, value in (("path", ".github/workflows/test.yml"), ("actor", {"login": "human"}),
                                 ("head_sha", "c" * 40), ("run_attempt", 2)):
                github.request.return_value = {**run, field: value}
                with self.subTest(field=field), self.assertRaises(WorkerError):
                    github.verify_worker_run(validation.REPOSITORY, self.claims, validation.KEY, workflow=validation.WORKFLOW)


class CursorSmokeTests(unittest.TestCase):
    def test_smoke_uses_cloud_adapter_checks_real_tool_read_and_releases_latest_auth(self):
        broker = SimpleNamespace(publication_guard=worker.PublicationGuard({}))
        calls, servers = [], []
        def call(action, **data):
            calls.append((action, data))
            if action == "claim":
                return {"auth": auth(), "lease": "synthetic-fence"}
            if action == "begin":
                return {"authorized": True, "timeout_seconds": 120}
            return {}
        broker.call = call
        def factory(home, **kwargs):
            server = FakeServer(home, **kwargs)
            servers.append(server)
            def turn(inner, value):
                text = value["params"]["prompt"][0]["text"]
                nonce = re.search(r"containing exactly ([0-9a-f-]{36})", text)[1]
                target = kwargs["cwd"] / "cloud-smoke.txt"
                target.write_text(nonce + "\n")
                worker.write_private(cursor.auth_path(home), auth(refresh="refreshed-by-cli"))
                for update in ({"sessionUpdate": "tool_call", "toolCallId": "read", "kind": "read",
                                "locations": [{"path": str(target)}]},
                               {"sessionUpdate": "tool_call_update", "toolCallId": "read", "status": "completed",
                                "rawOutput": {"content": nonce + "\n"}}):
                    inner.messages.put({"method": "session/update", "params": {"sessionId": "cursor-thread", "update": update}})
                inner.complete(value["id"], nonce)
            server.on_prompt = turn
            return server
        original = cursor.run_agent
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cursor, "inspect_account", return_value=({}, usage())), \
             patch.object(cursor, "read_usage", return_value=usage(11)), \
             patch.object(cursor.time, "time", return_value=NOW), \
             patch.object(cursor, "run_agent", side_effect=lambda *a, **k: original(*a, **k, server_factory=factory)) as run:
            report = cursor_smoke.probe(broker, {"model": cursor.MODEL, "effort": "high", "selection": {
                "min_remaining_percent": 20, "max_snapshot_age_seconds": 60}},
                SimpleNamespace(failed=__import__("threading").Event()), Path(directory))
        self.assertEqual(report["windows"][0]["used_percent"], 11)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertFalse(run.call_args.kwargs["steering"])
        self.assertTrue(servers[0].closed)
        self.assertEqual([action for action, _ in calls], ["claim", "begin", "save"])
        self.assertEqual(calls[-1][1]["auth"]["refreshToken"], "refreshed-by-cli")
        self.assertTrue(calls[-1][1]["release"])

    def test_failed_smoke_is_not_skipped_or_retried(self):
        broker = Mock()
        broker.call.side_effect = [RuntimeError("synthetic auth failure"), {}]
        with patch.object(cursor_smoke, "ValidationBroker", return_value=broker), \
             patch.dict(os.environ, {"CLUSTERFORK_BROKER_URL": "unused"}, clear=True), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cursor_smoke.main(), 1)
        self.assertIn("failed", output.getvalue())
        self.assertEqual([call.args[0] for call in broker.call.call_args_list], ["start", "finish"])


if __name__ == "__main__":
    unittest.main()
