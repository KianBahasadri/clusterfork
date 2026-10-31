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
sys.path[:0] = [str(ROOT / "automation")]
from common import diagnostics as diagnostics
import worker
from common.clients import Linear, RequestError, request_json

KEY = "a" * 64
NOW = 1_800_000_000


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.token = diagnostics.CONTEXT.set({})

    def tearDown(self):
        diagnostics.CONTEXT.reset(self.token)

    def broker(self):
        broker = worker.Broker("https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker", KEY)
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
        with patch("common.clients.urlopen", side_effect=URLError(TimeoutError("SECRET"))), \
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
        with patch("common.clients.urlopen", return_value=io.BytesIO(json.dumps(response).encode())), \
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
        self.assertIn("SECRET-output", caught.exception.clusterfork_failure["failure_output"])
        self.assertNotIn("SECRET", "\n".join(logged.output))

    def test_private_command_tails_refresh_and_scrub_runtime_and_static_credentials(self):
        for name in ("validation/1", "command/git", "command/gh", "command/codex", "http/oidc"):
            with self.subTest(operation=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "auth.json"
                path.write_text(json.dumps({"access_token": "opaque-old-login"}))
                guard = worker.PublicationGuard({"GH_TOKEN": "opaque-job-login"})
                guard.capture_auth(path)
                script = (f"from pathlib import Path; Path({str(path)!r}).write_text("
                          "'{\"access_token\": \"opaque-refreshed-login\"}'); "
                          "print('Traceback-marker opaque-old-login opaque-refreshed-login opaque-job-login "
                          "ghp_synthetic lin_api_synthetic sk-synthetic Bearer opaque-header "
                          "Basic opaque-basic api_key=opaque-key password=opaque-password'); raise SystemExit(7)")
                stdout, stderr = io.StringIO(), io.StringIO()
                with guard.watch_auth(path), patch("sys.stdout", stdout), patch("sys.stderr", stderr), \
                     self.assertLogs(level="ERROR") as logs, self.assertRaises(worker.WorkerFailure) as caught:
                    worker.command([sys.executable, "-c", script], diagnostic_operation=name, publication_guard=guard)
                data = caught.exception.clusterfork_failure
                if name in ("command/codex", "http/oidc"):
                    self.assertNotIn("failure_output", data)
                else:
                    tail = data["failure_output"]
                    self.assertIn("Traceback-marker", tail)
                    self.assertIn("[REDACTED]", tail)
                    for value in ("opaque-", "ghp_synthetic", "lin_api_synthetic", "sk-synthetic"):
                        self.assertNotIn(value, tail)
                self.assertNotIn("Traceback-marker", "\n".join(logs.output) + stdout.getvalue() + stderr.getvalue())

    def test_command_failure_tails_are_byte_bounded_nul_free_and_kept_on_timeout(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), self.assertLogs(level="ERROR"), self.assertRaises(worker.WorkerFailure) as caught:
                script = ("import sys, time; sys.stdout.buffer.write(b'earlier-output' + 'é'.encode() * 16000 "
                          "+ b'\\x00Traceback-tail\\n'); sys.stdout.flush(); "
                          + ("time.sleep(10)" if timeout else "raise SystemExit(3)"))
                worker.command([sys.executable, "-c", script], timeout=0.2 if timeout else 5,
                               diagnostic_operation="validation/1")
            data = caught.exception.clusterfork_failure
            self.assertEqual(data["kind"], "timeout" if timeout else "command_failed")
            self.assertLessEqual(len(data["failure_output"].encode()), 16_000)
            self.assertNotIn("\x00", data["failure_output"])
            self.assertNotIn("earlier-output", data["failure_output"])
            self.assertIn("Traceback-tail", data["failure_output"])

    def test_unreadable_auth_cache_withholds_output_and_keeps_original_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = worker.PublicationGuard({})
            path = Path(directory) / "auth.json"
            path.write_text("not valid auth")
            with guard.watch_auth(path), self.assertLogs(level="ERROR"), self.assertRaises(worker.WorkerFailure) as caught:
                worker.command([sys.executable, "-c", "print('private'); raise SystemExit(4)"],
                               diagnostic_operation="validation/1", publication_guard=guard)
            self.assertEqual(caught.exception.clusterfork_failure["exit_code"], 4)
            self.assertNotIn("failure_output", caught.exception.clusterfork_failure)

    def test_nul_removal_cannot_reconstruct_a_credential_after_scrubbing(self):
        guard = worker.PublicationGuard({"GH_TOKEN": "opaque-job-login"})
        self.assertEqual(guard.scrub("opaque-\x00job-login ghp_\x00synthetic"), "[REDACTED] [REDACTED]")

    def test_main_applies_the_broker_credential_guard_to_readiness_git_failures_too(self):
        broker = self.broker()
        config = {"serial_admission": True, "runtime_sha": "b"*40, "protocol": worker.PROTOCOL, "phase": "readiness",
                  "account_selection": False, "account_id": "a" * 64}
        def review(*args):
            broker.publication_guard.remember("opaque-checkout-login")
            worker.command([sys.executable, "-c", "print('private-tail opaque-checkout-login'); raise SystemExit(2)"],
                           diagnostic_operation="command/git")
        with patch.dict(os.environ, {"CLUSTERFORK_BROKER_URL": broker.url, "DISPATCH_KEY": KEY, "WORKER_REVISION": "b"*40}), \
             patch.object(worker, "Broker", return_value=broker), \
             patch.object(worker,"prepare_checkout"), \
             patch.object(worker, "json_request", side_effect=[config, {"status": "failed"}]) as request, \
             patch.object(worker, "review_readiness", side_effect=review), patch("sys.stderr", io.StringIO()), \
             self.assertLogs(level="ERROR") as logs:
            self.assertEqual(worker.main(), 1)
        sent = request.call_args.args[2]["failure"]
        self.assertEqual(sent["failure_output"], "private-tail [REDACTED]\n")
        self.assertNotIn("private-tail", "\n".join(logs.output))

    def test_failure_output_boundary_rejects_invalid_shapes_and_scrubs_again(self):
        safe = {"operation": "validation/1", "kind": "command_failed"}
        accepted = diagnostics.validate_failure({**safe, "failure_output": "Traceback ghp_synthetic api_key=example"})
        self.assertEqual(accepted["failure_output"], "Traceback [REDACTED] api_key=[REDACTED]")
        for extra in ({"failure_output": "x" * 16001}, {"failure_output": "é" * 8001},
                      {"failure_output": "null\x00"}, {"failure_output": 12},
                      {"operation": "command/codex", "failure_output": "private"},
                      {"operation": "http/oidc", "failure_output": "private"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                diagnostics.validate_failure({**safe, **extra})

    def test_cleanup_cannot_replace_original_validation_failure(self):
        broker = Mock(publication_guard=worker.PublicationGuard({}))
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
        broker.call.return_value = {"serial_admission": True, "runtime_sha": "b"*40, "protocol": worker.PROTOCOL, "phase": "development",
                                   "account_selection": False, "account_id": "a" * 64}
        error = TimeoutError("SECRET")
        error.clusterfork_failure = {"operation": "validation/1", "kind": "timeout", "stage": "publishing",
                                     "elapsed_ms": 20001, "timeout_seconds": 20, "failure_output": "Private-traceback"}
        error.clusterfork_wip = {"wip_branch": "clusterfork/kian-891-aaaaaaaa", "wip_commit_sha": "b" * 40}
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with patch.dict(os.environ, {"CLUSTERFORK_BROKER_URL": "unused", "DISPATCH_KEY": KEY, "WORKER_REVISION": "b"*40,
                                         "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_STEP_SUMMARY": str(summary)}), \
                 patch.object(worker, "Broker", return_value=broker), \
             patch.object(worker,"prepare_checkout"), patch.object(worker, "execute", side_effect=error), \
                 patch("sys.stdout", io.StringIO()) as stdout, patch("sys.stderr", io.StringIO()) as stderr, \
                 self.assertLogs(level="ERROR") as logs:
                self.assertEqual(worker.main(), 1)
            sent = broker.call.call_args.kwargs
            self.assertEqual(sent["status"], "failed")
            self.assertEqual(sent["failure"]["operation"], "validation/1")
            self.assertEqual(sent["failure"]["failure_output"], "Private-traceback")
            self.assertEqual(sent["wip_branch"], error.clusterfork_wip["wip_branch"])
            self.assertEqual(sent["wip_commit_sha"], "b" * 40)
            self.assertIn("publication", summary.read_text())
            self.assertIn("20.001s", summary.read_text())
            self.assertNotIn("SECRET", summary.read_text() + stderr.getvalue())
            self.assertNotIn("Private-traceback", summary.read_text() + stderr.getvalue() + stdout.getvalue() + "\n".join(logs.output))

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
    def test_failure_is_persisted_without_publication_of_private_output(self):
        from controller_fixtures import coding_fixture, execution_row
        from test_controller_authorization import claims
        from controller.model import Role
        from controller.reporting import attempt_reports
        from test_remote_worker import auth
        from test_worker_reporting import usage
        f = coding_fixture(self, NOW)
        def call(action, **fields):
            return f.broker.handle(action,claims(Role.CODING),{'dispatch_key':f.key,**fields})
        identity=f.pool.enroll(auth())
        call('start',protocol=worker.PROTOCOL)
        call('bind_account',account_id=identity)
        call('start',protocol=worker.PROTOCOL)
        call('claim',account_id=identity)
        call('progress',stage='coding',context_sha=f.spec.context_sha,account_id=identity,usage_before=usage())
        call('progress',stage='publishing')
        call('finish',status='failed',failure={'operation':'command/gh','kind':'timeout','stage':'publishing',
             'timeout_seconds':20,'elapsed_ms':20000,'message':'SECRET','failure_output':'Private-traceback ghp_synthetic'})
        saved=json.loads(execution_row(f)['failure'])
        self.assertNotIn('message',saved)
        self.assertEqual(saved['failure_output'],'Private-traceback [REDACTED]')
        self.assertEqual(saved['run_id'],'123')
        view=f.controller.inspect(f.view.task_id)
        body=attempt_reports({'attempt':view.attempt,'session':view.session('coding')},f.registry)[-1][1]
        self.assertIn('command/gh',body)
        self.assertIn('20s',body)
        self.assertNotIn('SECRET',body)
        self.assertNotIn('Private-traceback',body)


if __name__ == "__main__":
    unittest.main()
