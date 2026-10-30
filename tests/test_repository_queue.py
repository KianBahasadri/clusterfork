"""Repository admission safety across priorities, deliveries, crashes, and reruns."""

import copy
import json
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
from linear_sync import comment_id
from pending_writes import WRITE_SECONDS
from readiness_policy import PROTOCOL
from test_linear_sync import CommentAPI, pr, checks
from worker_reporting import AUTO_REPAIR_LABEL, worker_branch


class RepositoryQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.route = self.registry.routes[PROJECT]
        self.repository = self.route.repository
        self.issues, self.remote_runs, self.attempts, self.pulls = {}, {}, {}, {}
        self.dependencies = defaultdict(list)
        self.selected = set()
        self.queued = set()
        self.receipts, self.runs = MemoryTable(), MemoryTable()
        self.tables = defaultdict(MemoryTable)
        self.linear = Mock(spec=Linear)
        self.linear.issue_dependencies.side_effect = lambda key: copy.deepcopy(self.dependencies[key])
        self.linear.issue.side_effect = lambda key: copy.deepcopy(self.issues.get(key))
        self.linear.set_selected.side_effect = self.select
        self.linear.set_queued.side_effect = self.wait
        self.linear.set_state.side_effect = self.set_state
        self.linear.create_issue.side_effect = self.create_repair
        self.linear.stage_label.return_value = "repair-label-id"
        self.linear.project_issues.side_effect = lambda project: [copy.deepcopy(i) for i in self.issues.values()
                                                                if i["project"]["id"] == project]
        self.github = Mock(spec=GitHub)
        self.github.dispatch.side_effect = self.dispatch
        self.github.dispatch_runs.side_effect = lambda route, key: copy.deepcopy(self.remote_runs.get(key, []))
        self.github.worker_pull.side_effect = lambda route, receipt, key, url: copy.deepcopy(
            self.pulls.get(receipt.get("target_dispatch_key", key)))
        self.github.worker_result.side_effect = lambda repo, owner: copy.deepcopy(self.attempts[owner])
        self.queue = self.restart()
        self.service = WorkerService(self.registry, self.receipts, self.runs, Accounts(MemoryTable()),
                                     self.linear, self.github, self.queue, clock=lambda: self.now)
        self.dispatcher = Dispatcher(self.registry, self.linear, self.queue)

    def restart(self):
        return RepositoryQueue(self.registry, self.receipts, self.runs, self.tables.__getitem__,
                               self.linear, self.github, ApprovedReadiness(), clock=lambda: self.now)

    def issue(self, number, project=PROJECT, *, priority=0):
        issue = {"id": str(UUID(int=number)), "identifier": f"KIAN-{number}", "title": "Test",
                 "team": {"id": str(UUID(int=999))}, "labels": {"nodes": []},
                 "url": f"https://linear.app/test/issue/KIAN-{number}/test",
                 "priority": priority,
                 "state": {"id": self.registry.routes[project].ready_state_ids[0], "type": "unstarted"},
                 "createdAt": f"2026-10-01T00:00:{number:02d}Z", "archivedAt": None,
                 "project": {"id": project, "archivedAt": None,
                             "labels": {"nodes": [{"name": self.registry.enrollment_label}]}}}
        self.issues[issue["id"]] = issue
        return issue

    def create_repair(self, fields):
        with self.receipts.lock:
            if fields["id"] not in self.issues:
                parent = self.issues[fields["parentId"]]
                self.issues[fields["id"]] = {
                    **copy.deepcopy(parent), "id": fields["id"], "identifier": "KIAN-1000",
                    "url": "https://linear.app/test/issue/KIAN-1000/auto-repair",
                    "title": fields["title"], "description": fields["description"],
                    "priority": fields["priority"], "labels": {"nodes": [{"name": AUTO_REPAIR_LABEL}]},
                    "state": {"id": fields["stateId"], "type": "unstarted"}}
            return copy.deepcopy(self.issues[fields["id"]])

    def set_state(self, issue_id, state_id):
        self.issues[issue_id]["state"] = {"id": state_id, "type": (
            "completed" if state_id == self.route.linear_states.done else "started")}

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

    def block(self, dependent, prerequisite):
        prerequisite.setdefault("trashed", False)
        self.dependencies[dependent["id"]].append({
            "id": str(UUID(int=10_000 + len(self.dependencies[dependent["id"]]))),
            "type": "blocks", "issue": prerequisite})

    def test_kian_904_waits_for_905_despite_earlier_fifo_and_higher_priority(self):
        dependent = self.issue(904, priority=1)
        prerequisite = self.issue(905, priority=4)
        self.block(dependent, prerequisite)
        first = self.enqueue(dependent)
        second = self.enqueue(prerequisite)
        result = self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], second)
        self.assertEqual(result[first]["status"], "dependency_waiting")
        self.assertIn("KIAN-905 (unstarted)", result[first]["reason"])
        self.assertEqual(self.queued, {dependent["id"]})
        self.assertEqual(self.selected, {prerequisite["id"]})
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.start(second)
        self.finish(second)
        self.stop(second)
        prerequisite["state"] = {"id": "done", "type": "completed"}
        self.dispatcher.process({"type": "Issue", "id": prerequisite["id"]})
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_multiple_blockers_require_all_completed_types_and_preserve_runnable_order(self):
        dependent = self.issue(1, priority=1)
        completed, unresolved = self.issue(2), self.issue(3)
        completed["state"] = {"id": "custom-completion", "type": "completed", "name": "Shipped"}
        unresolved["state"]["name"] = "Done"  # Names cannot grant completion.
        self.block(dependent, completed)
        self.block(dependent, unresolved)
        key = self.enqueue(dependent)
        normal = self.enqueue(self.issue(4, priority=2))
        low = self.enqueue(self.issue(5, priority=4))
        result = self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], normal)
        self.assertNotIn("KIAN-2", result[key]["reason"])
        self.assertIn("KIAN-3", result[key]["reason"])
        self.start(normal)
        self.finish(normal)
        self.stop(normal)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], low)
        self.start(low)
        self.finish(low)
        self.stop(low)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], "")
        unresolved["state"]["type"] = "completed"
        self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], key)

    def test_canceled_deleted_missing_and_unverifiable_prerequisites_keep_slot_free(self):
        for source in (None, {"id": str(UUID(int=2)), "identifier": "KIAN-2", "trashed": False,
                              "state": {"type": "canceled"}},
                       {"id": str(UUID(int=2)), "identifier": "KIAN-2", "trashed": True,
                        "state": {"type": "completed"}},
                       {"id": str(UUID(int=2)), "identifier": "KIAN-2", "trashed": False, "state": None},
                       {"id": str(UUID(int=2)), "identifier": "KIAN-2", "state": {"type": "completed"}}):
            with self.subTest(source=source):
                self.setUp()
                dependent = self.issue(1)
                self.dependencies[dependent["id"]] = [{"id": str(UUID(int=90)), "type": "blocks", "issue": source}]
                key = self.enqueue(dependent)
                self.queue.advance(self.repository)
                self.assertEqual(self.slot()["active_key"], "")
                self.assertEqual(self.queued, {dependent["id"]})
                self.assertFalse(self.selected)
                self.assertFalse(self.github.dispatch.called)
                body = self.linear.status_comment.call_args.args[2]
                self.assertIn("prerequisite", body)
                self.assertEqual(self.linear.status_comment.call_args.args[1], comment_id(key, "queue"))
                self.dependencies[dependent["id"]] = []  # Explicit relation removal releases it.
                self.dispatcher.process({"type": "Issue", "id": dependent["id"]})
                self.assertEqual(self.slot()["active_key"], key)

    def test_dependency_lookup_failure_cannot_reuse_stale_completion_and_recovers(self):
        active = self.enqueue(self.issue(1))
        dependent = self.issue(2)
        prerequisite = self.issue(3)
        prerequisite["state"]["type"] = "completed"
        self.block(dependent, prerequisite)
        waiting = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.start(active)
        self.finish(active)
        self.stop(active)
        self.linear.issue_dependencies.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], "")
        self.assertEqual(self.queue.table(self.repository).get(waiting)[0]["sequence"], 2)
        self.assertEqual(self.github.dispatch.call_count, 1)
        self.linear.issue_dependencies.side_effect = lambda key: copy.deepcopy(self.dependencies[key])
        self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], waiting)

    def test_new_blocker_while_queued_is_refetched_at_handoff_without_webhook(self):
        active = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        dependent, prerequisite = self.issue(2, priority=1), self.issue(3, priority=4)
        waiting, runnable = self.enqueue(dependent), self.enqueue(prerequisite)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], active)
        self.block(dependent, prerequisite)
        self.start(active)
        self.finish(active)
        self.stop(active)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], runnable)
        self.assertEqual(self.queue.table(self.repository).get(waiting)[0]["status"], "queued")

    def test_cross_project_unmapped_blocker_event_wakes_waiting_dependent(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        prerequisite["project"] = None
        self.block(dependent, prerequisite)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], "")
        prerequisite["state"] = {"id": "shipped", "type": "completed"}
        self.dispatcher.process({"type": "Issue", "id": prerequisite["id"]})
        self.assertEqual(self.slot()["active_key"], key)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_relation_removal_event_refetches_dependencies_and_stable_comment(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        self.block(dependent, prerequisite)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.queue.reconcile()
        self.assertEqual(self.linear.status_comment.call_count, 1)
        self.assertFalse(self.slot()["active_key"])
        self.dependencies[dependent["id"]] = []
        self.dispatcher.process({"type": "IssueRelation", "id": str(UUID(int=80)),
                                 "issue_ids": [prerequisite["id"], dependent["id"]]})
        self.assertEqual(self.slot()["active_key"], key)
        self.assertEqual({call.args[1] for call in self.linear.status_comment.call_args_list}, {comment_id(key, "queue")})

    def test_blocker_completion_refreshes_comment_while_independent_work_is_active(self):
        active = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        dependent, prerequisite = self.issue(2), self.issue(3)
        prerequisite["project"] = None
        self.block(dependent, prerequisite)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.assertIn("KIAN-3", self.queue.table(self.repository).get(key)[0]["reported_reason"])
        prerequisite["state"]["type"] = "completed"
        self.dispatcher.process({"type": "Issue", "id": prerequisite["id"]})
        self.assertEqual(self.slot()["active_key"], active)
        self.assertEqual(self.queue.table(self.repository).get(key)[0]["reported_reason"],
                         "Queued behind KIAN-1. Waiting for the previous GitHub worker to stop.")

    def test_cycles_wait_without_workers_or_stalling_independent_work(self):
        first, second = self.issue(1, priority=1), self.issue(2, priority=1)
        self.block(first, second)
        self.block(second, first)
        keys = [self.enqueue(issue) for issue in (first, second)]
        self.queue.reconcile()
        self.assertFalse(self.github.dispatch.called)
        self.assertFalse(self.slot()["active_key"])
        independent = self.issue(3, priority=4)
        self.dispatcher.process({"type": "Issue", "id": independent["id"]})
        self.assertEqual(self.slot()["active_key"], dispatch_key(self.registry, self.route, independent))
        for key in keys:
            self.assertEqual(self.queue.table(self.repository).get(key)[0]["status"], "queued")
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_blocker_added_after_dispatch_denies_start_then_returns_position_to_queue(self):
        dependent = self.issue(1, priority=1)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        prerequisite = self.issue(2, priority=4)
        prerequisite_key = self.enqueue(prerequisite)
        self.block(dependent, prerequisite)
        with self.assertRaisesRegex(WorkerError, "prerequisites"):
            self.start(key)
        self.assertFalse(self.runs.list())
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], key)  # Dispatched run is still alive.
        self.stop(key, "failure")
        self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], prerequisite_key)
        self.assertEqual(self.queued, {dependent["id"]})
        self.start(prerequisite_key)
        self.finish(prerequisite_key)
        self.stop(prerequisite_key)
        prerequisite["state"] = {"id": "done", "type": "completed"}
        self.queue.reconcile()
        self.assertEqual(self.slot()["active_key"], key)
        self.assertEqual(len(self.remote_runs[key]), 2)
        self.assertEqual(self.receipts.get(key)[0]["dependency_epoch"],
                         self.queue.table(self.repository).get(key)[0]["dependency_epoch"])
        self.start(key)

    def test_final_coding_guard_denies_blocker_added_during_account_selection(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.start(key)
        self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "selecting_account"})
        self.block(dependent, prerequisite)
        with self.assertRaisesRegex(WorkerError, "prerequisites"):
            self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "coding"})
        self.assertEqual(self.runs.get(key)[0]["stage"], "selecting_account")
        self.finish(key, "failed")
        self.assertEqual(self.runs.get(key)[0]["status"], "waiting_for_dependencies")
        self.stop(key, "failure")
        self.queue.reconcile()
        self.assertFalse(self.slot()["active_key"])

    def test_added_blocker_does_not_interrupt_already_started_coding(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.start(key)
        self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "coding"})
        self.block(dependent, prerequisite)
        self.queue.reconcile()
        self.assertEqual(self.slot()["active_key"], key)
        self.assertFalse(self.queue.table(self.repository).get(key)[0].get("dependency_hold"))
        self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "publishing"})

    def test_unavailable_dependencies_hold_only_the_affected_candidate(self):
        dependent = self.issue(1, priority=1)
        key = self.enqueue(dependent)
        runnable = self.enqueue(self.issue(2, priority=4))

        def dependencies(issue_id):
            if issue_id == dependent["id"]:
                raise RequestError()
            return []

        self.linear.issue_dependencies.side_effect = dependencies
        with self.assertLogs(level="ERROR"):
            results = self.queue.advance(self.repository)
        self.assertEqual(results[key]["status"], "dependency_waiting")
        self.assertEqual(self.slot()["active_key"], runnable)
        self.assertEqual(self.github.dispatch.call_count, 1)

    def test_final_coding_check_refetches_after_the_initial_broker_check(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.start(key)
        self.block(dependent, prerequisite)
        self.linear.issue_dependencies.side_effect = [[], copy.deepcopy(self.dependencies[dependent["id"]])]
        with self.assertRaisesRegex(WorkerError, "prerequisites"):
            self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "coding"})
        self.assertEqual(self.runs.get(key)[0]["stage"], "initializing")
        self.assertTrue(self.runs.get(key)[0]["dependency_wait"])
        self.assertFalse(self.queue.table(self.repository).get(key)[0].get("coding_started"))

    def test_stale_dependency_hold_cannot_interrupt_a_concurrent_coding_start(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.start(key)
        self.block(dependent, prerequisite)
        entered, release = threading.Event(), threading.Event()
        blocked_snapshot = copy.deepcopy(self.dependencies[dependent["id"]])

        def delayed(issue_id):
            if not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(10))
                return blocked_snapshot
            return []

        self.linear.issue_dependencies.side_effect = delayed
        with ThreadPoolExecutor(max_workers=1) as pool:
            observation = pool.submit(self.queue.dependencies_ready, dependent, self.route)
            self.assertTrue(entered.wait(10))
            try:
                self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "coding"})
            finally:
                release.set()
            self.assertTrue(observation.result(timeout=10))
        row, _ = self.queue.table(self.repository).get(key)
        self.assertTrue(row["coding_started"])
        self.assertFalse(row.get("dependency_hold"))
        self.assertFalse(self.runs.get(key)[0].get("dependency_wait"))

    def test_dependency_retry_survives_crash_before_receipt_claim(self):
        dependent, prerequisite = self.issue(1), self.issue(2)
        key = self.enqueue(dependent)
        self.queue.advance(self.repository)
        self.block(dependent, prerequisite)
        with self.assertRaises(WorkerError):
            self.start(key)
        self.stop(key, "failure")
        prerequisite["state"]["type"] = "completed"
        replace_receipt = self.receipts.replace

        def crash(row, etag):
            if row["status"] == "dispatching":
                raise OSError("crash before receipt claim")
            replace_receipt(row, etag)

        with patch.object(self.receipts, "replace", side_effect=crash), self.assertRaises(OSError):
            self.queue.advance(self.repository)
        self.assertEqual(len(self.remote_runs[key]), 1)
        self.restart().reconcile()
        self.assertEqual(len(self.remote_runs[key]), 2)
        self.assertEqual(self.slot()["active_key"], key)
        self.start(key)

    def test_project_scan_admits_by_priority_before_creation_order(self):
        issues = [self.issue(n, priority=priority) for n, priority in enumerate((0, 4, 3, 2, 1), 1)]
        keys = [dispatch_key(self.registry, self.route, issue) for issue in issues]
        self.dispatcher.process({"type": "Project", "id": PROJECT})
        table = self.queue.table(self.repository)
        self.assertEqual([table.get(key)[0]["priority"] for key in keys], [0, 4, 3, 2, 1])
        self.assertEqual([table.get(key)[0]["sequence"] for key in keys], [1, 2, 3, 4, 5])
        for index in (4, 3, 2, 1, 0):
            self.assertEqual(self.slot()["active_key"], keys[index])
            self.assertEqual(self.selected, {issues[index]["id"]})
            self.assertEqual(self.queued, {issue["id"] for issue in issues[:index]})
            self.start(keys[index])
            self.finish(keys[index])
            self.stop(keys[index])
            self.queue.reconcile()
        self.assertEqual(self.slot()["active_key"], "")
        self.assertEqual(self.selected | self.queued, set())
        self.assertEqual(self.github.dispatch.call_count, 5)

    def test_equal_priority_fifo_survives_scans_retries_and_restarts(self):
        for priority in range(5):
            with self.subTest(priority=priority):
                self.setUp()
                issues = [self.issue(n, priority=priority) for n in (8, 2, 5)]
                keys = [self.enqueue(issue) for issue in issues]
                table = self.queue.table(self.repository)
                before = {key: table.get(key)[0] for key in keys}
                self.github.dispatch.side_effect = DispatchError("temporarily rejected")
                with self.assertRaises(DispatchError):
                    self.queue.advance(self.repository)
                self.now += 61
                self.github.dispatch.side_effect = self.dispatch
                for _ in range(2):
                    self.dispatcher.process({"type": "Project", "id": PROJECT})
                self.queue = self.restart()
                self.service.queue = self.dispatcher.queue = self.queue
                for key in keys:
                    self.queue.reconcile()
                    self.assertEqual(self.slot()["active_key"], key)
                    self.start(key)
                    self.finish(key)
                    self.stop(key)
                for key in keys:
                    after = table.get(key)[0]
                    self.assertEqual(after["sequence"], before[key]["sequence"])
                    self.assertEqual(after["queued_at"], before[key]["queued_at"])

    def test_equal_priority_batch_uses_creation_time_then_uuid_after_existing_entries(self):
        existing = self.issue(9, priority=2)
        self.enqueue(existing)
        issues = [self.issue(n, priority=2) for n in (3, 1, 2)]
        issues[1]["createdAt"] = issues[0]["createdAt"]
        self.dispatcher.process({"type": "Project", "id": PROJECT})
        for issue in (existing, issues[2], issues[1], issues[0]):
            key = dispatch_key(self.registry, self.route, issue)
            self.assertEqual(self.slot()["active_key"], key)
            self.start(key)
            self.finish(key)
            self.stop(key)
            self.queue.reconcile()

    def test_waiting_priority_edits_refresh_through_events_and_timer(self):
        for source in ("Issue", "Project", "timer"):
            for priority in (1, 0):
                with self.subTest(source=source, priority=priority):
                    self.setUp()
                    active = self.enqueue(self.issue(1))
                    self.queue.advance(self.repository)
                    second = self.enqueue(self.issue(2, priority=2))
                    issue = self.issue(3, priority=4 if priority == 1 else 1)
                    third = self.enqueue(issue)
                    self.queue.advance(self.repository)
                    table = self.queue.table(self.repository)
                    before = table.get(third)[0]
                    self.now += 10
                    issue["priority"] = priority
                    if source == "timer":
                        self.restart().reconcile()
                    else:
                        self.dispatcher.process({"type": source, "id": issue["id"] if source == "Issue" else PROJECT})
                    after = table.get(third)[0]
                    self.assertEqual(after["priority"], priority)
                    for field in ("sequence", "queued_at", "selection_version"):
                        self.assertEqual(after[field], before[field])
                    self.assertEqual(self.slot()["active_key"], active)
                    self.assertEqual(self.selected, {str(UUID(int=1))})
                    self.assertEqual(self.queued, {str(UUID(int=2)), issue["id"]})
                    self.assertEqual(self.github.dispatch.call_count, 1)
                    self.start(active)
                    self.finish(active)
                    self.stop(active)
                    self.restart().reconcile()
                    self.assertEqual(self.slot()["active_key"], third if priority == 1 else second)

    def test_handoff_refreshes_priorities_before_reserving_next_slot(self):
        first, second, third = [self.enqueue(self.issue(n, priority=3)) for n in (1, 2, 3)]
        self.queue.advance(self.repository)
        self.start(first)
        self.issues[str(UUID(int=3))]["priority"] = 1  # No issue webhook or project scan yet.
        self.finish(first)
        self.stop(first)
        self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], third)
        self.assertEqual(self.selected, {str(UUID(int=3))})
        self.assertEqual(self.queued, {str(UUID(int=2))})
        self.assertEqual(self.queue.table(self.repository).get(second)[0]["status"], "queued")
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_priority_edit_keeps_original_fifo_position_within_new_priority(self):
        first = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        earlier = self.issue(8, priority=4)
        second = self.enqueue(earlier)
        self.enqueue(self.issue(2, priority=2))
        earlier["priority"] = 2
        self.dispatcher.process({"type": "Issue", "id": earlier["id"]})
        self.start(first)
        self.finish(first)
        self.stop(first)
        self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], second)
        self.assertEqual(self.selected, {earlier["id"]})
        self.assertEqual(self.queued, {str(UUID(int=2))})

    def test_urgent_arrival_cannot_preempt_selected_or_running_task(self):
        for phase in ("selected", "running"):
            with self.subTest(phase=phase):
                self.setUp()
                first = self.enqueue(self.issue(1, priority=4))
                self.queue.advance(self.repository)
                if phase == "running":
                    self.start(first)
                self.enqueue(self.issue(2, priority=3))
                urgent = self.issue(3, priority=1)
                key = dispatch_key(self.registry, self.route, urgent)
                self.dispatcher.process({"type": "Issue", "id": urgent["id"]})
                self.issues[str(UUID(int=1))]["priority"] = 0
                self.dispatcher.process({"type": "Issue", "id": str(UUID(int=1))})
                self.assertEqual(self.slot()["active_key"], first)
                self.assertEqual(self.selected, {str(UUID(int=1))})
                self.assertEqual(self.queued, {str(UUID(int=2)), urgent["id"]})
                self.assertEqual(self.github.dispatch.call_count, 1)
                if phase == "selected":
                    self.start(first)
                self.finish(first)
                self.queue.reconcile()
                self.assertEqual(self.slot()["active_key"], first)
                self.stop(first)
                self.queue.reconcile()
                self.assertEqual(self.slot()["active_key"], key)
                self.assertEqual(self.selected, {urgent["id"]})
                self.assertEqual(self.queued, {str(UUID(int=2))})
                self.assertEqual(self.github.dispatch.call_count, 2)

    def test_legacy_waiting_priorities_are_backfilled_before_admission(self):
        first = self.enqueue(self.issue(1, priority=4))
        second = self.enqueue(self.issue(2, priority=1))
        table = self.queue.table(self.repository)
        for key in (first, second):
            row, version = table.get(key)
            del row["priority"]
            table.replace(row, version)
        self.restart().reconcile()
        self.assertEqual(self.slot()["active_key"], second)
        self.assertEqual(table.get(first)[0]["priority"], 4)
        self.assertEqual(table.get(second)[0]["priority"], 1)
        self.assertEqual([table.get(key)[0]["sequence"] for key in (first, second)], [1, 2])

    def test_priority_edit_racing_admission_retries_the_choice(self):
        for source in ("enqueue", "refresh"):
            with self.subTest(source=source):
                self.setUp()
                first = self.enqueue(self.issue(1, priority=3))
                urgent = self.issue(2, priority=4)
                second = self.enqueue(urgent)
                table = self.queue.table(self.repository)
                head = self.queue.head
                edited = False

                def edit_during_selection(table):
                    nonlocal edited
                    candidate = head(table)
                    if not edited:
                        edited = True
                        urgent["priority"] = 1
                        if source == "enqueue":
                            self.enqueue(urgent)
                        else:
                            self.queue.refresh_waiting_entry(table, second)
                    return candidate

                with patch.object(self.queue, "head", side_effect=edit_during_selection):
                    self.queue.advance(self.repository)
                self.assertTrue(edited)
                self.assertEqual(self.slot()["active_key"], second)
                self.assertEqual(self.selected, {urgent["id"]})
                self.assertEqual(self.queued, {str(UUID(int=1))})
                self.assertEqual([table.get(key)[0]["sequence"] for key in (first, second)], [1, 2])
                self.assertEqual(self.github.dispatch.call_count, 1)

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
        # A crash without a finish callback receives one repair after GitHub stops.
        self.stop(first, "failure")
        self.attempts["100:1"].update(status="completed", conclusion="failure")
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "repairing")
        self.assertNotEqual(self.slot()["active_key"], first)
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_manual_rerun_resolves_same_failed_task_and_cannot_overlap_next(self):
        first, second = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        self.start(first)
        self.finish(first, "failed")
        self.stop(first, "failure")
        # Legacy/exhausted automatic budget still permits human recovery of the
        # original task when no generated repair owns its slot.
        receipt, version = self.receipts.get(first)
        receipt["repair_attempted"] = True
        self.receipts.replace(receipt, version)
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
        self.assertEqual(self.queue.advance(self.repository)[first]["status"], "repairing")
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_existing_receipts_are_imported_before_new_todo_tasks(self):
        old = self.issue(1)
        key = "f" * 64
        self.receipts.create({"RowKey": key, "issue_id": old["id"], "identifier": old["identifier"],
                              "project_id": PROJECT, "repository": self.repository, "status": "uncertain"})
        self.enqueue(self.issue(2, priority=1))
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
        first, second = [self.enqueue(self.issue(n, priority=2)) for n in (1, 2)]
        self.queue.advance(self.repository)
        table = self.queue.table(self.repository)
        position = table.get(second)[0]["sequence"]
        issue_id = str(UUID(int=2))
        self.issues[issue_id]["priority"] = 1

        def unavailable(key):
            if key == issue_id:
                raise RequestError()
            return copy.deepcopy(self.issues.get(key))

        self.linear.issue.side_effect = unavailable
        with self.assertLogs(level="ERROR"):
            self.restart().reconcile()
        self.assertEqual(table.get(second)[0]["sequence"], position)
        self.assertEqual(table.get(second)[0]["status"], "queued")
        self.assertEqual(table.get(second)[0]["priority"], 2)
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

    def test_delayed_priority_observation_cannot_overwrite_newer_edit(self):
        first = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        issue = self.issue(2, priority=4)
        second = self.enqueue(issue)
        table = self.queue.table(self.repository)
        sequence = table.get(second)[0]["sequence"]
        issue["priority"] = 2
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

        self.linear.issue.side_effect = observe
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.queue.refresh_waiting_entry, table, second)
            try:
                self.assertTrue(started.wait(5))
                issue["priority"] = 1
                self.dispatcher.process({"type": "Issue", "id": issue["id"]})
            finally:
                resume.set()
            future.result(timeout=5)
        self.assertEqual(table.get(second)[0]["priority"], 1)
        self.assertEqual(table.get(second)[0]["sequence"], sequence)
        self.assertEqual(self.slot()["active_key"], first)
        self.assertEqual(self.selected, {str(UUID(int=1))})
        self.assertEqual(self.queued, {issue["id"]})
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
                        # The newer removal must retain the older in-flight writer.
                        self.assertTrue(table.get(first)[0]["selection_pending"])
                        self.assertTrue(table.get(first)[0]["selection_inflight"])
                    finally:
                        resume.set()
                    if isinstance(error, Exception):
                        future.result(timeout=5)
                        log.assert_called_once()
                        self.assertTrue(table.get(first)[0]["selection_pending"])
                    else:
                        with self.assertRaises(SystemExit):
                            future.result(timeout=5)
                        self.assertTrue(table.get(first)[0]["selection_pending"])
                self.assertEqual(self.selected, {str(UUID(int=1))})
                self.linear.set_selected.side_effect = self.select
                self.restart().reconcile()
                self.assertEqual(self.selected, set())
                self.assertEqual(self.github.dispatch.call_count, 1)

    def test_lost_selection_writer_expires_without_refreshing_settled_history(self):
        key = self.enqueue(self.issue(1))
        self.queue.advance(self.repository)
        self.start(key)
        self.finish(key)
        self.stop(key)
        self.queue.advance(self.repository)
        table = self.queue.table(self.repository)

        def interrupted(issue_id, enabled):
            row, _ = table.get(key)
            self.assertTrue(row["selection_pending"])
            self.assertTrue(row["selection_inflight"])
            self.select(issue_id, enabled)
            raise SystemExit()

        # No cleanup can run when a process disappears after the external write.
        self.linear.set_selected.side_effect = interrupted
        with patch.object(self.queue.selection, "defer", side_effect=WorkerError("process lost")), \
             self.assertRaises(SystemExit):
            self.queue.selection.sync(table, key, refresh=True)
        self.assertTrue(table.get(key)[0]["selection_pending"])
        self.assertTrue(table.get(key)[0]["selection_inflight"])
        self.now += WRITE_SECONDS + 1
        self.linear.set_selected.side_effect = self.select
        self.restart().reconcile()
        self.assertFalse(table.get(key)[0]["selection_pending"])
        self.assertNotIn("selection_inflight", table.get(key)[0])
        self.linear.reset_mock()
        self.github.reset_mock()
        for _ in range(3):
            self.restart().reconcile()
        self.assertEqual(self.linear.mock_calls, [])
        self.assertEqual(self.github.mock_calls, [])

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

    def prepare_repair(self, failure="worker"):
        parent, following = [self.enqueue(self.issue(n)) for n in (1, 2)]
        self.queue.advance(self.repository)
        if failure != "missing_callback":
            self.start(parent)
            if failure == "checks":
                pull = self.pulls[parent] = pr()
                checks(pull)[0].update(conclusion="FAILURE", detailsUrl=f"https://github.com/{self.repository}/actions/runs/500/job/600")
                self.finish(parent, pull=pull["url"])
            else:
                self.finish(parent, "failed")
        self.stop(parent, "success" if failure == "checks" else "failure")
        return parent, following

    def repair_row(self, parent):
        row = self.queue.table(self.repository).get(parent)[0]
        return self.queue.table(self.repository).get(row["repair_key"])[0]

    def repair_reports(self):
        return [call for call in self.linear.status_comment.call_args_list if "Auto-Repair Report" in call.args[2]]

    def test_worker_and_required_check_failures_dispatch_one_high_priority_repair(self):
        for failure in ("worker", "checks", "missing_callback"):
            with self.subTest(failure=failure):
                self.setUp()
                parent, following = self.prepare_repair(failure)
                result = self.queue.advance(self.repository)
                self.assertEqual(result[parent]["status"], "repairing")
                child = self.repair_row(parent)
                self.assertEqual(self.slot()["active_key"], child["RowKey"])
                self.assertEqual(result[following]["status"], "queued")
                self.assertTrue(self.receipts.get(parent)[0]["repair_attempted"])
                self.assertTrue(self.receipts.get(child["RowKey"])[0]["is_auto_repair"])
                fields = self.linear.create_issue.call_args.args[0]
                self.assertEqual(fields["priority"], 2)
                self.assertEqual(fields["projectId"], PROJECT)
                self.assertEqual(fields["stateId"], self.route.ready_state_ids[0])
                self.assertEqual(fields["parentId"], str(UUID(int=1)))
                self.assertEqual(fields["labelIds"], ["repair-label-id"])
                self.assertIn("Auto-repair: KIAN-1", fields["title"])
                self.assertIn(worker_branch("KIAN-1", parent), fields["description"])
                self.assertIn("actions/runs/100/attempts/1", fields["description"])
                if failure == "checks":
                    self.assertIn("actions/runs/500/job/600", fields["description"])
                    self.assertIn(self.pulls[parent]["url"], fields["description"])
                for _ in range(4):
                    self.restart().reconcile()
                self.linear.create_issue.assert_called_once()
                self.assertEqual(self.github.dispatch.call_count, 2)

    def test_repair_bypasses_clarification_but_retains_broker_boundaries(self):
        parent, _ = self.prepare_repair()
        self.queue.readiness.ensure = Mock(return_value={"status": "needs_clarification"})
        self.queue.readiness.require = Mock(side_effect=WorkerError("needs clarification"))
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        config = self.start(child["RowKey"])
        self.assertEqual(config["repair"]["target_dispatch_key"], parent)
        self.assertEqual(config["repair"]["target_branch"], worker_branch("KIAN-1", parent))
        self.queue.readiness.ensure.assert_not_called()
        self.queue.readiness.require.assert_not_called()
        self.service.handle("checkout", self.identity(child["RowKey"]), {"dispatch_key": child["RowKey"]})
        self.service.handle("progress", self.identity(child["RowKey"]),
                            {"dispatch_key": child["RowKey"], "stage": "coding"})
        self.issues[child["issue_id"]]["project"]["labels"]["nodes"] = []
        with self.assertRaisesRegex(WorkerError, "eligible"):
            self.service.handle("github", self.identity(child["RowKey"]), {"dispatch_key": child["RowKey"]})

    def test_successful_repair_merges_both_issues_and_dispatches_next_in_same_pass(self):
        parent, following = self.prepare_repair("checks")
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        key = child["RowKey"]
        self.start(key)
        self.service.handle("progress", self.identity(key), {
            "dispatch_key": key, "stage": "publishing", "repair_validation_passed": True})
        self.finish(key, pull=self.pulls[parent]["url"])
        self.stop(key)
        checks(self.pulls[parent])[0].update(status="IN_PROGRESS", conclusion=None)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], key)
        self.assertFalse(self.repair_reports())
        checks(self.pulls[parent])[0].update(status="COMPLETED", conclusion="SUCCESS")
        self.pulls[parent].update(merged=True, state="MERGED")
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], following)
        self.assertEqual(self.github.dispatch.call_count, 3)
        for issue in (str(UUID(int=1)), child["issue_id"]):
            self.assertEqual(self.issues[issue]["state"]["id"], self.route.linear_states.done)
        self.assertEqual(self.queue.table(self.repository).get(parent)[0]["status"], "completed")
        reports = self.repair_reports()
        self.assertEqual(len(reports), 2)
        self.assertEqual({c.args[1] for c in reports}, {comment_id(parent, "auto-repair-report"), comment_id(key, "auto-repair-report")})
        for report in reports:
            self.assertTrue(report.args[2].startswith(self.registry.notify_user_url))
            for expected in ("Triggered by:", "Repair issue & run:", "Outcome summary:", "Queue status:",
                             "Fixed & Merged", "All configured validation commands passed", "unblocked"):
                self.assertIn(expected, report.args[2])
        for _ in range(3):
            self.restart().reconcile()
        self.assertEqual(len(self.repair_reports()), 2)
        self.linear.create_issue.assert_called_once()

    def test_repair_without_repository_changes_releases_slot(self):
        parent, following = self.prepare_repair()
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        self.start(child["RowKey"])
        self.finish(child["RowKey"])
        self.stop(child["RowKey"])
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], following)
        self.assertEqual(self.issues[child["issue_id"]]["state"]["id"], self.route.linear_states.done)

    def test_successful_repair_immediately_admits_work_depending_on_parent_completion(self):
        parent, following = self.prepare_repair()
        dependent, prerequisite = self.issues[str(UUID(int=2))], self.issues[str(UUID(int=1))]
        self.block(dependent, prerequisite)
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        self.start(child["RowKey"])
        self.finish(child["RowKey"])
        self.stop(child["RowKey"])
        result = self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], following)
        self.assertEqual(result[following]["status"], "dispatched")
        self.assertEqual(prerequisite["state"]["type"], "completed")
        self.assertEqual(self.github.dispatch.call_count, 3)

    def test_repair_quota_wait_retains_budget_and_retries_before_coding(self):
        parent, following = self.prepare_repair()
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        key = child["RowKey"]
        self.start(key)
        self.finish(key, "waiting_for_quota")
        self.stop(key)
        self.assertEqual(self.queue.advance(self.repository)[key]["status"], "waiting")
        self.assertEqual(self.issues[child["issue_id"]]["state"]["id"], self.route.ready_state_ids[0])
        self.now += 901
        self.queue.advance(self.repository)
        self.assertEqual(self.github.dispatch.call_count, 3)
        self.start(key)
        self.finish(key)
        self.stop(key)
        self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], following)
        self.linear.create_issue.assert_called_once()

    def test_repair_dependency_or_eligibility_hold_cannot_release_broken_parent_slot(self):
        for hold in ("dependency", "cancellation", "mapping"):
            with self.subTest(hold=hold):
                self.setUp()
                parent, following = self.prepare_repair()
                dispatch = self.queue.dispatch
                changed = False
                def change_before_dispatch(table, state, etag, row, route, **kwargs):
                    nonlocal changed
                    if row.get("repair_parent_key") and not changed:
                        changed = True
                        if hold == "dependency":
                            self.block(self.issues[row["issue_id"]], self.issues[str(UUID(int=2))])
                        elif hold == "cancellation":
                            self.issues[row["issue_id"]]["state"] = {"id": "canceled", "type": "canceled"}
                        else:
                            self.registry.routes.clear()
                    return dispatch(table, state, etag, row, route, **kwargs)
                with patch.object(self.queue, "dispatch", side_effect=change_before_dispatch):
                    self.queue.advance(self.repository)
                child = self.repair_row(parent)
                for _ in range(2):
                    self.restart().reconcile()
                self.assertEqual(self.slot()["active_key"], child["RowKey"])
                self.assertNotEqual(self.slot()["active_key"], following)
                self.assertEqual(self.github.dispatch.call_count, 1)

    def test_simultaneous_failure_observers_create_one_issue_and_dispatch_one_repair(self):
        parent, _ = self.prepare_repair()
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: self.queue.advance(self.repository), range(12)))
        self.assertEqual(len([i for i in self.issues.values() if i["title"].startswith("Auto-repair:")]), 1)
        self.assertEqual(self.github.dispatch.call_count, 2)
        self.assertEqual(self.slot()["active_key"], self.repair_row(parent)["RowKey"])

    def test_repair_waits_for_prior_lifecycle_writer_after_fencing_receipt(self):
        parent, _ = self.prepare_repair()
        notes = self.queue.repair.lifecycle_notes = MemoryTable()
        notes.create({"RowKey": parent, "lease_until": self.now + 120})
        self.queue.advance(self.repository)
        self.assertTrue(self.receipts.get(parent)[0]["repair_attempted"])
        self.linear.create_issue.assert_not_called()
        self.assertEqual(self.slot()["active_key"], parent)
        self.now += 121
        self.queue.advance(self.repository)
        self.linear.create_issue.assert_called_once()
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_late_or_lost_progress_writer_cannot_undo_terminal_repair_report(self):
        for lost_process in (False, True):
            with self.subTest(lost_process=lost_process):
                self.setUp()
                parent, following = self.prepare_repair()
                self.queue.advance(self.repository)
                child = self.repair_row(parent)
                key = child["RowKey"]
                self.start(key)
                self.service.handle("progress", self.identity(key), {"dispatch_key": key, "stage": "coding"})
                stages = {}
                def stage(issue_id, value):
                    if value == "coding":
                        self.finish(key)
                        self.stop(key)
                        self.queue.advance(self.repository)
                        # The older external write arrives after Done was reported.
                        self.set_state(issue_id, self.route.linear_states.in_progress)
                        stages[issue_id] = value
                        if lost_process:
                            raise SystemExit("reporter process lost")
                    else:
                        stages[issue_id] = value
                self.linear.set_stage.side_effect = stage
                snapshot = self.queue.table(self.repository).get(parent)[0]
                if lost_process:
                    with self.assertRaises(SystemExit):
                        self.queue.repair.progress(self.queue.table(self.repository), snapshot)
                else:
                    self.queue.repair.progress(self.queue.table(self.repository), snapshot)
                self.assertTrue(self.queue.table(self.repository).get(parent)[0]["repair_report_pending"])
                self.now += WRITE_SECONDS + 1
                self.restart().reconcile()
                self.assertEqual(self.slot()["active_key"], following)
                self.assertEqual(self.issues[child["issue_id"]]["state"]["id"], self.route.linear_states.done)
                self.assertIsNone(stages[child["issue_id"]])
                self.assertFalse(self.queue.table(self.repository).get(parent)[0]["repair_report_pending"])

    def test_generated_repair_content_edits_cannot_bypass_readiness_authorization(self):
        parent, _ = self.prepare_repair()
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        self.issues[child["issue_id"]]["description"] = "A different task requiring new decisions"
        with self.assertRaisesRegex(WorkerError, "fenced parent"):
            self.start(child["RowKey"])
        self.assertFalse(self.runs.get(parent)[0]["status"] == "running")
        self.assertEqual(self.github.dispatch.call_count, 2)

    def test_failed_repair_worker_or_checks_never_cascade_or_retry(self):
        for failure in ("worker", "checks", "missing_callback"):
            with self.subTest(failure=failure):
                self.setUp()
                parent, following = self.prepare_repair("checks")
                self.queue.advance(self.repository)
                child = self.repair_row(parent)
                key = child["RowKey"]
                if failure != "missing_callback":
                    self.start(key)
                    self.finish(key, "failed" if failure == "worker" else "completed", self.pulls[parent]["url"])
                self.stop(key, "failure" if failure != "checks" else "success")
                self.queue.advance(self.repository)
                for _ in range(4):
                    self.restart().reconcile()
                self.assertEqual(self.slot()["active_key"], key)
                self.assertEqual(self.queue.table(self.repository).get(following)[0]["status"], "queued")
                self.assertEqual(self.github.dispatch.call_count, 2)
                self.linear.create_issue.assert_called_once()
                self.assertEqual(len(self.repair_reports()), 2)
                for issue in (str(UUID(int=1)), child["issue_id"]):
                    self.assertEqual(self.issues[issue]["state"]["id"], self.route.linear_states.blocked)
                for report in self.repair_reports():
                    self.assertTrue(report.args[2].startswith(self.registry.notify_user_url))
                    self.assertIn("Repair Failed", report.args[2])
                    self.assertIn("awaiting human review", report.args[2])
                if failure == "worker":
                    self.rerun(key)
                    with self.assertRaisesRegex(WorkerError, "cannot be retried"):
                        self.start(key)
                    with self.assertRaises(WorkerError):
                        self.service.handle("accounts", self.identity(key), {"dispatch_key": key})

    def test_label_or_persisted_metadata_alone_prevents_repair_cascade(self):
        for guard in ("label", "receipt", "row"):
            with self.subTest(guard=guard):
                self.setUp()
                parent, _ = self.prepare_repair()
                if guard == "label":
                    self.issues[str(UUID(int=1))]["labels"]["nodes"].append({"name": AUTO_REPAIR_LABEL})
                else:
                    table = self.receipts if guard == "receipt" else self.queue.table(self.repository)
                    row, version = table.get(parent)
                    row["is_auto_repair"] = True
                    table.replace(row, version)
                self.queue.advance(self.repository)
                self.assertEqual(self.slot()["active_key"], parent)
                self.linear.create_issue.assert_not_called()
                self.assertEqual(self.github.dispatch.call_count, 1)

    def test_repair_intent_and_creation_recover_after_interrupted_writes(self):
        for interruption in ("before_create", "after_create", "before_handoff", "after_handoff"):
            with self.subTest(interruption=interruption):
                self.setUp()
                parent, _ = self.prepare_repair()
                original_create = self.create_repair
                def create(fields):
                    persisted = self.queue.table(self.repository).get(parent)[0]
                    self.assertTrue(persisted["repair_attempted"])
                    self.assertTrue(self.receipts.get(parent)[0]["repair_attempted"])
                    if interruption == "before_create":
                        raise RuntimeError("interrupted before create")
                    result = original_create(fields)
                    if interruption == "after_create":
                        raise RuntimeError("creation response lost")
                    return result
                self.linear.create_issue.side_effect = create
                table = self.queue.table(self.repository)
                transaction = table.transaction
                def handoff(changes):
                    is_handoff = any(row.get("is_auto_repair") and row.get("status") == "active" for row, _ in changes)
                    if is_handoff and interruption == "before_handoff":
                        raise RuntimeError("interrupted before handoff")
                    transaction(changes)
                    if is_handoff and interruption == "after_handoff":
                        raise RuntimeError("handoff response lost")
                with patch.object(table, "transaction", side_effect=handoff), self.assertRaises(RuntimeError):
                    self.queue.advance(self.repository)
                self.linear.create_issue.side_effect = original_create
                self.queue = self.restart()
                self.queue.reconcile()
                self.queue.reconcile()
                self.assertEqual(len([i for i in self.issues.values() if i["title"].startswith("Auto-repair:")]), 1)
                self.assertEqual(self.github.dispatch.call_count, 2)
                self.assertEqual(self.slot()["active_key"], self.repair_row(parent)["RowKey"])

    def test_lost_report_response_and_failed_state_write_recover_without_duplicate_comments(self):
        parent, following = self.prepare_repair()
        self.queue.advance(self.repository)
        child = self.repair_row(parent)
        self.start(child["RowKey"])
        self.finish(child["RowKey"])
        self.stop(child["RowKey"])
        api = CommentAPI()
        client = Linear("test", api.request)
        lost = False
        def report(issue_id, identifier, body):
            nonlocal lost
            client.status_comment(issue_id, identifier, body)
            if identifier == comment_id(parent, "auto-repair-report") and not lost:
                lost = True
                raise RequestError()
        self.linear.status_comment.side_effect = report
        original_state = self.set_state
        state_failed = False
        def state(issue_id, state_id):
            nonlocal state_failed
            if issue_id == child["issue_id"] and not state_failed:
                state_failed = True
                raise RequestError()
            original_state(issue_id, state_id)
        self.linear.set_state.side_effect = state
        with self.assertLogs(level="ERROR"):
            self.queue.advance(self.repository)
        self.assertEqual(self.slot()["active_key"], following)
        self.assertTrue(self.queue.table(self.repository).get(parent)[0]["repair_report_pending"])
        self.restart().reconcile()
        self.assertFalse(self.queue.table(self.repository).get(parent)[0]["repair_report_pending"])
        for key in (parent, child["RowKey"]):
            self.assertEqual(api.writes.count(("create", comment_id(key, "auto-repair-report"))), 1)
        self.assertEqual(self.issues[child["issue_id"]]["state"]["id"], self.route.linear_states.done)

    def test_failure_context_redacts_credentials_and_rejects_foreign_check_links(self):
        parent, _ = self.prepare_repair("checks")
        checks(self.pulls[parent])[0].update(name="test token=private-value ghp_supersecret", detailsUrl="https://evil.example/?token=private-value")
        self.queue.advance(self.repository)
        fields = self.linear.create_issue.call_args.args[0]
        self.assertNotIn("private-value", fields["description"])
        self.assertNotIn("ghp_supersecret", fields["description"])
        self.assertNotIn("evil.example", fields["description"])

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
