"""Linear-to-live-agent steering, with durable storage and no cloud/model calls."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]

from agent_session import run_agent
from codex_runtime import AppServer, CodexError, CodexRejected
from dispatch_clients import GitHub, Linear, RequestError
from dispatch_core import Dispatcher, Registry, webhook_event
from readiness_policy import PROTOCOL
from pending_writes import WRITE_SECONDS
from steering import DELIVERY_SECONDS, Steering
from test_linear_dispatch import SECRET, signed
from test_remote_worker import ISSUE, KEY, NOW, MemoryTable, claims, receipt
from call_budget import CallBudget, record_api_calls
from worker_core import Accounts, WorkerError, WorkerService
import worker

OWNER, TRUSTED, STRANGER = (str(uuid4()) for _ in range(3))


def comment(text="@clusterfork Move the historical notes out of the setup guide.", *, author=OWNER, at=NOW + 1):
    return {"id": str(uuid4()), "body": text, "createdAt": datetime.fromtimestamp(at, timezone.utc).isoformat(),
            "user": {"id": author}, "botActor": None}


class Tracker:
    def __init__(self, registry):
        route = next(iter(registry.routes.values()))
        self.current = {"id": ISSUE, "identifier": "KIAN-895", "title": "Test", "description": "",
                        "project": {"id": route.project_id, "labels": {"nodes": [{"name": registry.enrollment_label}]}},
                        "state": {"id": route.linear_states.in_progress, "type": "started"}}
        self.comments, self.acks, self.writes = [], {}, []
        self.fail_report, self.after_report = False, None
        record_api_calls(self)

    def issue(self, issue_id):
        assert issue_id == self.current["id"]
        return copy.deepcopy(self.current)

    def viewer_id(self):
        return OWNER

    def issue_comments(self, issue_id):
        assert issue_id == self.current["id"]
        return copy.deepcopy(self.comments)

    def comment(self, comment_id):
        return {"issue": {"id": ISSUE}}

    def status_comment(self, issue_id, comment_id, body):
        assert issue_id == self.current["id"]
        self.acks[comment_id] = body
        self.writes.append((comment_id, body))
        if self.after_report:
            callback, self.after_report = self.after_report, None
            callback()
        if self.fail_report:
            raise OSError("ambiguous tracker response")


class SteeringTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.now = NOW + 10
        self.receipts, self.runs, self.table = MemoryTable(), MemoryTable(), MemoryTable()
        self.receipts.create(receipt())
        self.run = {"RowKey": KEY, "owner": "123:1", "attempt_id": str(uuid4()), "status": "running",
                    "started_at": NOW, "steering_started_at": NOW + 0.5, "lease_until": NOW + 120,
                    "stage": "coding", "thread_id": "thread-one", "turn_id": "turn-one"}
        self.runs.create(self.run)
        self.linear, self.queue = Tracker(self.registry), Mock()
        self.queue.assert_worker = Mock()
        self.queue.deployment_paused.return_value = False
        self.queue.readiness.require.return_value = {}
        self.queue.approval.return_value = {}
        self.github = Mock(spec=GitHub)
        self.service = WorkerService(self.registry, self.receipts, self.runs, Accounts(MemoryTable()),
                                     self.linear, self.github, self.queue, clock=lambda: self.now)
        self.steering = Steering(self.registry, self.receipts, self.runs, self.table,
                                 self.linear, self.queue, clock=lambda: self.now)
        self.service.steering = self.steering
        self.session = {field: self.run[field] for field in ("thread_id", "turn_id")}

    def call(self, action, **data):
        return self.service.handle(action, claims(), {"dispatch_key": KEY, **self.session, **data})

    def row(self, item):
        return self.table.get(item["id"])[0]

    def change_run(self, **updates):
        run, etag = self.runs.get(KEY)
        run.update(updates)
        self.runs.replace(run, etag)
        self.run = run

    def acknowledge(self, instruction, status="accepted"):
        return self.call("instruction_result", comment_id=instruction["comment_id"],
                         delivery=instruction["delivery"], status=status)

    def budget(self, **limits):
        return CallBudget(self, linear=self.linear, github=self.github, receipts=self.receipts,
                          runs=self.runs, instructions=self.table, **limits)

    def settled_history(self, count=12):
        current, comments = self.linear.current, self.linear.comments
        try:
            for number in range(100, 100 + count):
                issue_id, key = str(UUID(int=number)), f"{number:064x}"
                self.receipts.create({**receipt(), "RowKey": key, "issue_id": issue_id})
                self.runs.create({**self.run, "RowKey": key, "status": "completed", "steering_closed": True})
                self.linear.current = {**current, "id": issue_id,
                                       "state": {"type": "completed" if number % 2 else "canceled"}}
                item = comment(author=OWNER if number % 2 else STRANGER)
                self.linear.comments = [item]
                self.steering.observe(issue_id)
                row = self.row(item)
                self.assertEqual(row["reported_state"], row["state"])
                self.assertFalse(row["report_pending"])
        finally:
            self.linear.current, self.linear.comments = current, comments

    def test_queue_acceptance_order_and_duplicate_results_survive_restart(self):
        first, second = comment(at=NOW + 1), comment("@clusterfork Then update the references.", at=NOW + 2)
        self.linear.comments = [second, first]
        self.steering.comment(second["id"])
        first_row = self.row(first)
        self.assertIn("Queued instruction", self.linear.acks[first_row["ack_id"]])
        self.assertIn("/actions/runs/123/attempts/1", self.linear.acks[first_row["ack_id"]])
        self.assertNotIn("Agent accepted", self.linear.acks[first_row["ack_id"]])
        instruction = self.call("instructions")["instruction"]
        self.assertEqual(instruction["comment_id"], first["id"])
        self.assertIsNone(self.call("instructions")["instruction"])
        self.acknowledge(instruction)
        self.acknowledge(instruction)
        self.assertIn("Agent accepted", self.linear.acks[first_row["ack_id"]])
        # A fresh service over the same storage keeps both the receipt and order.
        self.service.steering = Steering(self.registry, self.receipts, self.runs, self.table,
                                         self.linear, self.queue, clock=lambda: self.now)
        self.assertEqual(self.call("instructions")["instruction"]["comment_id"], second["id"])
        self.assertEqual(len(self.linear.acks), 2)

    def test_concurrent_webhooks_and_polls_accept_each_comment_once(self):
        self.linear.comments = [comment(at=NOW + i) for i in range(1, 6)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.steering.comment(self.linear.comments[-1]["id"]), range(12)))
        rows = [row for row in self.table.list() if row.get("issue_id")]
        self.assertEqual(len(rows), 5)
        self.assertEqual(sorted(row["sequence"] for row in rows), [1, 2, 3, 4, 5])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.steering.poll(receipt(), self.run), range(8)))
        self.assertEqual(sum(result["instruction"] is not None for result in results), 1)

    def test_discussion_prefix_lookalikes_bot_and_owned_status_are_ignored(self):
        self.linear.comments = [comment(text) for text in (
            "Can @clusterfork handle this?", "@clusterforked change this", "> @clusterfork do it",
            "```\n@clusterfork example\n```", "    @clusterfork indented code example",
            "Clusterfork steering: Queued instruction for worker.")]
        bot = comment()
        bot["botActor"] = {"id": "integration"}
        self.linear.comments.append(bot)
        self.assertIsNone(self.call("instructions")["instruction"])
        self.assertEqual(self.linear.acks, {})

    def test_only_key_owner_and_explicitly_configured_user_ids_are_authorized(self):
        owner, trusted, stranger = comment("@Clusterfork Follow this instruction."), comment(author=TRUSTED), comment(author=STRANGER)
        self.registry = replace(self.registry, steering_user_ids=(TRUSTED,))
        self.steering.registry = self.registry
        self.linear.comments = [owner, trusted, stranger]
        self.steering.observe(ISSUE)
        self.assertEqual([self.row(item)["state"] for item in (owner, trusted, stranger)],
                         ["queued", "queued", "rejected"])
        self.assertIn("not authorized", self.linear.acks[self.row(stranger)["ack_id"]])
        self.assertEqual(self.row(stranger)["text"], "")

    def test_kian_remains_authorized_when_viewer_is_the_clusterfork_oauth_app(self):
        kian = comment(author="62a4e98c-29f0-4b6c-8062-ede9c6176c1d")
        stranger = comment(author=STRANGER, at=NOW + 2)
        self.linear.comments = [kian, stranger]
        with patch.object(self.linear, "viewer_id", return_value="a76cf048-2395-425b-ae2b-15779a1de587"):
            instruction = self.call("instructions")["instruction"]
        self.assertEqual(instruction["comment_id"], kian["id"])
        self.assertEqual(self.row(stranger)["state"], "rejected")

    def test_owner_lookup_failure_cannot_authorize_a_comment(self):
        self.linear.comments = [comment()]
        with patch.object(self.linear, "viewer_id", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                self.steering.observe(ISSUE)
        self.assertEqual(len(self.table.list()), 1)

    def test_first_observed_addressed_text_is_frozen_and_edit_does_not_resend(self):
        item = comment()
        self.linear.comments = [item]
        self.steering.observe(ISSUE)
        item["body"] = "@clusterfork Delete all the notes instead."
        instruction = self.call("instructions")["instruction"]
        self.assertEqual(instruction["text"], "Move the historical notes out of the setup guide.")
        self.acknowledge(instruction)
        self.assertIsNone(self.call("instructions")["instruction"])

    def test_empty_oversized_and_invalid_text_are_rejected(self):
        self.linear.comments = [comment(text) for text in ("@clusterfork", "@clusterfork " + "界" * 6000, "@clusterfork a\x00b")]
        self.assertIsNone(self.call("instructions")["instruction"])
        self.assertEqual({self.row(item)["state"] for item in self.linear.comments}, {"rejected"})

    def test_deleting_a_queued_comment_does_not_discard_accepted_text(self):
        item = comment()
        self.linear.comments = [item]
        self.steering.observe(ISSUE)
        self.linear.comments = []
        instruction = self.call("instructions")["instruction"]
        self.assertEqual(instruction["comment_id"], item["id"])
        self.assertEqual(instruction["text"], self.row(item)["text"])

    def test_no_active_run_or_completed_agent_explains_without_dispatching_backlog(self):
        self.change_run(status="completed", steering_closed=True)
        self.linear.current["state"] = {"id": str(uuid4()), "type": "backlog"}
        self.linear.comments = [comment()]
        dispatcher = Dispatcher(self.registry, self.linear, self.queue, steering=self.steering)
        dispatcher.process({"type": "Comment", "id": self.linear.comments[0]["id"]})
        body = next(iter(self.linear.acks.values()))
        self.assertIn("No active development agent", body)
        self.assertIn("No workflow was started", body)
        self.queue.enqueue.assert_not_called()
        self.queue.advance.assert_not_called()
        self.assertEqual(self.linear.current["state"]["type"], "backlog")

    def test_comment_predating_replacement_attempt_is_never_delivered(self):
        self.change_run(owner="456:2", attempt_id=str(uuid4()), steering_started_at=NOW + 5)
        self.linear.comments = [comment(at=NOW + 2)]
        self.steering.observe(ISSUE)
        row = self.row(self.linear.comments[0])
        self.assertEqual(row["state"], "failed")
        self.assertIn("predates", row["reason"])

    def test_comment_moved_to_another_issue_is_not_rebound(self):
        item = comment()
        self.linear.comments = [item]
        self.steering.observe(ISSUE)
        other_id, other_key = str(uuid4()), "c" * 64
        other_receipt = {**receipt(), "RowKey": other_key, "issue_id": other_id}
        other_run = {**self.run, "RowKey": other_key, "owner": "456:1", "attempt_id": str(uuid4())}
        self.receipts.create(other_receipt)
        self.runs.create(other_run)
        self.linear.current["id"] = other_id
        # Linear now presents the same comment on another actively running issue.
        self.assertIsNone(self.steering.poll(other_receipt, other_run)["instruction"])
        self.assertEqual(self.row(item)["issue_id"], ISSUE)
        self.assertEqual(self.row(item)["dispatch_key"], KEY)
        self.assertEqual(len(self.linear.acks), 1)

    def test_no_worker_record_still_receives_a_clear_acknowledgement(self):
        self.steering.runs = MemoryTable()
        self.linear.comments = [comment()]
        self.steering.observe(ISSUE)
        self.assertIn("No active development agent", next(iter(self.linear.acks.values())))

    def test_instruction_can_queue_during_initialization_and_deliver_after_session_registration(self):
        run, etag = self.runs.get(KEY)
        run.pop("thread_id")
        run.pop("turn_id")
        run["stage"] = "initializing"
        self.runs.replace(run, etag)
        self.linear.comments = [comment()]
        self.steering.observe(ISSUE)
        self.assertEqual(self.row(self.linear.comments[0])["state"], "queued")
        with self.assertRaises(WorkerError):
            self.call("instructions")
        self.call("progress", stage="coding")
        self.call("steering_open")
        self.assertIsNotNone(self.call("instructions")["instruction"])

    def test_start_retries_preserve_epoch_and_reruns_create_a_new_epoch(self):
        config = self.service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertEqual(config["attempt_id"], self.run["attempt_id"])
        self.linear.comments = [comment()]
        self.steering.observe(ISSUE)
        self.change_run(status="failed", lease_until=0)
        newer = self.service.handle("start", {**claims(), "run_attempt": "2"},
                                    {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertNotEqual(newer["attempt_id"], config["attempt_id"])
        self.assertEqual(newer["owner"], "123:2")
        self.assertEqual(self.row(self.linear.comments[0])["state"], "failed")

    def test_queued_old_attempt_and_inflight_old_attempt_do_not_cross_replacement(self):
        first, second = comment(), comment(at=NOW + 2)
        self.linear.comments = [first, second]
        instruction = self.call("instructions")["instruction"]
        self.change_run(owner="456:2", attempt_id=str(uuid4()), steering_started_at=NOW + 5)
        self.steering.repair(ISSUE)
        self.assertEqual(self.row(first)["state"], "uncertain")
        self.assertEqual(self.row(second)["state"], "failed")
        self.assertIsNone(self.steering.poll(receipt(), self.run)["instruction"])
        with self.assertRaises(WorkerError):
            self.acknowledge(instruction)

    def test_same_github_owner_with_a_different_attempt_epoch_is_fenced(self):
        self.linear.comments = [comment()]
        instruction = self.call("instructions")["instruction"]
        self.change_run(attempt_id=str(uuid4()))
        with self.assertRaises(WorkerError):
            self.acknowledge(instruction)
        self.assertIsNone(self.call("instructions")["instruction"])

    def test_canceled_moved_or_unenrolled_issue_cannot_receive_queued_instructions(self):
        for change in ("canceled", "moved", "unenrolled"):
            with self.subTest(change=change):
                self.setUp()
                item = comment()
                self.linear.comments = [item]
                self.steering.observe(ISSUE)
                if change == "canceled":
                    self.linear.current["state"]["type"] = "canceled"
                elif change == "moved":
                    self.linear.current["project"]["id"] = str(uuid4())
                else:
                    self.linear.current["project"]["labels"]["nodes"] = []
                self.assertIsNone(self.call("instructions")["instruction"])
                self.assertEqual(self.row(item)["state"], "failed")

    def test_close_or_publication_fails_queued_instructions_without_restarting(self):
        for action, data in (("steering_close", {}), ("progress", {"stage": "publishing"})):
            with self.subTest(action=action):
                self.setUp()
                item = comment()
                self.linear.comments = [item]
                self.steering.observe(ISSUE)
                self.call(action, **data)
                self.assertEqual(self.row(item)["state"], "failed")
                with self.assertRaises(WorkerError):
                    self.call("instructions")

    def test_testing_queues_instructions_for_a_correction_on_the_same_thread(self):
        item = comment()
        self.linear.comments = [item]
        self.call("progress", stage="testing")
        self.steering.observe(ISSUE)
        self.assertEqual(self.row(item)["state"], "queued")
        self.assertFalse(self.runs.get(KEY)[0].get("steering_closed"))
        with self.assertRaises(WorkerError):
            self.call("instructions")
        self.call("progress", stage="coding")
        with self.assertRaises(WorkerError):
            self.call("steering_open", thread_id="different-thread", turn_id="correction-turn")
        self.session["turn_id"] = "correction-turn"
        self.call("steering_open")
        instruction = self.call("instructions")["instruction"]
        self.assertEqual((instruction["thread_id"], instruction["turn_id"]), ("thread-one", "correction-turn"))
        self.acknowledge(instruction)
        self.assertEqual(self.row(item)["state"], "accepted")
        self.call("progress", stage="testing")
        self.call("progress", stage="publishing")
        with self.assertRaises(WorkerError):
            self.call("progress", stage="coding")

    def test_previous_turn_handoff_becomes_uncertain_without_replay_on_correction(self):
        item = comment()
        self.linear.comments = [item]
        self.call("instructions")
        self.call("progress", stage="testing")
        self.assertEqual(self.row(item)["state"], "delivering")
        self.call("progress", stage="coding")
        self.session["turn_id"] = "correction-turn"
        self.call("steering_open")
        self.assertIsNone(self.call("instructions")["instruction"])
        self.assertEqual(self.row(item)["state"], "uncertain")

    def test_lost_handoff_is_uncertain_and_never_replayed_but_late_confirmation_can_repair(self):
        item = comment()
        self.linear.comments = [item]
        instruction = self.call("instructions")["instruction"]
        self.now += DELIVERY_SECONDS + 1
        self.assertIsNone(self.call("instructions")["instruction"])
        self.assertEqual(self.row(item)["state"], "uncertain")
        self.assertIn("could not be confirmed", self.linear.acks[self.row(item)["ack_id"]])
        self.acknowledge(instruction)
        self.assertEqual(self.row(item)["state"], "accepted")

    def test_delivery_failure_and_uncertainty_do_not_claim_acceptance(self):
        for outcome in ("failed", "uncertain"):
            with self.subTest(outcome=outcome):
                self.setUp()
                item = comment()
                self.linear.comments = [item]
                self.acknowledge(self.call("instructions")["instruction"], outcome)
                body = self.linear.acks[self.row(item)["ack_id"]]
                self.assertIn(outcome, body)
                self.assertNotIn("Agent accepted", body)
                self.assertIsNone(self.call("instructions")["instruction"])

    def test_acknowledgement_outage_and_ambiguous_write_reuse_one_durable_comment(self):
        item = comment()
        self.linear.comments, self.linear.fail_report = [item], True
        with self.assertLogs(level="ERROR"):
            instruction = self.call("instructions")["instruction"]
            self.acknowledge(instruction)
        self.assertEqual(self.row(item)["reported_state"], "")
        self.linear.fail_report = False
        self.steering.reconcile()
        self.assertEqual(self.row(item)["reported_state"], "accepted")
        self.assertEqual(len(self.linear.acks), 1)
        self.assertIn("Agent accepted", next(iter(self.linear.acks.values())))

    def test_stale_acknowledgement_write_is_repaired_to_current_state(self):
        item = comment()
        self.linear.comments = [item]
        self.steering.observe(ISSUE)
        table = self.table
        def advance():
            row, etag = table.get(item["id"])
            row["state"] = "accepted"
            table.replace(row, etag)
        self.linear.after_report = advance
        self.steering.report(table, item["id"], refresh=True)
        self.assertIn("Agent accepted", next(iter(self.linear.acks.values())))

    def test_late_acknowledgement_write_retains_pending_intent_across_interruptions(self):
        for error in (None, RequestError(503), SystemExit()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.settled_history()
                acknowledgements = len(self.linear.acks) + 1
                item = comment()
                self.linear.comments = [item]
                self.steering.observe(ISSUE)
                started, resume = threading.Event(), threading.Event()
                write = self.linear.status_comment
                delayed = False

                def update(issue_id, ack_id, body):
                    nonlocal delayed
                    row = self.row(item)
                    self.assertTrue(row["report_pending"])
                    self.assertTrue(row["report_inflight"])
                    if not delayed:
                        delayed = True
                        started.set()
                        self.assertTrue(resume.wait(5))
                        write(issue_id, ack_id, body)
                        if error is not None:
                            raise error
                    else:
                        write(issue_id, ack_id, body)

                with patch.object(self.linear, "status_comment", side_effect=update), \
                     patch("steering.logging.error"), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.steering.report, self.table, item["id"], refresh=True)
                    try:
                        self.assertTrue(started.wait(5))
                        instruction = self.call("instructions")["instruction"]
                        self.acknowledge(instruction)
                        row = self.row(item)
                        self.assertEqual(row["reported_state"], "accepted")
                        self.assertTrue(row["report_pending"])
                        self.assertTrue(row["report_inflight"])
                    finally:
                        resume.set()
                    if isinstance(error, SystemExit):
                        with self.assertRaises(SystemExit):
                            future.result(timeout=5)
                    else:
                        future.result(timeout=5)
                self.change_run(status="completed", steering_closed=True)
                repair = ({"linear.issue": 1, "linear.viewer_id": 1, "linear.issue_comments": 1,
                           "linear.status_comment": 1, "instructions.create": 1, "instructions.replace": 4}
                          if error is not None else {})
                with self.budget(exact=repair) as budget:
                    self.steering.reconcile()
                self.assertEqual(budget.mutated_rows("instructions"),
                                 {item["id"], "control:" + ISSUE} if error is not None else set())
                row = self.row(item)
                self.assertEqual(row["reported_state"], "accepted")
                self.assertFalse(row["report_pending"])
                self.assertNotIn("report_inflight", row)
                self.assertIn("Agent accepted", self.linear.acks[row["ack_id"]])
                self.assertEqual(len(self.linear.acks), acknowledgements)
                self.linear.status_comment.assert_called_with(ISSUE, row["ack_id"], self.linear.acks[row["ack_id"]])
                for _ in range(3):
                    with self.budget():
                        self.steering.reconcile()

    def test_lost_acknowledgement_writer_expires_and_repairs_without_replaying_instruction(self):
        self.settled_history()
        acknowledgements = len(self.linear.acks) + 1
        item = comment()
        self.linear.comments = [item]
        self.acknowledge(self.call("instructions")["instruction"])
        self.change_run(status="completed", steering_closed=True)

        write = self.linear.status_comment
        def interrupted(*args):
            self.assertTrue(self.row(item)["report_pending"])
            self.assertTrue(self.row(item)["report_inflight"])
            write(*args)
            raise SystemExit()

        with patch.object(self.linear, "status_comment", side_effect=interrupted), \
             patch.object(self.steering, "defer_report", side_effect=WorkerError("process lost")), \
             self.assertRaises(SystemExit):
            self.steering.report(self.table, item["id"], refresh=True)
        self.assertTrue(self.row(item)["report_pending"])
        self.assertTrue(self.row(item)["report_inflight"])
        self.now += WRITE_SECONDS + 1
        with self.budget(exact={"linear.issue": 1, "linear.viewer_id": 1,
                                "linear.issue_comments": 1, "linear.status_comment": 1,
                                "instructions.create": 1, "instructions.replace": 4}) as budget:
            self.steering.reconcile()
        self.assertEqual(budget.mutated_rows("instructions"), {item["id"], "control:" + ISSUE})
        row = self.row(item)
        self.assertEqual(row["state"], "accepted")
        self.assertEqual(row["reported_state"], "accepted")
        self.assertFalse(row["report_pending"])
        self.assertNotIn("report_inflight", row)
        self.assertEqual(len(self.linear.acks), acknowledgements)
        self.linear.status_comment.assert_called_with(ISSUE, row["ack_id"], self.linear.acks[row["ack_id"]])
        for _ in range(3):
            with self.budget():
                self.steering.reconcile()

    def test_acknowledgement_repair_continues_when_issue_observation_fails(self):
        item = comment()
        self.linear.comments = [item]
        self.acknowledge(self.call("instructions")["instruction"])
        row, etag = self.table.get(item["id"])
        row["reported_state"] = "delivering"
        self.table.replace(row, etag)
        self.change_run(status="completed", steering_closed=True)
        with patch.object(self.linear, "issue", side_effect=RequestError(503)), self.assertLogs(level="ERROR"):
            self.steering.reconcile()
        self.assertEqual(self.row(item)["reported_state"], "accepted")

    def test_oidc_owner_issue_delivery_token_and_session_are_all_fenced(self):
        self.linear.comments = [comment()]
        instruction = self.call("instructions")["instruction"]
        bad_claims = ({**claims(), "run_attempt": "2"}, {**claims(), "repository": "other/repository"})
        for identity in bad_claims:
            with self.assertRaises(WorkerError):
                self.service.handle("instructions", identity, {"dispatch_key": KEY, **self.session})
        for action, data in (("steering_open", {"thread_id": "replacement"}),
                             ("instructions", {"turn_id": "different-turn"}),
                             ("instruction_result", {"comment_id": instruction["comment_id"],
                                                      "delivery": "wrong", "status": "accepted"}),
                             ("instruction_result", {"comment_id": str(uuid4()),
                                                      "delivery": instruction["delivery"], "status": "accepted"})):
            with self.subTest(action=action, data=data), self.assertRaises(WorkerError):
                self.call(action, **data)
        self.assertEqual(self.row(self.linear.comments[0])["state"], "delivering")

    def test_expired_worker_lease_is_not_evidence_of_delivery(self):
        self.linear.comments = [comment(), comment(at=NOW + 2)]
        self.call("instructions")
        self.change_run(lease_until=self.now - 1)
        self.steering.repair(ISSUE)
        self.assertEqual([self.row(item)["state"] for item in self.linear.comments], ["uncertain", "failed"])


class LiveSessionTests(unittest.TestCase):
    setUp = SteeringTests.setUp
    row = SteeringTests.row
    call = SteeringTests.call

    def run_session(self, server, directory, *, broker_failure=None, open_failure=0, open_exception=None):
        service = self.service
        class Broker:
            key = KEY
            failures = 0
            open_failures = 0
            def call(inner, action, **data):
                if action == "steering_open" and inner.open_failures < open_failure:
                    inner.open_failures += 1
                    raise open_exception or OSError("broker unavailable during registration")
                if action == "instruction_result" and broker_failure and inner.failures < broker_failure:
                    inner.failures += 1
                    raise OSError("response unavailable")
                return service.handle(action, claims(), {"dispatch_key": KEY, **data})
        config = {"model": "gpt-6.1-sol", "effort": "max", "issue": {"id": ISSUE},
                  "owner": self.run["owner"], "attempt_id": self.run["attempt_id"]}
        return run_agent(Broker(), config, directory, directory, "Implement the original task", {},
                         timeout=5, server_factory=lambda *a, **kw: server)

    def test_authorized_comment_changes_same_workspace_without_starting_a_second_turn(self):
        self.linear.comments = [comment()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            server = FakeAgent(path)
            summary = self.run_session(server, path)
            self.assertEqual((path / "history.md").read_text(), "Historical investigation notes\n")
            self.assertEqual((path / "setup.md").read_text(), "Setup instructions\n")
            self.assertEqual(summary, "Moved notes and retained the setup instructions.")
            self.assertEqual(server.methods.count("turn/start"), 1)
            self.assertEqual(server.methods.count("turn/steer"), 1)
            self.assertTrue(server.closed)
        self.assertEqual(self.row(self.linear.comments[0])["state"], "accepted")
        self.assertTrue(self.runs.get(KEY)[0]["steering_closed"])

    def test_explicit_rpc_rejection_is_failed_and_transport_loss_is_uncertain(self):
        for error, expected in ((CodexRejected("rejected"), "failed"), (CodexError("timed out"), "uncertain")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                self.setUp()
                self.linear.comments = [comment()]
                server = FakeAgent(Path(directory), error=error)
                self.run_session(server, Path(directory))
                self.assertEqual(self.row(self.linear.comments[0])["state"], expected)
                self.assertEqual(server.methods.count("turn/steer"), 1)

    def test_ack_retry_does_not_repeat_agent_steering(self):
        self.linear.comments = [comment()]
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            self.run_session(server, Path(directory), broker_failure=1)
            self.assertEqual(server.methods.count("turn/steer"), 1)
            self.assertEqual(self.row(self.linear.comments[0])["state"], "accepted")

    def test_session_registration_outage_retries_in_the_same_live_turn(self):
        self.linear.comments = [comment()]
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            with patch("agent_session.POLL_SECONDS", 0):
                self.run_session(server, Path(directory), open_failure=1)
            self.assertEqual(server.methods.count("turn/start"), 1)
            self.assertEqual(server.methods.count("turn/steer"), 1)
            self.assertEqual(self.row(self.linear.comments[0])["state"], "accepted")

    def test_explicit_registration_denial_stops_the_unregistered_session(self):
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            with self.assertRaises(CodexError):
                self.run_session(server, Path(directory), open_failure=1,
                                 open_exception=HTTPError("https://broker/steering_open", 403, "denied", {}, None))
            self.assertTrue(server.closed)
            self.assertNotIn("turn/steer", server.methods)

    def test_polling_outage_does_not_discard_work_or_restart_the_agent(self):
        self.linear.comments = [comment()]
        self.steering.observe(ISSUE)
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            def unavailable(*args):
                server.complete()
                raise OSError("broker unavailable")
            with patch.object(self.steering, "poll", side_effect=unavailable):
                self.run_session(server, Path(directory))
            self.assertIn("Historical investigation notes", (Path(directory) / "setup.md").read_text())
            self.assertEqual(server.methods.count("turn/start"), 1)
            self.assertNotIn("turn/steer", server.methods)

    def test_turn_completion_during_poll_rejects_delivery_and_never_starts_another_turn(self):
        self.linear.comments = [comment()]
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            original = self.steering.poll
            def finish_during_poll(*args):
                instruction = original(*args)
                server.complete()
                return instruction
            with patch.object(self.steering, "poll", side_effect=finish_during_poll):
                self.run_session(server, Path(directory))
            self.assertNotIn("turn/steer", server.methods)
            self.assertEqual(server.methods.count("turn/start"), 1)
            self.assertEqual(self.row(self.linear.comments[0])["state"], "failed")

    def test_lease_loss_closes_live_agent_without_delivering(self):
        lost = threading.Event()
        lost.set()
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            with self.assertRaises(CodexError):
                run_agent(Mock(), {"model": "test", "effort": "max"}, directory, directory, "Test", {},
                          failure_signals=[lost], server_factory=lambda *a, **kw: server)
            self.assertTrue(server.closed)
            self.assertNotIn("turn/steer", server.methods)

    def test_validation_cannot_start_a_correction_after_lease_loss_or_deadline(self):
        for stop in ("lease", "deadline"):
            with self.subTest(stop=stop):
                lost = threading.Event()
                clock = [0]
                server = Mock()
                server.call.side_effect = [{"thread": {"id": "thread-one"}}, {"turn": {"id": "turn-one"}}]
                server.next_event.side_effect = [{"method": "turn/completed", "params": {
                    "threadId": "thread-one", "turn": {"id": "turn-one", "status": "completed"}}}, None]
                broker = Mock()
                broker.call.return_value = {"accepting": True}
                def validate(*args, **kwargs):
                    self.assertEqual(kwargs["deadline"], 10)
                    self.assertEqual(kwargs["failure_signals"], [lost])
                    if stop == "lease":
                        lost.set()
                    else:
                        clock[0] = 10
                    return "Fix validation/1."
                with patch("agent_session.time.monotonic", side_effect=lambda: clock[0]), self.assertRaises(CodexError):
                    run_agent(broker, {"model": "test", "effort": "max"}, ROOT, ROOT, "Task", {},
                              validate=validate, failure_signals=[lost], deadline=10,
                              server_factory=lambda *a, **kw: server)
                self.assertEqual([call.args[0] for call in server.call.call_args_list], ["thread/start", "turn/start"])
                server.close.assert_called_once()


class FakeAgent:
    def __init__(self, directory, error=None):
        self.directory, self.error = directory, error
        self.events, self.methods, self.closed = deque(), [], False

    def complete(self):
        self.events.append({"method": "item/completed", "params": {"threadId": "thread-one", "turnId": "turn-one",
                            "item": {"type": "agentMessage", "text": "Moved notes and retained the setup instructions."}}})
        self.events.append({"method": "turn/completed", "params": {"threadId": "thread-one",
                            "turn": {"id": "turn-one", "status": "completed"}}})

    def call(self, method, params):
        self.methods.append(method)
        if method == "thread/start":
            assert params["approvalPolicy"] == "never"
            assert params["sandbox"] == "danger-full-access"
            return {"thread": {"id": "thread-one"}}
        if method == "turn/start":
            (self.directory / "setup.md").write_text("Setup instructions\nHistorical investigation notes\n")
            return {"turn": {"id": "turn-one"}}
        if method == "turn/steer":
            assert params["threadId"] == "thread-one" and params["expectedTurnId"] == "turn-one"
            assert params["input"][0]["text"] == "Move the historical notes out of the setup guide."
            self.complete()
            if self.error:
                raise self.error
            assert (self.directory / "setup.md").read_text().startswith("Setup instructions")
            (self.directory / "history.md").write_text("Historical investigation notes\n")
            (self.directory / "setup.md").write_text("Setup instructions\n")
            return {"turnId": "turn-one"}
        raise AssertionError(method)

    def next_event(self, timeout=0):
        return self.events.popleft() if self.events else None

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_signed_comment_webhook_queues_id_and_timestamp_without_content(self):
        identifier = str(uuid4())
        body, headers = signed({"type": "Comment", "action": "update", "webhookTimestamp": NOW * 1000,
                                "data": {"id": identifier, "body": "@clusterfork private", "userId": STRANGER}})
        self.assertEqual(webhook_event(body, headers, SECRET, now=NOW),
                         {"type": "Comment", "id": identifier, "webhook_at": NOW})

    def test_registry_validates_explicit_trusted_linear_ids(self):
        data = json.loads((ROOT / "automation/azure/projects.json").read_text())
        for value in ({"trusted_user_ids": [TRUSTED]}, {"trusted_user_ids": ["user@example.com"]},
                      {"trusted_user_ids": "everyone"}, {"allow_all": True}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "projects.json"
                path.write_text(json.dumps({**data, "steering": value}))
                if value == {"trusted_user_ids": [TRUSTED]}:
                    self.assertEqual(Registry.load(path).steering_user_ids, (TRUSTED,))
                else:
                    with self.assertRaises(ValueError):
                        Registry.load(path)

    def test_linear_owner_lookup_and_all_comment_pages_are_read_in_azure(self):
        calls = []
        first, second = comment(), comment(at=NOW + 2)
        first["parent"], second["parent"] = None, {"id": first["id"]}
        def request(url, **kwargs):
            query, variables = kwargs["data"]["query"], kwargs["data"]["variables"]
            calls.append((query, variables))
            if "viewer" in query:
                return {"data": {"viewer": {"id": OWNER}}}
            after = variables["after"]
            return {"data": {"comments": {"nodes": [second if after else first],
                    "pageInfo": {"hasNextPage": not after, "endCursor": "page-two" if not after else None}}}}
        linear = Linear("fake-key", request=request)
        self.assertEqual(linear.viewer_id(), OWNER)
        self.assertEqual(linear.viewer_id(), OWNER)
        self.assertEqual(list(linear.issue_comments(ISSUE)), [first, second])
        self.assertIn("parent { id }", calls[1][0])
        self.assertIn("filter: {issue: {id: {eq: $id}}}", calls[1][0])
        self.assertEqual([call[1].get("after") for call in calls[1:]], [None, "page-two"])
        self.assertEqual(len(calls), 3)

    def test_app_server_preserves_events_received_before_rpc_response_and_strips_job_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            executable = path / "fake-codex"
            executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in os.environ
assert "LINEAR_API_KEY" not in os.environ
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "turn/steer":
        print(json.dumps({"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}}}), flush=True)
        print(json.dumps({"id": message["id"], "result": {"turnId": "turn"}}), flush=True)
    else:
        print(json.dumps({"id": message["id"], "result": {}}), flush=True)
''')
            executable.chmod(0o700)
            with patch.dict(os.environ, {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "job-secret", "LINEAR_API_KEY": "tracker-secret"}):
                server = AppServer(path, binary=str(executable), env=worker.clean_environment())
                try:
                    result = server.call("turn/steer", {"threadId": "thread", "expectedTurnId": "turn", "input": []})
                    self.assertEqual(result, {"turnId": "turn"})
                    self.assertEqual(server.next_event()["method"], "turn/completed")
                finally:
                    server.close()

