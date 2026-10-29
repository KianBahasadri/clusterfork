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
from repository_queue import RepositoryQueue, dispatch_key
from deployment_control import DeploymentControl
from worker_core import Accounts, WorkerError, WorkerService
from test_remote_worker import ApprovedReadiness, MemoryTable, NOW, PROJECT, claims
from readiness_policy import PROTOCOL
from test_linear_sync import pr, checks


class RepositoryQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.route = self.registry.routes[PROJECT]
        self.repository = self.route.repository
        self.issues, self.remote_runs, self.attempts, self.pulls = {}, {}, {}, {}
        self.selected = set()
        self.queued = set()
        self.receipts, self.runs = MemoryTable(), MemoryTable()
        self.tables = defaultdict(MemoryTable)
        self.linear = Mock(spec=Linear)
        self.linear.issue.side_effect = lambda key: copy.deepcopy(self.issues.get(key))
        self.linear.set_selected.side_effect = self.select
        self.linear.set_queued.side_effect = self.wait
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
                               self.linear, self.github, ApprovedReadiness(), clock=lambda: self.now)

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

    def select(self, issue_id, enabled):
        if enabled:
            self.selected.add(issue_id)
        else:
            self.selected.discard(issue_id)

    def wait(self, issue_id, enabled):
        if enabled:
            self.queued.add(issue_id)
        else:
            self.queued.discard(issue_id)

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
        return self.service.handle("start", self.identity(key), {"dispatch_key": key, "protocol": PROTOCOL})

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

    def test_deployment_pause_holds_new_work_but_allows_current_worker_to_finish(self):
        control = DeploymentControl(MemoryTable())
        self.queue.control = control
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        control.pause("deployment", "a" * 40)
        self.queue.advance(self.repository)
        self.github.dispatch.assert_not_called()
        control.resume("deployment", "a" * 40)
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.start(first)
        control.pause("deployment", "a" * 40)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        control.resume("deployment", "a" * 40)
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 2)

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
        admitted, _ = self.tables[self.repository.lower()].get(self.slot()["active_key"])
        self.assertEqual(self.selected, {admitted["issue_id"]})
        self.assertEqual(self.queued, {issue["id"] for issue in issues} - self.selected)
        self.assertEqual({call.args[0] for call in self.linear.set_selected.call_args_list
                          if call.args[1]}, self.selected)
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
        self.assertEqual(self.selected, {str(UUID(int=2))})
        self.assertEqual(self.queued, {str(UUID(int=5))})
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.service.queue = self.queue
        self.start(keys[1])
        self.finish(keys[1])
        self.stop(keys[1])
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], keys[2])
        self.assertEqual(self.selected, {str(UUID(int=5))})
        self.assertEqual(self.queued, set())

    def test_pending_ci_blocked_and_manual_merge_prs_retain_the_slot(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        pull = self.pulls[first] = pr()
        self.finish(first, pull=pull["url"])
        self.stop(first)
        checks(pull)[0].update(status="IN_PROGRESS", conclusion=None)
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "waiting")
        self.assertEqual(self.selected, {str(UUID(int=1))})
        pull["mergeable"] = "CONFLICTING"
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "blocked")
        self.assertEqual(self.selected, {str(UUID(int=1))})
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
        self.assertEqual(self.selected, {str(UUID(int=2))})

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
        self.assertEqual(self.selected, {str(UUID(int=1))})

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
        self.assertEqual(self.selected, {str(UUID(int=1))})
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
        self.assertEqual(self.selected, {str(UUID(int=1)), str(UUID(int=2))})
        self.github.dispatch_runs.side_effect = lambda route, key: (_ for _ in ()).throw(RequestError()) if key == first else copy.deepcopy(self.remote_runs[key])
        self.start(second)
        self.finish(second)
        self.stop(second)
        with self.assertLogs(level="ERROR"):
            self.queue.reconcile()
        self.assertEqual(self.slot("owner/another")["active_key"], "")
        self.assertEqual(self.selected, {str(UUID(int=1))})

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
                self.assertEqual(self.selected, {str(UUID(int=1))})
                # GitHub eventually reveals a run that merged before persisting its outcome.
                self.remote_runs[first] = [{"id": 100, "run_attempt": 1, "status": "completed", "conclusion": "failure"}]
                self.pulls[first] = {**pr(), "state": "MERGED", "merged": True}
                self.github.dispatch.side_effect = self.dispatch
                self.queue.advance(self.repository)
                self.assertEqual(self.slot()["active_key"], second)
                self.assertEqual(self.selected, {str(UUID(int=2))})

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

    def test_queued_label_requires_eligible_committed_waiting_entry(self):
        first = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        issue = self.issue(2)
        issue["state"]["id"] = "backlog"
        event = {"type": "Issue", "id": issue["id"]}
        self.dispatcher.process(event)
        self.assertEqual(self.queued, set())

        issue["state"]["id"] = self.route.ready_state_ids[0]
        table = self.queue.table(self.repository)
        key = dispatch_key(self.registry, self.route, issue)
        with patch.object(table, "transaction", side_effect=RuntimeError("storage unavailable")):
            with self.assertRaises(RuntimeError):
                self.dispatcher.process(event)
        self.assertEqual(self.queued, set())
        self.assertNotIn(key, table.rows)

        def update(issue_id, enabled):
            if enabled:
                self.assertEqual(issue_id, issue["id"])
                self.assertEqual(table.get(key)[0]["status"], "queued")
                self.assertEqual(self.slot()["active_key"], first)
            self.wait(issue_id, enabled)

        self.linear.set_queued.side_effect = update
        self.dispatcher.process(event)
        self.assertEqual(self.queued, {issue["id"]})
        self.assertEqual(self.selected, {str(UUID(int=1))})
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertFalse(self.runs.list())

    def test_inserted_waiting_intent_survives_restart_and_label_generated_events(self):
        first = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        issue = self.issue(2)
        key = self.enqueue(issue)  # Process stops before advance/reporting.
        table = self.queue.table(self.repository)
        before = table.get(key)[0]
        self.assertTrue(before["selection_pending"])
        self.assertEqual(self.queued, set())
        self.restart().reconcile()
        for _ in range(3):
            self.dispatcher.process({"type": "Issue", "id": issue["id"]})
            self.dispatcher.process({"type": "Project", "id": PROJECT})
        after = table.get(key)[0]
        self.assertEqual(after["sequence"], before["sequence"])
        self.assertEqual(after["queued_at"], before["queued_at"])
        self.assertEqual(after["selection_version"], before["selection_version"])
        self.assertFalse(after["selection_pending"])
        self.assertEqual(len(table.list()), 3)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.queued, {issue["id"]})
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_waiting_marker_failures_preserve_fifo_and_recover_after_restart(self):
        keys = [self.enqueue(self.issue(n)) for n in (1, 2, 3)]
        table = self.queue.table(self.repository)
        positions = {key: table.get(key)[0]["sequence"] for key in keys}
        self.linear.set_queued.side_effect = RequestError(429)
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.assertEqual(self.selected, {str(UUID(int=1))})
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertTrue(all(table.get(key)[0]["selection_pending"] for key in keys))
        self.linear.set_queued.side_effect = self.wait
        self.restart().reconcile()
        self.assertEqual(self.queued, {str(UUID(int=n)) for n in (2, 3)})
        self.assertEqual({key: table.get(key)[0]["sequence"] for key in keys}, positions)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_waiting_ineligibility_clears_marker_without_waiting_for_active_task(self):
        changes = {
            "backlog": lambda i: i["state"].update(id="backlog"),
            "canceled": lambda i: i["state"].update(type="canceled"),
            "archive": lambda i: i.update(archivedAt="now"),
            "project_archive": lambda i: i["project"].update(archivedAt="now"),
            "unenrolled": lambda i: i["project"]["labels"].update(nodes=[]),
            "project_move": lambda i: i["project"].update(id=str(UUID(int=99))),
            "no_project": lambda i: i.update(project=None),
        }
        for source in ("Issue", "timer"):
            for name, change in changes.items():
                with self.subTest(source=source, change=name):
                    self.setUp()
                    issues = [self.issue(n) for n in (1, 2, 3)]
                    keys = [self.enqueue(issue) for issue in issues]
                    self.queue.advance(self.repository)
                    change(issues[1])
                    if source == "Issue":
                        self.dispatcher.process({"type": source, "id": issues[1]["id"]})
                    else:
                        self.restart().reconcile()
                    self.assertEqual(self.queued, {issues[2]["id"]})
                    self.assertEqual(self.selected, {issues[0]["id"]})
                    self.assertEqual(self.slot()["active_key"], keys[0])
                    self.assertEqual(self.queue.table(self.repository).get(keys[1])[0]["status"], "skipped")
                    self.assertEqual(self.github.dispatch.call_count, 1)
                    self.start(keys[0])
                    self.finish(keys[0])
                    self.stop(keys[0])
                    self.queue.advance(self.repository)
                    self.assertEqual(self.slot()["active_key"], keys[2])

    def test_empty_project_scan_still_cleans_up_waiting_issues(self):
        issues = [self.issue(n) for n in (1, 2)]
        for issue in issues:
            self.enqueue(issue)
        self.queue.advance(self.repository)
        issues[1]["project"]["labels"]["nodes"] = []
        self.linear.project_issues.side_effect = lambda project: []
        self.dispatcher.process({"type": "Project", "id": PROJECT})
        self.assertEqual(self.queued, set())
        self.assertEqual(self.selected, {issues[0]["id"]})
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_moving_waiting_issue_to_another_mapped_repository_preserves_new_marker(self):
        other = str(UUID(int=99))
        self.registry.routes[other] = replace(self.route, project_id=other, repository="owner/another")
        first, old = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        other_active = self.enqueue(self.issue(3, other))
        self.queue.advance("owner/another")
        issue = self.issues[str(UUID(int=2))]
        issue["project"]["id"] = other
        self.dispatcher.process({"type": "Issue", "id": issue["id"]})
        self.assertEqual(self.queue.table(self.repository).get(old)[0]["status"], "skipped")
        new = dispatch_key(self.registry, self.registry.routes[other], issue)
        self.assertEqual(self.queue.table("owner/another").get(new)[0]["status"], "queued")
        self.assertEqual(self.queued, {issue["id"]})
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.slot("owner/another")["active_key"], other_active)
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_waiting_cleanup_survives_github_outage_and_preserves_position_on_linear_outage(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        table = self.queue.table(self.repository)
        position = table.get(second)[0]["sequence"]
        issue_id = str(UUID(int=2))

        def unavailable(key):
            if key == issue_id:
                raise RequestError()
            return copy.deepcopy(self.issues.get(key))

        self.linear.issue.side_effect = unavailable
        with self.assertLogs(level="ERROR"):
            self.restart().reconcile()
        self.assertEqual(table.get(second)[0]["sequence"], position)
        self.assertEqual(table.get(second)[0]["status"], "queued")
        self.assertEqual(self.queued, {issue_id})
        self.issues[issue_id]["state"]["id"] = "backlog"
        self.linear.issue.side_effect = lambda key: copy.deepcopy(self.issues.get(key))
        self.github.dispatch_runs.side_effect = RequestError()
        with self.assertRaises(RequestError):
            self.queue.advance(self.repository)
        self.assertEqual(self.queued, set())
        self.assertEqual(table.get(second)[0]["status"], "skipped")
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_delayed_ineligibility_observation_cannot_remove_reentered_position(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        issue = self.issues[str(UUID(int=2))]
        issue["state"]["id"] = "backlog"
        started, resume = threading.Event(), threading.Event()
        delayed = False

        def observe(key):
            nonlocal delayed
            snapshot = copy.deepcopy(self.issues.get(key))
            if key == issue["id"] and not delayed:
                delayed = True
                started.set()
                self.assertTrue(resume.wait(5))
            return snapshot

        table = self.queue.table(self.repository)
        self.linear.issue.side_effect = observe
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.queue.refresh_waiting_entry, table, second)
            try:
                self.assertTrue(started.wait(5))
                self.dispatcher.process({"type": "Issue", "id": issue["id"]})
                issue["state"]["id"] = self.route.ready_state_ids[0]
                self.dispatcher.process({"type": "Issue", "id": issue["id"]})
                position = table.get(second)[0]["sequence"]
            finally:
                resume.set()
            future.result(timeout=5)
        self.assertEqual(table.get(second)[0]["sequence"], position)
        self.assertEqual(table.get(second)[0]["status"], "queued")
        self.assertEqual(self.queued, {issue["id"]})
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_backlog_removal_failure_and_todo_reentry_join_tail_without_losing_intent(self):
        issues = [self.issue(n) for n in (1, 2, 3)]
        first, second, third = [self.enqueue(issue) for issue in issues]
        self.queue.advance(self.repository)
        table = self.queue.table(self.repository)
        original = table.get(second)[0]["sequence"]
        issues[1]["state"]["id"] = "backlog"
        self.linear.set_queued.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.dispatcher.process({"type": "Issue", "id": issues[1]["id"]})
        self.assertEqual(table.get(second)[0]["status"], "skipped")
        self.assertTrue(table.get(second)[0]["selection_pending"])
        self.assertEqual(self.slot()["active_key"], first)
        self.linear.set_queued.side_effect = self.wait
        self.restart().reconcile()
        self.assertEqual(self.queued, {issues[2]["id"]})
        issues[1]["state"]["id"] = self.route.ready_state_ids[0]
        self.dispatcher.process({"type": "Issue", "id": issues[1]["id"]})
        self.assertGreater(table.get(second)[0]["sequence"], original)
        self.assertGreater(table.get(second)[0]["sequence"], table.get(third)[0]["sequence"])
        self.assertEqual(self.queued, {issues[n]["id"] for n in (1, 2)})
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], third)
        self.assertEqual(self.queued, {issues[1]["id"]})
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_timer_adopts_legacy_waiting_rows_and_repairs_unacknowledged_labels(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        table = self.queue.table(self.repository)
        row, etag = table.get(second)
        sequence = row["sequence"]
        for name in list(row):
            if name.startswith("selection_"):
                del row[name]
        table.replace(row, etag)
        self.queued.clear()
        self.restart().reconcile()
        self.assertEqual(self.queued, {str(UUID(int=2))})
        self.assertEqual(table.get(second)[0]["sequence"], sequence)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_late_queued_add_after_admission_is_repaired_even_if_reporter_crashes(self):
        for error in (None, RequestError(), SystemExit()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
                self.queue.advance(self.repository)
                self.start(first)
                self.finish(first)
                self.stop(first)
                table = self.queue.table(self.repository)
                started, resume = threading.Event(), threading.Event()
                delayed = False

                def update(issue_id, enabled):
                    nonlocal delayed
                    if enabled and not delayed:
                        delayed = True
                        started.set()
                        self.assertTrue(resume.wait(5))
                        self.wait(issue_id, enabled)
                        if error is not None:
                            raise error
                    else:
                        self.wait(issue_id, enabled)

                self.linear.set_queued.side_effect = update
                with patch("selection_reporting.logging.error"), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.queue.selection.sync, table, second, refresh=True)
                    try:
                        self.assertTrue(started.wait(5))
                        self.queue.advance(self.repository)
                        self.assertEqual(self.slot()["active_key"], second)
                        self.assertEqual(self.queued, set())
                        self.assertEqual(self.selected, {str(UUID(int=2))})
                    finally:
                        resume.set()
                    if isinstance(error, SystemExit):
                        with self.assertRaises(SystemExit):
                            future.result(timeout=5)
                    else:
                        future.result(timeout=5)
                self.linear.set_queued.side_effect = self.wait
                self.restart().reconcile()
                self.assertEqual(self.queued, set())
                self.assertEqual(self.selected, {str(UUID(int=2))})
                self.assertEqual(self.github.dispatch.call_count, 2)

    def test_late_queued_removal_preserves_reentry_through_another_project_or_repository(self):
        for repository in (self.repository, "owner/another"):
            with self.subTest(repository=repository):
                self.setUp()
                other = str(UUID(int=99))
                self.registry.routes[other] = replace(self.route, project_id=other, repository=repository)
                first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
                self.queue.advance(self.repository)
                if repository != self.repository:
                    self.enqueue(self.issue(3, other))
                    self.queue.advance(repository)
                issue = self.issues[str(UUID(int=2))]
                issue["state"]["id"] = "backlog"
                self.dispatcher.process({"type": "Issue", "id": issue["id"]})
                table = self.queue.table(self.repository)
                started, resume = threading.Event(), threading.Event()
                delayed = False

                def update(issue_id, enabled):
                    nonlocal delayed
                    if not enabled and not delayed:
                        delayed = True
                        started.set()
                        self.assertTrue(resume.wait(5))
                    self.wait(issue_id, enabled)

                self.linear.set_queued.side_effect = update
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.queue.selection.sync, table, second, refresh=True)
                    try:
                        self.assertTrue(started.wait(5))
                        issue["project"]["id"] = other
                        issue["state"]["id"] = self.route.ready_state_ids[0]
                        self.dispatcher.process({"type": "Issue", "id": issue["id"]})
                        self.assertEqual(self.queued, {issue["id"]})
                    finally:
                        resume.set()
                    future.result(timeout=5)
                self.assertEqual(self.queued, {issue["id"]})
                self.assertEqual(self.slot()["active_key"], first)
                self.assertEqual(self.github.dispatch.call_count, 1 if repository == self.repository else 2)

    def test_crash_after_reservation_before_receipt_creation_retries_safely(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        table = self.queue.table(self.repository)
        with patch.object(self.queue.selection, "sync", side_effect=SystemExit), self.assertRaises(SystemExit):
            self.queue.reserve(table)
        self.assertTrue(table.get(first)[0]["selection_pending"])
        self.assertEqual(self.selected, set())
        self.assertFalse(self.receipts.list())
        self.restart().advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.selected, {str(UUID(int=1))})

    def test_only_admitted_todo_is_marked_before_dispatch_without_changing_assignees(self):
        issues = [self.issue(n) for n in (1, 2, 3)]
        issues[0]["state"]["id"] = "backlog"
        for issue in issues:
            issue.update(assignee={"id": "human"}, labels=["bug"])
        before = copy.deepcopy(issues)

        def dispatch(route, inputs):
            self.assertEqual(self.selected, {issues[1]["id"]})
            self.assertFalse(self.runs.list())  # No worker has reached start.
            self.assertFalse(self.remote_runs)
            self.dispatch(route, inputs)

        self.github.dispatch.side_effect = dispatch
        self.dispatcher.process({"type": "Project", "id": PROJECT})
        self.assertEqual(self.selected, {issues[1]["id"]})
        self.assertEqual(issues, before)
        self.linear.set_state.assert_not_called()
        self.linear.set_stage.assert_not_called()

    def test_slow_steering_cannot_delay_admission_or_the_first_label(self):
        issue = self.issue(1)
        self.dispatcher.steering = Mock()
        markers = {}

        def steering(*args, **kwargs):
            self.assertEqual(self.selected, {issue["id"]})
            self.now += 12
            markers["steering_finished"] = self.now

        def selected(issue_id, enabled):
            self.now += 0.3
            self.select(issue_id, enabled)
            markers["label_acknowledged"] = self.now

        self.dispatcher.steering.observe.side_effect = steering
        self.linear.set_selected.side_effect = selected
        self.dispatcher.process({"type": "Issue", "id": issue["id"]})
        self.assertAlmostEqual(markers["label_acknowledged"] - NOW, 0.3)
        self.assertAlmostEqual(markers["steering_finished"] - NOW, 12.3)

    def test_issue_event_can_select_while_an_unrelated_scan_is_stalled(self):
        issue = self.issue(1)
        scanning, resume = threading.Event(), threading.Event()

        def scan(project):
            scanning.set()
            if not resume.wait(5):
                raise TimeoutError("test did not resume project scan")
            return [copy.deepcopy(issue)]

        self.linear.project_issues.side_effect = scan
        with ThreadPoolExecutor(max_workers=2) as pool:
            project = pool.submit(self.dispatcher.process, {"type": "Project", "id": PROJECT})
            try:
                self.assertTrue(scanning.wait(5))
                event = pool.submit(self.dispatcher.process, {"type": "Issue", "id": issue["id"]})
                event.result(timeout=5)
                self.assertEqual(self.selected, {issue["id"]})
                self.assertFalse(project.done())
            finally:
                resume.set()
            project.result(timeout=5)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_new_selection_precedes_historical_label_and_comment_repairs(self):
        first = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        second = self.enqueue(self.issue(2))
        table = self.tables[self.repository.lower()]
        row, etag = table.get(first)
        row.update(selection_pending=True, reported_reason="")
        table.replace(row, etag)
        markers = {}

        def selected(issue_id, enabled):
            self.now += 0.3 if enabled else 8
            self.select(issue_id, enabled)
            if enabled:
                self.assertEqual(self.slot()["active_key"], second)
                markers["label_acknowledged"] = self.now

        def comment(*args):
            self.now += 4

        self.linear.set_selected.side_effect = selected
        self.linear.status_comment.side_effect = comment
        self.queue.advance(self.repository)
        self.assertAlmostEqual(markers["label_acknowledged"] - NOW, 0.3)
        self.assertFalse(table.get(first)[0]["selection_pending"])
        self.assertEqual(self.selected, {str(UUID(int=2))})
        self.assertTrue(table.get(first)[0]["reported_reason"])

    def test_handoff_reports_next_selection_before_the_old_removal(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        writes = []

        def selected(issue_id, enabled):
            writes.append((issue_id, enabled))
            if enabled:
                self.assertEqual(self.slot()["active_key"], second)
            self.select(issue_id, enabled)

        self.linear.set_selected.side_effect = selected
        self.queue.advance(self.repository)
        self.assertEqual(writes, [(str(UUID(int=2)), True), (str(UUID(int=1)), False)])
        self.assertEqual(self.selected, {str(UUID(int=2))})

    def test_timing_follows_first_eligible_observation_and_durable_selection(self):
        issue = self.issue(1)
        timing = {"source_updated_at": NOW - 1, "webhook_at": NOW - 0.9, "received_at": NOW - 0.8,
                  "enqueued_at": NOW - 0.7, "processing_at": NOW - 0.1}
        first = self.queue.enqueue(issue, self.route, timing=timing)
        self.assertFalse(self.tables[self.repository.lower()].get(first)[0].get("selected_at"))
        self.linear.set_selected.assert_not_called()
        self.now += 1
        self.queue.enqueue(issue, self.route, timing={"received_at": self.now})
        self.queue.advance(self.repository)
        row, _ = self.tables[self.repository.lower()].get(first)
        for name, value in timing.items():
            self.assertEqual(row[name], value)
        self.assertEqual(row["queued_at"], NOW)
        self.assertEqual(row["selected_at"], NOW + 1)
        self.assertEqual(row["selection_reported_at"], NOW + 1)

    def test_label_failure_can_recover_on_the_same_event_without_redispatch(self):
        first = self.enqueue(self.issue(1))
        attempts = []

        def selected(issue_id, enabled):
            attempts.append((issue_id, enabled))
            if len(attempts) == 1:
                raise RequestError(429)
            self.select(issue_id, enabled)

        self.linear.set_selected.side_effect = selected
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.selected, {str(UUID(int=1))})
        self.assertFalse(self.tables[self.repository.lower()].get(first)[0]["selection_pending"])

    def test_historical_removal_still_recovers_when_active_github_observation_fails(self):
        first, _ = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.queue.advance(self.repository)
        table = self.tables[self.repository.lower()]
        row, etag = table.get(first)
        row["selection_pending"] = True
        table.replace(row, etag)
        self.selected.add(str(UUID(int=1)))  # A late write needs repair.
        self.github.dispatch_runs.side_effect = RequestError()
        with self.assertRaises(RequestError):
            self.queue.advance(self.repository)
        self.assertEqual(self.selected, {str(UUID(int=2))})
        self.assertFalse(table.get(first)[0]["selection_pending"])
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_label_outages_retry_after_restarts_without_releasing_or_redispatching(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        table = self.queue.table(self.repository)
        self.linear.set_selected.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertTrue(table.get(first)[0]["selection_pending"])
        self.linear.set_selected.side_effect = self.select
        self.restart().reconcile()
        self.assertEqual(self.selected, {str(UUID(int=1))})
        self.assertFalse(table.get(first)[0]["selection_pending"])
        self.assertEqual(self.github.dispatch.call_count, 1)

        self.start(first)
        self.finish(first)
        self.stop(first)
        self.linear.set_selected.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.restart().advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.assertTrue(table.get(first)[0]["selection_pending"])
        self.assertTrue(table.get(second)[0]["selection_pending"])
        self.linear.set_selected.side_effect = self.select
        self.restart().reconcile()
        self.assertEqual(self.selected, {str(UUID(int=2))})
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_failed_removal_retries_with_empty_slot_and_removed_project_mapping(self):
        key = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        self.start(key)
        self.finish(key)
        self.stop(key)
        self.linear.set_selected.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], "")
        self.assertTrue(self.tables[self.repository.lower()].get(key)[0]["selection_pending"])
        del self.registry.routes[PROJECT]
        self.linear.set_selected.side_effect = self.select
        self.restart().reconcile()
        self.assertEqual(self.selected, set())
        self.assertFalse(self.tables[self.repository.lower()].get(key)[0]["selection_pending"])
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_delayed_selection_add_is_repaired_after_ownership_transfers(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first)
        self.stop(first)
        table = self.queue.table(self.repository)
        started, resume = threading.Event(), threading.Event()
        delayed = False

        def update(issue_id, enabled):
            nonlocal delayed
            if issue_id == str(UUID(int=1)) and enabled and not delayed:
                delayed = True
                started.set()
                self.assertTrue(resume.wait(timeout=5))
            self.select(issue_id, enabled)

        self.linear.set_selected.side_effect = update
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.queue.selection.sync, table, first, refresh=True)
            try:
                self.assertTrue(started.wait(timeout=5))
                self.queue.advance(self.repository)
                self.assertEqual(self.slot()["active_key"], second)
                self.assertEqual(self.selected, {str(UUID(int=2))})
            finally:
                resume.set()
            future.result(timeout=5)
        self.assertEqual(self.selected, {str(UUID(int=2))})
        self.linear.set_stage.assert_not_called()

    def test_timer_repairs_late_ambiguous_write_even_if_reporting_process_was_lost(self):
        for error in (RequestError(), SystemExit()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                first = self.enqueue(self.issue(1))
                self.queue.advance(self.repository)
                self.start(first)
                self.finish(first)
                self.stop(first)
                table = self.queue.table(self.repository)
                started, resume = threading.Event(), threading.Event()
                delayed = False

                def update(issue_id, enabled):
                    nonlocal delayed
                    if enabled and not delayed:
                        delayed = True
                        started.set()
                        self.assertTrue(resume.wait(timeout=5))
                        self.select(issue_id, enabled)
                        raise error
                    self.select(issue_id, enabled)

                self.linear.set_selected.side_effect = update
                with patch("selection_reporting.logging.error") as log, ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.queue.selection.sync, table, first, refresh=True)
                    try:
                        self.assertTrue(started.wait(timeout=5))
                        self.queue.advance(self.repository)
                        self.assertEqual(self.selected, set())
                        self.assertFalse(table.get(first)[0]["selection_pending"])
                    finally:
                        resume.set()
                    if isinstance(error, Exception):
                        future.result(timeout=5)
                        log.assert_called_once()
                        self.assertTrue(table.get(first)[0]["selection_pending"])
                    else:
                        with self.assertRaises(SystemExit):
                            future.result(timeout=5)
                        self.assertFalse(table.get(first)[0]["selection_pending"])
                self.assertEqual(self.selected, {str(UUID(int=1))})
                self.linear.set_selected.side_effect = self.select
                self.restart().reconcile()
                self.assertEqual(self.selected, set())
                self.assertEqual(self.github.dispatch.call_count, 1)

    def test_delayed_removal_preserves_same_issue_readmitted_through_another_project_or_repository(self):
        for repository in (self.repository, "owner/another"):
            with self.subTest(repository=repository):
                self.setUp()
                issue = self.issue(1)
                first = self.enqueue(issue)
                self.queue.advance(self.repository)
                self.start(first)
                self.finish(first)
                self.stop(first)
                self.queue.advance(self.repository)
                table = self.queue.table(self.repository)
                started, resume = threading.Event(), threading.Event()
                delayed = False

                def update(issue_id, enabled):
                    nonlocal delayed
                    if not enabled and not delayed:
                        delayed = True
                        started.set()
                        self.assertTrue(resume.wait(timeout=5))
                    self.select(issue_id, enabled)

                self.linear.set_selected.side_effect = update
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.queue.selection.sync, table, first, refresh=True)
                    try:
                        self.assertTrue(started.wait(timeout=5))
                        other = str(UUID(int=99))
                        self.registry.routes[other] = replace(self.route, project_id=other, repository=repository)
                        issue["project"]["id"] = other
                        second = self.enqueue(issue)
                        self.queue.advance(repository)
                        self.assertEqual(self.slot(repository)["active_key"], second)
                        self.assertEqual(self.selected, {issue["id"]})
                    finally:
                        resume.set()
                    future.result(timeout=5)
                self.assertEqual(self.selected, {issue["id"]})
                self.restart().reconcile()
                self.assertEqual(self.selected, {issue["id"]})

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
                self.service.handle("start", {**identity, "run_id": run_id}, {"dispatch_key": first, "protocol": PROTOCOL})
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
