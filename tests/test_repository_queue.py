"""Repository FIFO safety across deliveries, worker crashes, PRs, and reruns."""

import copy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation/azure"))
from dispatch_clients import GitHub, Linear, RequestError
from dispatch_core import DispatchError, Dispatcher, Registry
from repository_queue import RepositoryQueue
from worker_core import Accounts, WorkerError, WorkerService
from test_remote_worker import MemoryTable, NOW, PROJECT, claims
from test_linear_sync import pr, checks


class RepositoryQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.route = self.registry.routes[PROJECT]
        self.repository = self.route.repository
        self.issues, self.remote_runs, self.attempts, self.pulls = {}, {}, {}, {}
        self.receipts, self.runs = MemoryTable(), MemoryTable()
        self.tables = defaultdict(MemoryTable)
        self.linear = Mock(spec=Linear)
        self.linear.issue.side_effect = lambda key: copy.deepcopy(self.issues.get(key))
        self.linear.project_issues.side_effect = lambda project: [copy.deepcopy(i) for i in self.issues.values()
                                                                if i["project"]["id"] == project]
        self.github = Mock(spec=GitHub)
        self.github.dispatch.side_effect = self.dispatch
        self.github.dispatch_runs.side_effect = lambda route, key: copy.deepcopy(self.remote_runs.get(key, []))
        self.github.worker_pull.side_effect = lambda route, receipt, key, url: copy.deepcopy(self.pulls.get(key))
        self.github.worker_result.side_effect = lambda repo, owner: copy.deepcopy(self.attempts[owner])
        self.queue = self.restart()
        self.service = WorkerService(self.registry, self.receipts, self.runs, Accounts(MemoryTable()),
                                     self.linear, self.github, self.queue, clock=lambda: self.now)
        self.dispatcher = Dispatcher(self.registry, self.linear, self.queue)

    def restart(self):
        return RepositoryQueue(self.registry, self.receipts, self.runs, self.tables.__getitem__,
                               self.linear, self.github, clock=lambda: self.now)

    def issue(self, number, project=PROJECT):
        issue = {"id": str(UUID(int=number)), "identifier": f"KIAN-{number}", "title": "Test",
                 "state": {"id": self.registry.routes[project].ready_state_ids[0], "type": "unstarted"},
                 "createdAt": f"2026-10-01T00:00:{number:02d}Z", "archivedAt": None,
                 "project": {"id": project, "archivedAt": None,
                             "labels": {"nodes": [{"name": self.registry.enrollment_label}]}}}
        self.issues[issue["id"]] = issue
        return issue

    def enqueue(self, issue):
        return self.queue.enqueue(issue, self.registry.routes[issue["project"]["id"]])

    def dispatch(self, route, inputs):
        key = inputs["dispatch_key"]
        run = {"id": 100 + sum(len(r) for r in self.remote_runs.values()), "run_attempt": 1,
               "status": "queued", "conclusion": None}
        self.remote_runs.setdefault(key, []).append(run)
        self.attempts[f"{run['id']}:1"] = run

    def identity(self, key):
        receipt, _ = self.receipts.get(key)
        run = self.remote_runs[key][-1]
        repository = receipt["repository"]
        return {**claims(), "run_id": str(run["id"]), "run_attempt": str(run["run_attempt"]),
                "repository": repository,
                "workflow_ref": f"{repository}/.github/workflows/clusterfork-worker.yml@refs/heads/main"}

    def start(self, key):
        self.remote_runs[key][-1]["status"] = "in_progress"
        return self.service.handle("start", self.identity(key), {"dispatch_key": key})

    def finish(self, key, status="completed", pull=""):
        self.service.handle("finish", self.identity(key),
                            {"dispatch_key": key, "status": status, "pull_request": pull})

    def stop(self, key, conclusion="success"):
        self.remote_runs[key][-1].update(status="completed", conclusion=conclusion)

    def rerun(self, key):
        run = self.remote_runs[key][-1]
        newer = {**run, "run_attempt": run["run_attempt"] + 1, "status": "in_progress", "conclusion": None}
        self.remote_runs[key][-1] = newer
        self.attempts[f"{newer['id']}:{newer['run_attempt']}"] = newer

    def slot(self, repository=None):
        return self.tables[(repository or self.repository).lower()].get("slot")[0]

    def test_simultaneous_todo_events_and_timer_scans_admit_exactly_one(self):
        issues = [self.issue(n) for n in range(1, 9)]
        events = [{"type": "Issue", "id": issue["id"]} for issue in issues]
        events += [{"type": "Project", "id": PROJECT}] * 8
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self.dispatcher.process, events * 2))
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(len(self.receipts.list()), 1)
        self.assertEqual(len([r for r in self.tables[self.repository.lower()].list()
                              if r.get("status") == "queued"]), 7)
        self.assertTrue(all(i["state"]["id"] == self.route.ready_state_ids[0] for i in issues))
        self.assertFalse(self.runs.list())
        self.linear.status_comment.assert_called()
        self.assertTrue(any("Queued behind" in call.args[2] for call in self.linear.status_comment.call_args_list))

    def test_fifo_survives_restarts_and_waits_for_both_finish_and_github_stop(self):
        keys = [self.enqueue(self.issue(n)) for n in (8, 2, 5)]
        self.queue.advance(self.repository)
        self.start(keys[0])
        self.finish(keys[0])
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.stop(keys[0])
        self.queue = self.restart()
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], keys[1])
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.service.queue = self.queue
        self.start(keys[1])
        self.finish(keys[1])
        self.stop(keys[1])
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], keys[2])

    def test_pending_ci_blocked_and_manual_merge_prs_retain_the_slot(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        pull = self.pulls[first] = pr()
        self.finish(first, pull=pull["url"])
        self.stop(first)
        checks(pull)[0].update(status="IN_PROGRESS", conclusion=None)
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "waiting")
        pull["mergeable"] = "CONFLICTING"
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
        pull.update(mergeable="MERGEABLE", autoMergeRequest=None)
        checks(pull)[0].update(status="COMPLETED", conclusion="SUCCESS")
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
        self.registry.routes[PROJECT] = replace(self.route, auto_merge=False)
        checks(pull)[0].update(status="COMPLETED", conclusion="SUCCESS")
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "waiting")
        self.assertEqual(self.github.dispatch.call_count, 1)
        pull.update(state="MERGED", merged=True)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)

    def test_failed_or_expired_worker_does_not_release_slot_or_allow_takeover(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.now += 10_000
        self.queue.advance(self.repository)
        self.rerun(first)
        with self.assertRaisesRegex(WorkerError, "has not stopped"):
            self.start(first)
        self.assertEqual(self.github.dispatch.call_count, 1)
        # A crash without a finish callback remains blocked after GitHub stops.
        self.stop(first, "failure")
        self.attempts["100:1"].update(status="completed", conclusion="failure")
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")

    def test_manual_rerun_resolves_same_failed_task_and_cannot_overlap_next(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "failed")
        self.stop(first, "failure")
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
        self.rerun(first)
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)
        self.rerun(first)
        with self.assertRaisesRegex(WorkerError, "repository slot"):
            self.start(first)
        with self.assertRaises(WorkerError):
            self.service.handle("accounts", self.identity(first), {"dispatch_key": first})

    def test_cancellation_requires_stopped_worker_and_resolved_pr(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "failed")
        self.issues[str(UUID(int=1))]["state"] = {"id": "canceled", "type": "canceled"}
        self.pulls[first] = pr()
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.stop(first, "failure")
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.pulls[first]["state"] = "CLOSED"
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)
        self.assertEqual(self.tables[self.repository.lower()].get(first)[0]["status"], "abandoned")

    def test_closed_unmerged_pr_is_not_automatic_abandonment(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "failed")
        self.stop(first, "failure")
        self.pulls[first] = {**pr(), "state": "CLOSED"}
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_cancellation_checks_known_run_even_when_github_listing_lags(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.issues[str(UUID(int=1))]["state"] = {"id": "canceled", "type": "canceled"}
        self.github.dispatch_runs.side_effect = lambda *args: []
        self.now += 10_000
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)

    def test_recheck_and_skip_eligibility_changes_before_dispatch(self):
        for change in (lambda i: i["state"].update(id="backlog"),
                       lambda i: i["state"].update(type="canceled"),
                       lambda i: i["project"]["labels"].update(nodes=[]),
                       lambda i: i.update(archivedAt="now"),
                       lambda i: i["project"].update(id="moved")):
            with self.subTest(change=change):
                self.setUp()
                first, middle, last = [self.enqueue(self.issue(n)) for n in (1, 2, 3)]
                self.queue.advance(self.repository)
                change(self.issues[str(UUID(int=2))])
                self.start(first)
                self.finish(first)
                self.stop(first)
                self.queue.advance(self.repository)
                self.assertEqual(self.slot()["active_key"], last)
                self.assertEqual(self.tables[self.repository.lower()].get(middle)[0]["status"], "skipped")
                self.assertEqual(self.github.dispatch.call_count, 2)

    def test_skipped_undispatched_issue_reenters_at_the_tail(self):
        issue = self.issue(1)
        key = self.enqueue(issue)
        issue["state"]["id"] = "backlog"
        self.queue.advance(self.repository)
        next_key = self.enqueue(self.issue(2))
        issue["state"]["id"] = self.route.ready_state_ids[0]
        self.enqueue(issue)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], next_key)
        self.assertNotEqual(key, next_key)

    def test_removed_mapping_skips_undispatched_work(self):
        key = self.enqueue(self.issue(1))
        del self.registry.routes[PROJECT]
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], "")
        self.assertEqual(self.tables[self.repository.lower()].get(key)[0]["status"], "skipped")
        self.github.dispatch.assert_not_called()

    def test_active_work_uses_original_destination_after_mapping_removal(self):
        key = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        self.start(key)
        self.finish(key, "failed")
        self.stop(key, "failure")
        del self.registry.routes[PROJECT]
        self.issues[str(UUID(int=1))]["state"] = {"id": "canceled", "type": "canceled"}
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], "")
        observed_route = self.github.worker_pull.call_args.args[0]
        self.assertEqual((observed_route.repository, observed_route.ref), (self.repository, "main"))

    def test_quota_wait_keeps_fifo_and_concurrent_retries_dispatch_once(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "waiting_for_quota")
        self.now += 901
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)  # Worker still running.
        self.stop(first)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.restart().advance(self.repository), range(24)))
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.assertEqual(self.slot()["active_key"], first)
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)

    def test_ineligible_quota_head_is_skipped_after_worker_stops(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "waiting_for_quota")
        self.issues[str(UUID(int=1))]["project"]["labels"]["nodes"] = []
        self.stop(first)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)

    def test_fast_quota_finish_merges_concurrent_dispatch_acceptance(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        replace_receipt = self.receipts.replace
        raced = False
        def accept_while_finishing(row, etag):
            nonlocal raced
            if row.get("retry_after") and not raced:
                raced = True
                accepted, version = self.receipts.get(first)
                accepted["status"] = "dispatched"
                replace_receipt(accepted, version)
            replace_receipt(row, etag)
        with patch.object(self.receipts, "replace", side_effect=accept_while_finishing):
            self.finish(first, "waiting_for_quota")
        self.assertTrue(raced)
        self.assertEqual(self.receipts.get(first)[0]["retry_after"], self.now + 900)
        self.stop(first)
        self.now += 901
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.assertEqual(self.slot()["active_key"], first)

    def test_same_repository_across_projects_and_case_has_one_slot(self):
        other = str(UUID(int=99))
        self.registry.routes[other] = replace(self.route, project_id=other, repository=self.repository.lower())
        first = self.enqueue(self.issue(1))
        self.enqueue(self.issue(2, other))
        self.queue.advance(self.repository)
        self.queue.advance(self.repository.lower())
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)

    def test_different_repositories_advance_independently(self):
        other = str(UUID(int=99))
        self.registry.routes[other] = replace(self.route, project_id=other, repository="owner/another")
        first = self.enqueue(self.issue(1))
        second = self.enqueue(self.issue(2, other))
        self.queue.reconcile()
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.slot("owner/another")["active_key"], second)
        self.github.dispatch_runs.side_effect = lambda route, key: (_ for _ in ()).throw(RequestError()) if key == first else copy.deepcopy(self.remote_runs[key])
        self.start(second)
        self.finish(second)
        self.stop(second)
        with self.assertLogs(level="ERROR"):
            self.queue.reconcile()
        self.assertEqual(self.slot("owner/another")["active_key"], "")

    def test_dispatch_crash_or_uncertainty_blocks_until_run_and_pr_are_recovered(self):
        for error in (RuntimeError("dispatcher crash"), DispatchError("timeout", uncertain=True)):
            with self.subTest(error=error):
                self.setUp()
                first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
                self.github.dispatch.side_effect = error
                try:
                    self.queue.advance(self.repository)
                except RuntimeError:
                    pass
                self.queue = self.restart()
                self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
                self.assertEqual(self.github.dispatch.call_count, 1)
                # GitHub eventually reveals a run that merged before persisting its outcome.
                self.remote_runs[first] = [{"id": 100, "run_attempt": 1, "status": "completed", "conclusion": "failure"}]
                self.pulls[first] = {**pr(), "state": "MERGED", "merged": True}
                self.github.dispatch.side_effect = self.dispatch
                self.queue.advance(self.repository)
                self.assertEqual(self.slot()["active_key"], second)

    def test_rerun_during_completion_observation_fences_stale_release(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "failed")
        self.stop(first, "failure")
        def pull(*args):
            self.github.worker_pull.side_effect = lambda *args: {**pr(), "state": "MERGED", "merged": True}
            self.rerun(first)
            self.start(first)
            return {**pr(), "state": "MERGED", "merged": True}
        self.github.worker_pull.side_effect = pull
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)

    def test_unregistered_manual_attempt_cannot_inherit_no_change_success(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.rerun(first)
        self.stop(first, "failure")
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_existing_receipts_are_imported_before_new_todo_tasks(self):
        old = self.issue(1)
        key = "f" * 64
        self.receipts.create({"RowKey": key, "issue_id": old["id"], "identifier": old["identifier"],
                              "project_id": PROJECT, "repository": self.repository, "status": "uncertain"})
        self.enqueue(self.issue(2))
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], key)
        self.github.dispatch.assert_not_called()

    def test_routing_pilot_still_completes_without_worker_callbacks(self):
        self.registry.routes[PROJECT] = replace(self.route, workflow="clusterfork-pilot.yml")
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.stop(first)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_completed_receipt_is_not_reset_by_more_todo_events(self):
        issue = self.issue(1)
        first = self.enqueue(issue)
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        self.dispatcher.process({"type": "Issue", "id": issue["id"]})
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], "")

    def test_queue_explanations_retry_after_tracker_outage_including_completion(self):
        first = self.enqueue(self.issue(1))
        self.linear.status_comment.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.linear.status_comment.side_effect = None
        self.queue.advance(self.repository)
        self.assertIn("Validated work completed", self.linear.status_comment.call_args.args[2])

    def test_crash_after_reservation_before_receipt_creation_retries_safely(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.reserve(self.queue.table(self.repository))
        self.assertFalse(self.receipts.list())
        self.restart().advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)

    def test_competing_worker_starts_and_manual_runs_are_fenced(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        barrier = threading.Barrier(2)
        def verify(*args):
            barrier.wait(timeout=5)
        self.github.verify_worker_run.side_effect = verify
        self.github.worker_result.side_effect = lambda *args: {"status": "in_progress"}
        identity = self.identity(first)
        def start(run_id):
            try:
                self.service.handle("start", {**identity, "run_id": run_id}, {"dispatch_key": first})
                return True
            except WorkerError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(start, ("100", "999")))
        self.assertEqual(sum(admitted), 1)
        owner = self.runs.get(first)[0]["owner"]
        self.assertEqual(self.slot()["owner"], owner)

    def test_canceled_ambiguous_dispatch_cannot_later_obtain_credentials(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.github.dispatch.side_effect = DispatchError("timeout", uncertain=True)
        self.queue.advance(self.repository)
        self.issues[str(UUID(int=1))]["state"] = {"id": "canceled", "type": "canceled"}
        self.github.dispatch.side_effect = self.dispatch
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)
        self.dispatch(self.route, {"dispatch_key": first})
        with self.assertRaises(WorkerError):
            self.start(first)


if __name__ == "__main__":
    unittest.main()
