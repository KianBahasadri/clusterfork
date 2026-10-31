"""Real committed evidence, ambiguous delivery, bounds and isolation from workers."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.diagnostics import context
from controller import telemetry
from controller.core import Controller
from controller.model import Finish, Progress, Rejected, Retry, SessionUpdated
from controller.store import Database
import test_controller as fixtures

CONFIG = {"endpoint": "https://fixture.eastus-1.ingest.monitor.azure.com", "rule_id": "dcr-" + "a" * 32}


def deduplicate(events):
    """Fixture oracle for the workbook's arg_max(TimeGenerated, *) by EventId."""
    result = {}
    for event in sorted(events, key=lambda e: e["TimeGenerated"]):
        result[event["EventId"]] = event
    return list(result.values())


class ControllerTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ControllerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.database = self.fixture.database
        self.now = 1000
        self.sender = Mock()
        self.telemetry = telemetry.Telemetry(self.database, Mock(), CONFIG, "a" * 40,
                                             clock=lambda: self.now, send=self.sender)

    def events(self, kind=None):
        with self.database.transaction() as db:
            result = [json.loads(row[0]) for row in db.execute("SELECT payload FROM telemetry_outbox ORDER BY created_at,rowid")]
        return [e for e in result if e["EventType"] == kind] if kind else result

    def test_callbacks_reconciliation_and_restart_do_not_inflate_phase_or_stop_counts(self):
        view = self.fixture.coding(self.fixture.admit())
        # Repeat a real successful start and progress, then an idempotent finish.
        self.fixture.start(view, fixtures.Role.CODING, review_job_succeeded=True)
        self.fixture.apply(Progress(view.task_id, view.attempt["attempt_id"], "coding", fixtures.SPEC.context_sha))
        finish = Finish(view.task_id, view.attempt["attempt_id"], "coding", "failed")
        self.fixture.apply(finish)
        self.fixture.apply(finish)
        for _ in range(4):
            self.fixture.external(view, conclusion="failure", pull_state="absent")
        self.assertEqual(len(self.events("phase_started")), 2)
        self.assertEqual(len(self.events("phase_result")), 2)
        self.assertEqual(len(self.events("github_stopped")), 1)
        self.assertEqual(len(self.events("publication_observed")), 1)
        self.assertEqual(len(self.events("task_outcome")), 1)
        before = self.events()
        self.telemetry.flush()
        self.assertFalse(self.events())
        # Durable receipts survive both successful delivery and process restart.
        self.database.close()
        database = Database(self.fixture.path)
        self.database = database
        self.addCleanup(database.close)
        self.fixture.controller = Controller(database)
        restarted = telemetry.Telemetry(database, Mock(), CONFIG, "b" * 40, clock=lambda: self.now, send=self.sender)
        self.fixture.external(view, conclusion="failure", pull_state="absent")
        self.assertFalse(self.events("github_stopped"))
        self.assertEqual(self.events()[0]["StreamStartedAt"], before[0]["StreamStartedAt"])
        restarted.flush()
        deliveries = [json.loads(call.args[0]) for call in self.sender.call_args_list]
        logical = deduplicate(before + before + sum(deliveries, []))
        self.assertEqual(sum(e["EventType"] == "phase_result" for e in logical), 2)
        self.assertEqual(sum(e["EventType"] == "github_stopped" for e in logical), 1)

    def test_version_five_upgrade_preserves_evidence_without_backfilling_old_outcomes(self):
        with self.database.transaction() as db:
            db.execute("DELETE FROM metadata WHERE key LIKE 'telemetry:%'")
        view = self.fixture.coding(self.fixture.admit())
        self.database.close()
        with sqlite3.connect(self.fixture.path) as db:
            db.execute("DROP TABLE telemetry_outbox")
            db.execute("DROP TABLE telemetry_receipts")
            db.execute("PRAGMA user_version=5")
        self.database = Database(self.fixture.path)
        self.addCleanup(self.database.close)
        self.assertEqual(Controller(self.database).inspect(view.task_id), view)
        telemetry.Telemetry(self.database, Mock(), CONFIG, "b" * 40, clock=lambda: 2000, send=self.sender)
        self.assertEqual([e["EventType"] for e in self.events()], ["controller_started"])
        self.assertEqual(self.database.connection.execute("PRAGMA user_version").fetchone()[0], 9)

    def test_no_callback_or_missing_conclusion_does_not_invent_an_agent_outcome(self):
        view = self.fixture.admit()
        self.fixture.external(view, stopped=True, conclusion="", all_attempts_stopped=False, publication_checked=False)
        self.assertFalse(self.events("phase_result"))
        self.assertFalse(self.events("github_stopped"))
        self.assertFalse(self.events("task_outcome"))
        self.fixture.external(view, conclusion="failure")
        self.assertFalse(self.events("phase_result"))
        self.assertEqual(self.events("github_stopped")[0]["Outcome"], "failure")

    def test_phase_cleanup_failure_cannot_overwrite_a_committed_merged_task_outcome(self):
        view = self.fixture.coding(self.fixture.admit())
        self.fixture.apply(Finish(view.task_id, view.attempt["attempt_id"], "coding", "failed"))
        final = self.fixture.external(view, conclusion="failure", pull_state="merged",
                                      pull_request="https://github.com/Owner/Repo/pull/1")
        self.assertEqual(final.phase, "completed")
        self.assertEqual(self.events("phase_result")[-1]["Outcome"], "failed")
        self.assertEqual(self.events("github_stopped")[0]["Outcome"], "failure")
        self.assertEqual(self.events("publication_observed")[0]["Outcome"], "merged")
        self.assertTrue(self.events("task_outcome")[0]["TaskFinal"])
        self.assertEqual(self.events("task_outcome")[0]["Outcome"], "completed")

    def test_evidence_sequence_resolves_latest_task_decision_when_timestamps_tie(self):
        view = self.fixture.coding(self.fixture.admit())
        self.fixture.apply(Finish(view.task_id, view.attempt["attempt_id"], "coding", "failed"))
        self.fixture.external(view, conclusion="failure", pull_state="closed",
                              pull_request="https://github.com/Owner/Repo/pull/1")
        self.fixture.external(view, conclusion="failure", pull_state="merged",
                              pull_request="https://github.com/Owner/Repo/pull/1")
        decisions = self.events("task_outcome")
        self.assertEqual(len(decisions), 2)
        self.assertEqual(len({d["TimeGenerated"] for d in decisions}), 1)
        self.assertEqual(max(decisions, key=lambda d: d["EvidenceSequence"])["Outcome"], "completed")

    def test_a_retry_is_a_distinct_attempt_without_recounting_its_parent(self):
        view = self.fixture.admit()
        self.fixture.start(view, fixtures.Role.REVIEW_SELECT)
        self.fixture.external(view, conclusion="timed_out")
        self.fixture.now += 901
        retry = self.fixture.apply(Retry(view.task_id, fixtures.SPEC, "retry", "review"))
        start = fixtures.Start(retry.task_id, retry.attempt["attempt_id"], "456", 2, fixtures.Role.REVIEW_SELECT)
        self.fixture.apply(start)
        events = self.events("phase_preflight_started")
        self.assertEqual(len({e["EventId"] for e in events}), 2)
        self.assertEqual({e["RunId"] for e in events}, {"123", "456"})
        self.assertIn("/runs/456/attempts/2", events[-1]["ActionsUrl"])

    def test_rollback_and_rejected_commands_export_nothing(self):
        view = self.fixture.admit()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.database.transaction():
                self.fixture.start(view, fixtures.Role.REVIEW_SELECT)
                raise RuntimeError("rollback")
        self.assertFalse(self.events("phase_preflight_started"))
        with self.assertRaises(Rejected):
            self.fixture.start(view, fixtures.Role.CODING)
        self.assertFalse(self.events("phase_started"))

    def test_projection_failure_rolls_back_only_telemetry_and_worker_command_commits(self):
        view = self.fixture.admit()
        def fail_after_insert(db, value, now, **kwargs):
            db.execute("INSERT INTO telemetry_outbox VALUES ('fixture',?, '{}',0,0)", (now,))
            raise OSError("private raw evidence")
        with patch.object(telemetry, "enqueue", side_effect=fail_after_insert), self.assertLogs(level="WARNING") as logs:
            started = self.fixture.start(view, fixtures.Role.REVIEW_SELECT)
        self.assertTrue(started.session("review")["selector_started"])
        self.assertFalse(self.events("phase_preflight_started"))
        self.assertNotIn("private raw evidence", " ".join(logs.output))
        self.assertFalse(any(e["EventId"] == "fixture" for e in self.events()))
        self.sender.assert_not_called()

    def test_only_allowlisted_classifications_and_authenticated_correlation_are_exported(self):
        view = self.fixture.coding(self.fixture.admit())
        failure = {"operation": "command/git", "kind": "command_failed", "exit_code": 1,
                   "reason_code": "git_integrity_changed", "failure_output": "PRIVATE COMMAND OUTPUT",
                   "run_id": "999", "request_id": "12345678-1234-1234-1234-123456789abc",
                   "headers": {"Authorization": "SECRET"}, "message": "PRIVATE EXCEPTION"}
        self.fixture.apply(SessionUpdated(view.task_id, view.attempt["attempt_id"], "coding", {"failure": failure}))
        with context(run_id="123", run_attempt="1", request_id="12345678-1234-1234-1234-123456789abc"):
            self.fixture.apply(Finish(view.task_id, view.attempt["attempt_id"], "coding", "failed"))
        result = self.events("phase_result")[-1]
        self.assertEqual(result["RunId"], "123")
        self.assertEqual(result["Failure"], {"operation": "command/git", "kind": "command_failed", "exit_code": 1,
                                            "reason_code": "git_integrity_changed", "request_id": failure["request_id"]})
        self.assertEqual(result["RequestId"], failure["request_id"])
        self.assertNotIn("PRIVATE", json.dumps(self.events()))
        self.assertNotIn("SECRET", json.dumps(self.events()))
        schema = {c["name"] for c in json.loads((ROOT / "infra/azure/monitoring/schema.json").read_text())}
        for event in self.events():
            self.assertLessEqual(len(json.dumps(event).encode()), telemetry.MAX_EVENT_BYTES)
            self.assertLessEqual(event.keys(), schema)

    def test_slow_or_failed_export_holds_no_controller_lock_and_does_not_block_worker_operations(self):
        view = self.fixture.admit()
        entered, release = threading.Event(), threading.Event()
        def blocked_send(payload):
            self.assertFalse(self.database.connection.in_transaction)
            entered.set()
            release.wait(5)
            raise OSError("PRIVATE RESPONSE")
        self.telemetry.send = blocked_send
        with ThreadPoolExecutor(max_workers=2) as pool:
            exporting = pool.submit(self.telemetry.flush)
            try:
                self.assertTrue(entered.wait(2))
                worker = pool.submit(self.fixture.start, view, fixtures.Role.REVIEW_SELECT)
                self.assertTrue(worker.result(timeout=2).session("review")["selector_started"])
            finally:
                release.set()
            with self.assertRaises(OSError):
                exporting.result(timeout=2)
        self.assertTrue(self.events("phase_preflight_started"))
        self.assertTrue(self.fixture.controller.inspect(view.task_id).active)

    def test_ambiguous_retry_preserves_event_ids_and_expires_after_bounded_attempts(self):
        self.sender.side_effect = OSError("private response body")
        self.assertRaises(OSError, self.telemetry.flush)
        first = json.loads(self.sender.call_args.args[0])
        self.now += 60
        self.assertRaises(OSError, self.telemetry.flush)
        second = json.loads(self.sender.call_args.args[0])
        self.assertEqual(first, second)
        for _ in range(8):
            self.now += 900
            try:
                self.telemetry.flush()
            except OSError:
                pass
        self.assertEqual(sum(first[0]["EventId"] in {e["EventId"] for e in json.loads(call.args[0])}
                             for call in self.sender.call_args_list), telemetry.MAX_TRIES)
        self.assertNotIn(first[0]["EventId"], {e["EventId"] for e in self.events()})

    def test_http_poll_aggregation_is_exact_with_bounded_samples_and_weighted_latency(self):
        for _ in range(1000):
            self.telemetry.request("broker/heartbeat", 200, 10)
        self.telemetry.request("broker/heartbeat", 503, 110)
        for _ in range(1000):
            self.telemetry.request("controller/unknown", 404, 1, {"RunId": "not-an-id", "path": "SECRET"})
        self.assertLessEqual(len(self.telemetry.samples), telemetry.SAMPLES_PER_MINUTE)
        self.telemetry.flush()
        events = json.loads(self.sender.call_args.args[0])
        summaries = [e for e in deduplicate(events + events) if e["EventType"] == "http_summary"]
        polls = [e for e in summaries if e["Operation"] == "broker/heartbeat"]
        self.assertEqual(sum(e["Count"] for e in polls), 1001)
        self.assertEqual(sum(e["Failures"] for e in polls), 1)
        self.assertAlmostEqual(sum(e["DurationMs"] for e in polls) / sum(e["Count"] for e in polls), 10110 / 1001)
        self.assertEqual(max(e["MaxDurationMs"] for e in polls), 110)
        self.assertFalse(any(e["EventType"] == "http_request" and e["Status"] == 200 for e in events))
        self.assertNotIn("SECRET", json.dumps(events))

    def test_queue_ttl_payload_and_persisted_daily_budget_are_bounded(self):
        with patch.object(telemetry, "MAX_PENDING", 3):
            self.fixture.coding(self.fixture.admit())
        self.assertLessEqual(len(self.events()), 3)
        with self.database.transaction() as db:
            self.assertGreater(int(telemetry.metadata(db, "dropped")), 0)
            with self.assertRaises(ValueError):
                telemetry.enqueue(db, {"EventId": "large", "Failure": "x" * 4096}, self.now)
        with patch.object(telemetry, "DAILY_BYTES", 0):
            self.telemetry.flush()
        self.sender.assert_not_called()
        self.now += telemetry.MAX_AGE + 1
        self.telemetry.flush()
        sent = json.loads(self.sender.call_args.args[0])
        self.assertTrue(all(e["EventType"] == "controller_heartbeat" for e in sent))
        with self.database.transaction() as db:
            used = int(telemetry.metadata(db, "bytes"))
        self.assertGreater(used, 0)
        restarted = telemetry.Telemetry(self.database, Mock(), CONFIG, "a" * 40, clock=lambda: self.now, send=self.sender)
        self.sender.reset_mock()
        with patch.object(telemetry, "DAILY_BYTES", used):
            restarted.flush()
        self.sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
