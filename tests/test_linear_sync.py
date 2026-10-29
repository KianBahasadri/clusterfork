"""Linear lifecycle behavior across worker, CI, merge, and retry boundaries."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation/azure"))
from dispatch_clients import GitHub, Linear, RequestError
from dispatch_core import Registry, eligibility
from linear_sync import LinearSync, pull_outcome
from readiness_policy import PROTOCOL
from worker_core import Accounts, WorkerError, WorkerService
from test_remote_worker import KEY, NOW, PROJECT, ISSUE, MemoryTable, claims, receipt, repository_queue


def pr():
    return {"url": "https://github.com/KianBahasadri/clusterfork/pull/1", "state": "OPEN", "merged": False,
            "body": f"<!-- clusterfork-dispatch:{KEY} -->", "baseRefName": "main",
            "headRefName": "clusterfork/kian-891-aaaaaaaa",
            "headRepository": {"nameWithOwner": "KianBahasadri/clusterfork"},
            "author": {"login": "clusterfork", "__typename": "Bot"},
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False,
            "autoMergeRequest": {"enabledAt": "2026-10-28T20:00:00Z"},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {
                "nodes": [{"__typename": "CheckRun", "name": "test", "isRequired": True,
                           "status": "COMPLETED", "conclusion": "SUCCESS"}],
                "pageInfo": {"hasNextPage": False}}}}}]}}


def checks(pull):
    return pull["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.route = self.registry.routes[PROJECT]
        self.states = self.route.linear_states
        self.issue = {"id": ISSUE, "identifier": "KIAN-891", "state": {"id": self.route.ready_state_ids[0]},
                      "project": {"id": PROJECT, "archivedAt": None, "labels": {"nodes": [{"name": "clusterfork-symphony"}]}},
                      "title": "Test", "description": "", "url": "https://linear.app/test"}
        self.linear = Mock(spec=Linear)
        self.linear.issue.side_effect = lambda _: copy.deepcopy(self.issue)
        self.linear.set_state.side_effect = lambda _, state: self.issue.update(state={"id": state})
        self.github = Mock(spec=GitHub)
        self.github.installation_token.return_value = "temporary-token"
        self.github.worker_result.return_value = {"status": "completed"}
        self.pull = pr()
        self.github.worker_pull.side_effect = lambda *args: copy.deepcopy(self.pull)
        self.receipts, self.runs, self.notes = MemoryTable(), MemoryTable(), MemoryTable()
        self.receipts.create(receipt())
        self.run = {"RowKey": KEY, "owner": "123:1", "status": "completed", "lease_until": 0,
                    "pull_request": self.pull["url"]}
        self.runs.create(self.run)
        self.sync = LinearSync(self.registry, self.receipts, self.runs, self.notes,
                               self.linear, self.github, clock=lambda: NOW)
        self.queue = repository_queue(self.registry, self.receipts, self.runs, self.linear, self.github)

    def update_run(self, **fields):
        row, etag = self.runs.get(KEY)
        self.runs.replace({**row, **fields}, etag)

    def sync_state(self):
        self.sync.sync(KEY)
        return self.issue["state"]["id"]

    def test_pending_ci_stays_in_progress_and_only_merge_marks_done(self):
        checks(self.pull)[0].update(status="IN_PROGRESS", conclusion=None)
        self.assertEqual(self.sync_state(), self.states.in_progress)
        self.assertIn("Waiting for required CI", self.linear.status_comment.call_args.args[2])
        self.pull.update(state="MERGED", merged=True)
        self.assertEqual(self.sync_state(), self.states.done)
        self.assertIn("Pull request merged", self.linear.status_comment.call_args.args[2])
        self.github.reset_mock()
        self.sync.sync(KEY)
        self.github.worker_pull.assert_not_called()

    def test_conflict_and_failed_required_ci_block_then_recover(self):
        for failure in ("conflict", "ci"):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == "conflict":
                    self.pull["mergeable"] = "CONFLICTING"
                else:
                    checks(self.pull)[0]["conclusion"] = "FAILURE"
                self.assertEqual(self.sync_state(), self.states.blocked)
                body = self.linear.status_comment.call_args.args[2]
                self.assertIn("conflicts" if failure == "conflict" else "test", body)
                self.assertIn("[Pull request]", body)
                self.assertIn("/actions/runs/123/attempts/1", body)
                first_id = self.linear.status_comment.call_args.args[1]
                self.pull = pr()
                checks(self.pull)[0].update(status="IN_PROGRESS", conclusion=None)
                self.assertEqual(self.sync_state(), self.states.in_progress)
                self.assertEqual(self.linear.status_comment.call_args.args[1], first_id)

    def test_normal_waits_and_other_merge_blockers(self):
        cases = [({"state": "CLOSED"}, "blocked"), ({"isDraft": True}, "blocked"),
                 ({"autoMergeRequest": None}, "blocked"), ({"mergeStateStatus": "BEHIND"}, "blocked"),
                 ({"mergeStateStatus": "BLOCKED"}, "blocked"), ({"mergeable": "UNKNOWN"}, "in_progress"),
                 ({"commits": {"nodes": []}}, "in_progress")]
        for change, expected in cases:
            with self.subTest(change=change):
                self.assertEqual(pull_outcome({**pr(), **change}, True)[0], expected)
        self.assertEqual(pull_outcome({**pr(), "autoMergeRequest": None}, False)[0], "in_progress")

    def test_unrequired_failures_do_not_block_and_running_reruns_are_pending(self):
        checks(self.pull).append({"__typename": "CheckRun", "name": "extra", "isRequired": False,
                                  "status": "COMPLETED", "conclusion": "FAILURE"})
        self.assertEqual(self.sync_state(), self.states.in_progress)
        checks(self.pull)[0]["conclusion"] = "FAILURE"
        checks(self.pull).append({"__typename": "CheckRun", "name": "test", "isRequired": True,
                                  "status": "IN_PROGRESS", "conclusion": None})
        self.assertEqual(self.sync_state(), self.states.in_progress)

    def test_canceled_ci_and_legacy_commit_status_are_detected(self):
        checks(self.pull)[0]["conclusion"] = "CANCELLED"
        self.assertEqual(self.sync_state(), self.states.blocked)
        checks(self.pull)[:] = [{"__typename": "StatusContext", "context": "legacy", "isRequired": True,
                                "state": "PENDING"}]
        self.assertEqual(self.sync_state(), self.states.in_progress)
        checks(self.pull)[0]["state"] = "ERROR"
        self.assertEqual(self.sync_state(), self.states.blocked)

    def test_partial_api_results_do_not_change_status(self):
        self.pull["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["pageInfo"]["hasNextPage"] = True
        with self.assertRaises(ValueError):
            self.sync.sync(KEY)
        self.linear.set_state.assert_not_called()
        self.assertEqual(self.notes.get(KEY)[0]["lease_until"], 0)

    def test_worker_failure_discovers_a_pr_created_before_the_failure(self):
        self.update_run(status="failed", pull_request="")
        self.pull["mergeable"] = "CONFLICTING"
        self.assertEqual(self.sync_state(), self.states.blocked)
        self.github.worker_pull.assert_called_once_with(self.route, receipt(), KEY, "")
        self.pull.update(state="MERGED", merged=True)
        self.assertEqual(self.sync_state(), self.states.done)

    def test_failed_and_canceled_workers_without_prs_are_blocked(self):
        self.github.worker_pull.side_effect = lambda *args: None
        self.update_run(status="failed", pull_request="")
        self.assertEqual(self.sync_state(), self.states.blocked)
        self.update_run(status="running", pull_request="", lease_until=NOW-1)
        self.github.worker_result.return_value = {"status": "completed", "conclusion": "cancelled"}
        self.assertEqual(self.sync_state(), self.states.blocked)
        self.github.worker_result.return_value = {"status": "in_progress"}
        self.assertEqual(self.sync_state(), self.states.in_progress)

    def test_no_change_completion_is_done_and_quota_wait_is_ready(self):
        self.update_run(pull_request="")
        self.assertEqual(self.sync_state(), self.states.done)
        self.setUp()
        self.update_run(status="waiting_for_quota", pull_request="")
        self.issue["state"]["id"] = self.states.in_progress
        self.assertEqual(self.sync_state(), self.route.ready_state_ids[0])
        self.github.worker_pull.assert_not_called()

    def test_repeated_polls_do_not_repeat_comments(self):
        self.sync.sync(KEY)
        self.sync.sync(KEY)
        self.assertEqual(self.linear.status_comment.call_count, 1)
        self.assertEqual(self.linear.set_state.call_count, 1)
        self.assertEqual(UUID(self.linear.status_comment.call_args.args[1]).version, 4)

    def test_failed_comment_does_not_advance_state_and_retries(self):
        self.linear.status_comment.side_effect = [RequestError(), None]
        with self.assertRaises(RequestError):
            self.sync.sync(KEY)
        self.linear.set_state.assert_not_called()
        self.assertEqual(self.sync_state(), self.states.in_progress)

    def test_project_changes_and_manual_terminal_states_are_respected(self):
        changes = [lambda i: i.update(archivedAt="now"),
                   lambda i: i["project"].update(id="another-project"),
                   lambda i: i["project"]["labels"].update(nodes=[]),
                   lambda i: i["project"].update(archivedAt="now"),
                   lambda i: i.update(state={"id": self.states.done}),
                   lambda i: i.update(state={"id": "canceled", "type": "canceled"})]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                change(self.issue)
                self.sync.sync(KEY)
                self.linear.set_state.assert_not_called()
                self.linear.status_comment.assert_not_called()

    def test_newer_worker_attempt_supersedes_an_old_observation(self):
        def fetch(*args):
            self.update_run(owner="123:2", status="running")
            return pr()
        self.github.worker_pull.side_effect = fetch
        self.sync.sync(KEY)
        self.linear.set_state.assert_not_called()

    def test_concurrent_sync_lease_prevents_duplicate_updates(self):
        self.notes.create({"RowKey": KEY, "lease_until": NOW+120})
        self.sync.sync(KEY)
        self.linear.set_state.assert_not_called()

    def test_started_worker_can_publish_after_linear_moves_to_in_progress(self):
        self.runs = MemoryTable()
        self.sync.runs = self.runs
        self.queue.runs = self.runs
        service = WorkerService(self.registry, self.receipts, self.runs, Accounts(MemoryTable()),
                                self.linear, self.github, self.queue, clock=lambda: NOW, sync=self.sync)
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertEqual(self.issue["state"]["id"], self.states.in_progress)
        self.assertEqual(eligibility(self.issue, self.registry)[1], "not_ready")
        self.assertEqual(service.handle("github", claims(), {"dispatch_key": KEY})["token"], "temporary-token")
        service.handle("finish", claims(), {"dispatch_key": KEY, "status": "waiting_for_quota"})
        self.assertEqual(self.issue["state"]["id"], self.route.ready_state_ids[0])
        self.assertEqual(self.receipts.get(KEY)[0]["retry_after"], NOW+900)

    def test_manual_rerun_can_resume_blocked_issue_but_publication_honors_cancellation(self):
        self.update_run(status="failed", pull_request="")
        self.issue["state"]["id"] = self.states.blocked
        service = WorkerService(self.registry, self.receipts, self.runs, Accounts(MemoryTable()),
                                self.linear, self.github, self.queue, clock=lambda: NOW, sync=self.sync)
        service.handle("start", {**claims(), "run_attempt": "2"}, {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertEqual(self.issue["state"]["id"], self.states.in_progress)
        self.issue["state"]["id"] = "canceled"
        with self.assertRaises(WorkerError):
            service.handle("github", {**claims(), "run_attempt": "2"}, {"dispatch_key": KEY})

    def test_tracker_failure_does_not_lose_the_worker_result(self):
        self.update_run(status="running", lease_until=NOW+120)
        service = WorkerService(self.registry, self.receipts, self.runs, Accounts(MemoryTable()),
                                self.linear, self.github, self.queue, clock=lambda: NOW, sync=self.sync)
        self.queue.start_worker(self.route.repository, KEY, "123:1")
        self.linear.issue.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            service.handle("finish", claims(), {"dispatch_key": KEY, "status": "failed"})
        self.assertEqual(self.runs.get(KEY)[0]["status"], "failed")


class LifecycleAdapterTests(unittest.TestCase):
    def test_comment_creation_and_updates_use_the_same_id_and_skip_identical_text(self):
        request = Mock(side_effect=[{"data": {"issue": {"comments": {"nodes": []}}}},
                                   {"data": {"commentCreate": {"success": True}}},
                                   {"data": {"issue": {"comments": {"nodes": [{"id": ISSUE, "body": "old"}]}}}},
                                   {"data": {"commentUpdate": {"success": True}}},
                                   {"data": {"issue": {"comments": {"nodes": [{"id": ISSUE, "body": "new"}]}}}}])
        client = Linear("private-test-token", request)
        for body in ("old", "new", "new"):
            client.status_comment(ISSUE, ISSUE, body)
        self.assertEqual(request.call_count, 5)
        self.assertEqual(request.call_args_list[1].kwargs["data"]["variables"]["id"], ISSUE)
        self.assertEqual(request.call_args_list[3].kwargs["data"]["variables"]["id"], ISSUE)

    def test_pr_lookup_requires_the_exact_dispatch_branch_author_and_marker(self):
        route = Registry.load(ROOT / "automation/azure/projects.json").routes[PROJECT]
        for change in ({}, {"author": {"login": "someone", "__typename": "User"}},
                       {"body": "unrelated"}, {"headRefName": "another-branch"},
                       {"headRepository": {"nameWithOwner": "attacker/fork"}}):
            with self.subTest(change=change):
                client = GitHub("1", "unused", lambda url, **kw: [] if "/pulls?" in url else
                                {"data": {"repository": {"pullRequest": {**pr(), **change}}}})
                with patch.object(client, "installation_token", return_value="test-token") as token:
                    if change:
                        with self.assertRaises(RequestError):
                            client.worker_pull(route, receipt(), KEY, pr()["url"])
                    else:
                        self.assertEqual(client.worker_pull(route, receipt(), KEY, pr()["url"])["url"], pr()["url"])
                token.assert_called_once_with(route.repository, permissions={"contents": "read", "pull_requests": "read"})

    def test_pr_lookup_checks_all_pages_and_prefers_open_pr_over_stored_merged_url(self):
        route = Registry.load(ROOT / "automation/azure/projects.json").routes[PROJECT]
        pull = {**pr(), "url": "https://github.com/KianBahasadri/clusterfork/pull/999"}
        request = Mock(side_effect=[
            [{"number": n, "state": "closed", "merged_at": "earlier"} for n in range(1, 101)],
            [{"number": 999, "state": "open", "merged_at": None}],
            {"data": {"repository": {"pullRequest": pull}}}])
        client = GitHub("1", "unused", request)
        with patch.object(client, "installation_token", return_value="test-token"):
            self.assertEqual(client.worker_pull(route, receipt(), KEY, pr()["url"]), pull)
        self.assertEqual(request.call_args.kwargs["data"]["variables"]["number"], 999)

    def test_lifecycle_configuration_is_optional_but_complete_and_distinct(self):
        data = json.loads((ROOT / "automation/azure/projects.json").read_text())
        original = data["projects"][0].pop("linear_states")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(data))
            self.assertIsNone(Registry.load(path).routes[PROJECT].linear_states)
            for invalid in ({"blocked": original["blocked"]}, {**original, "done": original["blocked"]},
                            {**original, "done": data["projects"][0]["ready_state_ids"][0]},
                            {**original, "blocked": "not-a-uuid"}):
                data["projects"][0]["linear_states"] = invalid
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    Registry.load(path)


if __name__ == "__main__":
    unittest.main()
