"""Failure attribution, public-log privacy, durable reporting, and monitoring setup."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import diagnostics
import worker
from dispatch_clients import Linear, RequestError, request_json
import test_worker_reporting as reporting

KEY = "a" * 64


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.token = diagnostics.CONTEXT.set({})

    def tearDown(self):
        diagnostics.CONTEXT.reset(self.token)

    def broker(self):
        broker = worker.Broker("https://clusterfork-dispatch-7604cac329.azurewebsites.net/api/worker", KEY)
        broker.token = "SECRET-credential"
        broker.expires = time.time() + 3600
        return broker

    def test_timeout_keeps_action_run_stage_request_and_elapsed_without_secrets(self):
        broker = self.broker()
        error = TimeoutError("SECRET remote exception text")
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}), \
             patch.object(worker, "urlopen", side_effect=error) as transport, \
             self.assertLogs(level="INFO") as logged, self.assertRaises(TimeoutError) as caught:
            broker.call("progress", stage="publishing", auth={"access_token": "SECRET-payload"})
        data = caught.exception.clusterfork_failure
        self.assertEqual(data["operation"], "broker/progress")
        self.assertEqual(data["stage"], "publishing")
        self.assertEqual((data["run_id"], data["run_attempt"], data["dispatch_key"]), ("123", "2", KEY))
        self.assertEqual(data["kind"], "timeout")
        self.assertEqual(data["timeout_seconds"], 20)
        self.assertGreaterEqual(data["elapsed_ms"], 0)
        sent = json.loads(transport.call_args.args[0].data)
        self.assertEqual(data["request_id"], sent["request_id"])
        self.assertNotIn("SECRET", "\n".join(logged.output))
        self.assertEqual(transport.call_count, 1)  # Logging must not replay a side effect.

    def test_http_rejection_retains_claim_action_and_status_without_response_body(self):
        error = HTTPError("https://secret.invalid/SECRET", 409, "SECRET-body", {}, io.BytesIO(b"SECRET"))
        with patch.object(worker, "urlopen", side_effect=error), self.assertLogs(level="ERROR") as logged, \
             self.assertRaises(HTTPError) as caught:
            self.broker().call("claim", account_id="SECRET-account")
        self.assertEqual(caught.exception.clusterfork_action, "claim")
        self.assertEqual(caught.exception.clusterfork_failure["http_status"], 409)
        self.assertNotIn("SECRET", "\n".join(logged.output))
        error.close()

    def test_azure_dependency_logs_name_graphql_operation_and_keep_safe_cause(self):
        with patch("dispatch_clients.urlopen", side_effect=URLError(TimeoutError("SECRET"))), \
             self.assertLogs(level="INFO") as logged, self.assertRaises(RequestError) as caught:
            request_json("https://api.linear.app/graphql?SECRET", token="SECRET", method="POST",
                         data={"query": "mutation($id: String!) { issueAddLabel(id: $id) { success } }",
                               "variables": {"id": "SECRET"}})
        self.assertEqual(caught.exception.clusterfork_failure["operation"], "linear/issueAddLabel")
        self.assertEqual(caught.exception.clusterfork_failure["kind"], "timeout")
        self.assertNotIn("SECRET", "\n".join(logged.output))

    def test_graphql_rejection_retains_bounded_classification_after_http_completion(self):
        private = "SECRET-auth-token-body-and-variables"
        query = "mutation($id: String!) { commentUpdate(id: $id) { success } }"
        response = {"data": {"commentUpdate": {"success": False}}, "errors": [{
            "message": private, "path": ["commentUpdate", private],
            "extensions": {"code": "FORBIDDEN", "type": "forbidden", "userPresentableMessage": private,
                           "credentials": private}}] * 1001}
        with patch("dispatch_clients.urlopen", return_value=io.BytesIO(json.dumps(response).encode())), \
             self.assertLogs(level="INFO") as logs, self.assertRaises(RequestError) as caught:
            Linear(private).query(query, {"id": private})
        data = diagnostics.failure(caught.exception, "worker/linear_sync")
        self.assertEqual(data["kind"], "graphql_error")
        self.assertEqual(data["operation"], "linear/commentUpdate")
        self.assertEqual(data["graphql_code"], "FORBIDDEN")
        self.assertEqual(data["graphql_type"], "forbidden")
        self.assertEqual(data["graphql_field"], "commentUpdate")
        self.assertEqual(data["graphql_error_count"], 1000)
        rendered = "\n".join(logs.output) + json.dumps(data) + str(caught.exception)
        self.assertNotIn(private, rendered)
        events = [json.loads(line.split("Clusterfork: ", 1)[1])["event"] for line in logs.output]
        self.assertEqual(events[-2:], ["operation_completed", "linear_graphql_rejected"])
        self.assertIn("FORBIDDEN", diagnostics.failure_summary(data))

    def test_unknown_graphql_metadata_and_mutation_false_are_safe_and_retryable(self):
        for response, reason in (({"errors": [{"message": "SECRET", "path": ["SECRET"],
                                               "extensions": {"code": "SECRET", "type": "SECRET"}}]}, "errors"),
                                 ({"data": None}, "invalid_response"),
                                 ({"data": {"commentUpdate": {"success": False}}}, "unsuccessful_mutation")):
            with self.subTest(reason=reason), self.assertLogs(level="ERROR") as logs, self.assertRaises(RequestError) as caught:
                Linear("SECRET", lambda *a, **kw: response).query(
                    "mutation { commentUpdate { success } }", {"id": "SECRET"})
            data = caught.exception.clusterfork_failure
            self.assertEqual(data["graphql_reason"], reason)
            self.assertNotIn("graphql_code", data)
            self.assertNotIn("graphql_type", data)
            self.assertNotIn("graphql_field", data)
            self.assertNotIn("SECRET", "\n".join(logs.output) + json.dumps(data))

    def test_command_failure_keeps_exit_and_timing_without_stdout_or_argv(self):
        with self.assertLogs(level="INFO") as logged, self.assertRaises(worker.WorkerFailure) as caught:
            worker.command([sys.executable, "-c", "print('SECRET-output'); raise SystemExit(7)"],
                           diagnostic_operation="validation/1")
        self.assertEqual(caught.exception.clusterfork_failure["exit_code"], 7)
        self.assertIn("elapsed_ms", caught.exception.clusterfork_failure)
        self.assertNotIn("SECRET", "\n".join(logged.output))

    def test_cleanup_cannot_replace_original_validation_failure(self):
        broker = Mock()
        broker.call.side_effect = lambda action, **kw: {"auth": {}, "lease": "fixture"} if action == "claim" else (_ for _ in ()).throw(TimeoutError("SECRET-cleanup"))
        original = worker.WorkerFailure("original")
        with self.assertLogs(level="ERROR") as logged, self.assertRaises(worker.WorkerFailure) as caught:
            with worker.account_lease(broker, "fixture"):
                raise original
        self.assertIs(caught.exception, original)
        self.assertIn("account_release_failed", "\n".join(logged.output))
        self.assertNotIn("SECRET", "\n".join(logged.output))

    def test_main_saves_readable_failure_in_callback_and_actions_summary(self):
        broker = Mock(stage="publishing")
        broker.call.return_value = {"serial_admission": True, "protocol": worker.PROTOCOL, "phase": "development"}
        error = TimeoutError("SECRET")
        error.clusterfork_failure = {"operation": "broker/github", "kind": "timeout", "stage": "publishing",
                                     "elapsed_ms": 20001, "timeout_seconds": 20}
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with patch.dict(os.environ, {"CLUSTERFORK_BROKER_URL": "unused", "DISPATCH_KEY": KEY,
                                         "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_STEP_SUMMARY": str(summary)}), \
                 patch.object(worker, "Broker", return_value=broker), patch.object(worker, "execute", side_effect=error), \
                 patch("sys.stderr", io.StringIO()) as stderr, self.assertLogs(level="ERROR"):
                self.assertEqual(worker.main(), 1)
            sent = broker.call.call_args.kwargs
            self.assertEqual(sent["status"], "failed")
            self.assertEqual(sent["failure"]["operation"], "broker/github")
            self.assertIn("publication", summary.read_text())
            self.assertIn("20.001s", summary.read_text())
            self.assertNotIn("SECRET", summary.read_text() + stderr.getvalue())

    def test_context_does_not_leak_between_azure_invocations(self):
        with diagnostics.context(run_id="123", request_id="a" * 36):
            self.assertEqual(diagnostics.CONTEXT.get()["run_id"], "123")
        with diagnostics.context(run_id="456"):
            self.assertNotIn("request_id", diagnostics.CONTEXT.get())
        self.assertEqual(diagnostics.CONTEXT.get(), {})

    def test_failure_boundary_drops_unknown_fields_and_rejects_free_text(self):
        safe = {"operation": "broker/github", "kind": "timeout"}
        self.assertEqual(diagnostics.validate_failure({**safe, "auth": "SECRET", "message": "SECRET"}), safe)
        for value in ({**safe, "operation": "SECRET"}, {**safe, "kind": "SECRET"},
                      {**safe, "locations": ["/private/SECRET.py:1"]}, {**safe, "elapsed_ms": float("nan")}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diagnostics.validate_failure(value)

    def test_workflow_permission_failure_reports_its_safe_reason_without_exception_text(self):
        error = worker.WorkerFailure("SECRET-private-path")
        data = diagnostics.failure(error, stage="testing", reason_code="workflows_permission_missing")
        self.assertIn("approved Workflows write permission", diagnostics.failure_summary(data))
        self.assertNotIn("SECRET", diagnostics.failure_summary(data) + json.dumps(data))
        self.assertNotIn("reason_code", diagnostics.validate_failure({**data, "reason_code": "SECRET"}))


class DurableFailureTests(unittest.TestCase):
    setUp = reporting.ProgressTests.setUp
    call = reporting.ProgressTests.call
    select = reporting.ProgressTests.select
    comments = reporting.ProgressTests.comments

    def test_failure_is_persisted_and_reconciled_to_linear(self):
        self.github.worker_pull.return_value = None
        self.github.worker_pull.side_effect = None
        self.select()
        self.call("progress", stage="publishing")
        diagnostic = {"operation": "broker/github", "kind": "timeout", "stage": "publishing",
                      "timeout_seconds": 20, "elapsed_ms": 20000, "message": "SECRET"}
        self.linear.status_comment.side_effect = RequestError()
        with self.assertLogs(level="ERROR"):
            self.call("finish", status="failed", failure=diagnostic)
        saved = json.loads(self.runs.get(KEY)[0]["failure"])
        self.assertNotIn("message", saved)
        self.assertEqual(saved["run_id"], "123")
        self.linear.status_comment.reset_mock()
        self.linear.status_comment.side_effect = None
        self.sync.sync(KEY)
        comments = [call.args[2] for call in self.linear.status_comment.call_args_list]
        self.assertTrue(any("publication" in body and "broker/github" in body and "20s" in body for body in comments))
        self.assertFalse(any("SECRET" in body for body in comments))
        self.assertEqual(self.issue["state"]["id"], self.states.blocked)


if __name__ == "__main__":
    unittest.main()
