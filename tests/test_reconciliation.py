"""Completed lifecycles stay quiet across timer ticks and project scans."""

import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import call
from uuid import UUID

import test_readiness as fixtures
from dispatch_clients import Linear, RequestError
from linear_sync import LinearSync, comment_id
from repository_queue import dispatch_key
from steering import Steering
from test_linear_sync import CommentAPI, pr
from test_remote_worker import MemoryTable, PROJECT


class BoundedReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReadinessTests()
        self.case.setUp()
        c = self.case
        self.author = str(UUID(int=9_000))
        c.linear.viewer_id.return_value = self.author
        c.linear.comment.side_effect = lambda identifier: next(
            {**comment, "issue": {"id": issue_id}}
            for issue_id, comments in c.threads.items() for comment in comments if comment["id"] == identifier)
        states = c.route.linear_states
        types = {states.in_progress: "started", states.done: "completed", states.blocked: "unstarted"}
        c.linear.set_state.side_effect = lambda issue_id, state: c.issues[issue_id].update(
            state={"id": state, "type": types.get(state, "unstarted")})
        self.pulls = {}
        c.github.worker_pull.side_effect = lambda route, receipt, key, url: copy.deepcopy(self.pulls.get(key))
        self.notes, self.instructions = MemoryTable(), MemoryTable()
        self.sync = LinearSync(c.registry, c.receipts, c.runs, self.notes, c.linear, c.github, clock=lambda: c.now)
        self.steering = Steering(c.registry, c.receipts, c.runs, self.instructions,
                                 c.linear, c.queue, clock=lambda: c.now)
        c.service.sync, c.service.steering, c.dispatcher.steering = self.sync, self.steering, self.steering

    def tick(self):
        # Same services and order as the Azure timer, followed by its queued scan.
        c = self.case
        c.gate.reconcile()
        self.sync.reconcile()
        c.queue.reconcile()
        self.steering.reconcile()

    def instruction(self, issue, number):
        c = self.case
        item = {"id": str(UUID(int=1_000 + number)), "body": "@clusterfork Preserve the existing defaults.",
                "createdAt": datetime.fromtimestamp(c.now, timezone.utc).isoformat(),
                "user": {"id": self.author}, "botActor": None}
        c.threads[issue["id"]].append(item)
        return item

    def start_coding(self, issue):
        c = self.case
        review, _ = c.pass_review(issue)
        key = dispatch_key(c.registry, c.route, issue)
        c.remote_runs[key][-1]["status"] = "in_progress"
        c.call(key, "start")
        c.call(key, "progress", stage="coding", context_sha=c.sha)
        session = {"thread_id": "thread-one", "turn_id": "turn-one"}
        c.call(key, "steering_open", **session)
        return review, key, session

    def complete_history(self, count=4):
        c = self.case
        history = []
        for number in range(1, count + 1):
            issue = c.issue if number == 1 else c.make_issue(number)
            review, key, session = self.start_coding(issue)
            c.now += 1
            item = self.instruction(issue, number)
            instruction = c.call(key, "instructions", **session)["instruction"]
            self.assertEqual(instruction["comment_id"], item["id"])
            c.call(key, "instruction_result", **session, comment_id=item["id"],
                   delivery=instruction["delivery"], status="accepted")
            c.call(key, "progress", stage="testing")
            c.call(key, "progress", stage="publishing")
            self.pulls[key] = {**pr(), "url": f"https://github.com/{c.route.repository}/pull/{number}"}
            c.call(key, "finish", status="completed", pull_request=self.pulls[key]["url"])
            c.stop(key)
            self.pulls[key].update(state="MERGED", merged=True)
            c.sha = f"{number:040x}"
            self.tick()
            self.assertEqual(c.table.get(review)[0]["status"], "passed")
            self.assertTrue(c.table.get(review)[0]["reported"])
            queue_row = c.tables[c.route.repository.lower()].get(key)[0]
            self.assertEqual(queue_row["status"], "completed")
            self.assertFalse(queue_row["selection_pending"])
            self.assertNotIn("selection_inflight", queue_row)
            self.assertTrue(c.runs.get(key)[0]["steering_closed"])
            self.assertTrue(self.notes.get(key)[0]["terminal"])
            self.assertEqual(issue["state"]["type"], "completed")
            row, _ = self.instructions.get(item["id"])
            self.assertEqual(row["reported_state"], "accepted")
            self.assertFalse(row["report_pending"])
            history.append(issue["id"])
        # One settling pass allows all terminal projections to finish reporting.
        self.tick()
        c.dispatcher.process({"type": "Project", "id": PROJECT})
        return history

    def test_settled_history_makes_no_per_issue_api_calls_on_timers_or_project_scans(self):
        c = self.case
        self.complete_history()
        c.linear.reset_mock()
        c.github.reset_mock()
        queue_rows = copy.deepcopy(c.tables[c.route.repository.lower()].rows)
        instruction_rows = copy.deepcopy(self.instructions.rows)
        for _ in range(3):
            c.now += 901
            self.tick()
            c.dispatcher.process({"type": "Project", "id": PROJECT})
        self.assertEqual(c.linear.mock_calls, [call.project_issues(PROJECT)] * 3)
        self.assertEqual(c.github.mock_calls, [])
        self.assertEqual(c.tables[c.route.repository.lower()].rows, queue_rows)
        self.assertEqual(self.instructions.rows, instruction_rows)

    def test_identity_recovery_and_report_retries_do_not_readmit_completed_work(self):
        c = self.case
        _, first, _ = self.start_coding(c.issue)
        pull = self.pulls[first] = pr()
        c.call(first, "finish", status="completed", pull_request=pull["url"])
        c.stop(first)
        next_issue = c.make_issue(2)
        c.dispatcher.process({"type": "Issue", "id": next_issue["id"]})
        self.assertEqual(c.tables[c.route.repository.lower()].get("slot")[0]["active_key"], first)
        legacy_id = comment_id(first, "status")
        api = CommentAPI()
        legacy = api.seed(legacy_id, c.comments[legacy_id])
        lose_response = True

        def request(url, **kwargs):
            nonlocal lose_response
            result = api.request(url, **kwargs)
            if lose_response and "commentCreate(" in kwargs["data"]["query"]:
                lose_response = False
                raise RequestError(503)
            return result

        # Use the real comment adapter only for lifecycle reports; readiness and
        # queue feedback retain their normal fixture threads.
        original_writer = c.linear.status_comment.side_effect
        comment_client = Linear("test", request)

        def write(issue, identifier, body):
            if identifier == legacy_id:
                return comment_client.status_comment(issue, identifier, body)
            return original_writer(issue, identifier, body)

        c.linear.status_comment.side_effect = write
        receipts, runs = copy.deepcopy(c.receipts.rows), copy.deepcopy(c.runs.rows)
        pull.update(state="MERGED", merged=True)
        c.sha = "c" * 40
        with self.assertLogs(level="ERROR"):
            self.tick()
        self.assertEqual(c.issue["state"]["id"], c.route.linear_states.done)
        self.assertFalse(self.notes.get(first)[0]["terminal"])
        self.assertNotIn("clusterfork: PR review and CI", c.issue["labels"])
        self.assertEqual(api.comments[legacy_id], legacy)
        self.assertEqual(c.runs.rows, runs)
        self.assertEqual(c.receipts.rows, receipts)
        second = dispatch_key(c.registry, c.route, next_issue)
        self.assertEqual(c.tables[c.route.repository.lower()].get("slot")[0]["active_key"], second)
        # The next issue can pass review and code while terminal reporting repairs.
        c.pass_review(next_issue)
        c.call(second, "start")
        c.call(second, "progress", stage="coding", context_sha=c.sha)
        dispatches = c.github.dispatch.call_count
        for _ in range(3):
            c.now += 901
            self.tick()
            c.dispatcher.process({"type": "Project", "id": PROJECT})
        self.assertTrue(self.notes.get(first)[0]["terminal"])
        self.assertEqual(len(api.comments), 2)
        self.assertEqual(len(api.writes), 1)
        self.assertEqual(len(c.remote_runs[first]), 1)
        self.assertEqual(c.runs.get(first)[0]["status"], "completed")
        self.assertEqual(c.github.dispatch.call_count, dispatches)
        self.assertEqual(c.tables[c.route.repository.lower()].get("slot")[0]["active_key"], second)

    def test_active_worker_is_polled_without_touching_completed_history(self):
        c = self.case
        history = set(self.complete_history())
        active = c.make_issue(5)
        _, key, _ = self.start_coding(active)
        c.linear.reset_mock()
        c.github.reset_mock()
        self.tick()
        c.dispatcher.process({"type": "Project", "id": PROJECT})
        self.assertGreaterEqual(c.linear.issue_comments.call_count, 2)
        for method, args, _ in c.linear.mock_calls:
            if method != "project_issues" and args:
                self.assertNotIn(args[0], history, (method, args))
        for args in c.github.dispatch_runs.call_args_list:
            self.assertEqual(args.args[1], key)
        for args in c.github.worker_pull.call_args_list:
            self.assertEqual(args.args[2], key)
        owner = c.runs.get(key)[0]["owner"]
        for args in c.github.worker_result.call_args_list:
            self.assertEqual(args.args[1], owner)

    def test_late_comments_on_completed_issues_are_answered_only_when_webhooks_arrive(self):
        c = self.case
        history = self.complete_history()
        for number, event_type in enumerate(("Issue", "Comment"), 10):
            with self.subTest(event_type=event_type):
                issue = c.issues[history[number - 10]]
                item = self.instruction(issue, number)
                c.linear.reset_mock()
                c.github.reset_mock()
                self.tick()
                c.dispatcher.process({"type": "Project", "id": PROJECT})
                self.assertEqual(c.linear.mock_calls, [call.project_issues(PROJECT)])
                event_id = item["id"] if event_type == "Comment" else issue["id"]
                c.dispatcher.process({"type": event_type, "id": event_id})
                c.linear.issue_comments.assert_called_once_with(issue["id"])
                row, _ = self.instructions.get(item["id"])
                self.assertEqual(row["state"], "failed")
                self.assertEqual(row["reported_state"], "failed")
                self.assertIn("No active development agent", c.comments[row["ack_id"]])
                self.assertEqual(c.github.mock_calls, [])
                c.linear.reset_mock()
                self.tick()
                c.dispatcher.process({"type": "Project", "id": PROJECT})
                self.assertEqual(c.linear.mock_calls, [call.project_issues(PROJECT)])
