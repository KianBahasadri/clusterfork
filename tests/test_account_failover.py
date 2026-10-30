"""Resume real worker/session control flow with fake Codex events and leased auth."""

from collections import deque
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import agent_session
import worker
from codex_runtime import CodexError, copy_sessions
from dispatch_core import Registry
from readiness_policy import PROTOCOL
from worker_core import WorkerError
from worker_reporting import attempt_comment
import test_remote_worker as fixtures
from test_worker_reporting import limits, usage


class AccountFailoverTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.service, _, _ = fixtures.WorkerAuthorizationTests.service(self)
        self.service.clock = self.service.accounts.clock = lambda: fixtures.NOW
        self.service.sync_linear = Mock()
        self.service.sync_steering = Mock()
        self.service.steering = Mock()
        self.service.steering.poll.return_value = None
        self.identities = [self.service.accounts.enroll(fixtures.auth(f"account-{name}", f"access-{name}"))
                           for name in ("a", "b", "c")]
        self.call("start", identity=fixtures.selector_claims())
        self.call("bind_account", identity=fixtures.selector_claims(), account_id=self.identities[0])
        self.config = self.call("start")
        self.homes, self.servers, self.calls, self.deadlines = [], [], [], []
        self.error_info, self.error_status = "usageLimitExceeded", "failed"
        self.error_in_turn, self.error_retry = False, False
        self.all_exhausted = False
        self.replacement_usage = (20, 30)
        self.other_usage = (30, 40)
        self.clock = [0]
        self.probe_delay = 0
        self.turn_hook = lambda: None
        self.keep_history = True
        self.limit_on_correction = False
        self.broker = SimpleNamespace(key=fixtures.KEY, call=self.broker_call,
                                      publication_guard=worker.PublicationGuard({}))
        self.heartbeat = SimpleNamespace(failed=threading.Event())

    def call(self, action, *, identity=None, **data):
        return self.service.handle(action, identity or fixtures.claims(),
                                   {"dispatch_key": fixtures.KEY, "protocol": PROTOCOL, **data})

    def broker_call(self, action, **data):
        self.calls.append((action, data))
        if action in {"claim", "exhaust_account"}:
            self.assertTrue(all(not path.exists() for path in self.homes))
            self.assertFalse(any(row["owner"] == "123:1" for row in self.service.accounts.table.list()))
        try:
            return self.call(action, **data)
        except WorkerError as exc:
            error = HTTPError("https://broker/" + action, exc.status, "rejected", {}, None)
            error.clusterfork_action = action
            raise error from None

    def inspect(self, home, model, effort):
        self.homes.append(home)
        identity = json.loads((home / "auth.json").read_text())["tokens"]["account_id"]
        self.clock[0] += self.probe_delay if identity != "account-a" else 0
        used = ((10, 20) if identity == "account-a" else
                self.replacement_usage if identity == "account-b" else self.other_usage)
        return {"account": {"email": identity + "@example.com", "planType": "pro"}}, limits(*used)

    def final_usage(self, home):
        identity = json.loads((home / "auth.json").read_text())["tokens"]["account_id"]
        return limits(100, 27) if identity == "account-a" else limits(25, 32)

    def server(self, home, *, env, cwd, config):
        test = self
        number = len(self.servers) + 1

        class FakeServer:
            closed = False

            def __init__(inner):
                inner.events, inner.methods = deque(), []
                inner.turn_count = 0

            def call(inner, method, params):
                inner.methods.append((method, params))
                if method == "thread/start":
                    test.assertNotIn("ephemeral", params)
                    test.assertEqual(number, 1)
                    return {"thread": {"id": "thread-one"}}
                if method == "thread/resume":
                    test.assertEqual(params["threadId"], "thread-one")
                    test.assertTrue(all(server.closed for server in test.servers[:-1]))
                    history = list((home / "sessions").rglob("*"))
                    test.assertEqual([path.name for path in history], ["rollout-thread-one.jsonl"])
                    test.assertIn("original task", history[0].read_text())
                    test.assertEqual((cwd / "work.txt").read_text(), "existing edits\n")
                    test.assertFalse((home / "credential-cache").exists())
                    test.assertNotEqual(json.loads((home / "auth.json").read_text())["tokens"]["account_id"], "account-a")
                    return {"thread": {"id": "thread-one"}}
                if method == "turn/start":
                    inner.turn_count += 1
                    test.assertEqual(params["threadId"], "thread-one")
                    (home / "sessions").mkdir(exist_ok=True)
                    if test.keep_history:
                        (home / "sessions/rollout-thread-one.jsonl").write_text('{"task":"original task"}\n')
                    (home / "sessions/auth.json").write_text('"must not transfer"')
                    (home / "credential-cache").write_text("must not transfer")
                    turn = f"turn-{number}" + (f"-correction-{inner.turn_count}" if inner.turn_count > 1 else "")
                    fail = (number == 1 and (not test.limit_on_correction or inner.turn_count > 1)) or test.all_exhausted
                    (cwd / "work.txt").write_text("existing edits\n" if fail else "existing edits\nfinished\n")
                    if fail and not test.error_in_turn:
                        inner.events.append({"method": "error", "params": {
                            "threadId": "thread-one", "turnId": turn, "willRetry": test.error_retry,
                            "error": {"codexErrorInfo": test.error_info, "message": "usage limit text is not a classifier"}}})
                    if fail:
                        test.clock[0] += 25
                    else:
                        inner.events.append({"method": "item/completed", "params": {
                            "threadId": "thread-one", "turnId": turn,
                            "item": {"type": "agentMessage", "text": "Finished with the original edits intact."}}})
                    inner.events.append({"method": "turn/completed", "params": {
                        "threadId": "thread-one", "turn": {"id": turn, "status": test.error_status if fail else "completed",
                            **({"error": {"codexErrorInfo": test.error_info}} if fail and test.error_in_turn else {})}}})
                    test.turn_hook()
                    return {"turn": {"id": turn}}
                raise AssertionError(method)

            def next_event(inner, timeout=0):
                return inner.events.popleft() if inner.events else None

            def close(inner):
                inner.closed = True

        server = FakeServer()
        self.servers.append(server)
        return server

    def agent(self, *args, **kwargs):
        self.deadlines.append(kwargs["deadline"])
        return agent_session.run_agent(*args, **kwargs, server_factory=self.server)

    @contextmanager
    def execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            original = Path.cwd()
            try:
                os.chdir(workspace)
                with patch.object(worker, "inspect_account", side_effect=self.inspect), \
                     patch.object(worker, "read_usage", side_effect=self.final_usage), \
                     patch.object(worker, "run_agent", side_effect=self.agent), \
                     patch.object(worker.time, "time", return_value=fixtures.NOW), \
                     patch.object(worker.time, "monotonic", side_effect=lambda: self.clock[0]), \
                     redirect_stdout(io.StringIO()):
                    yield root, workspace
            finally:
                os.chdir(original)

    def test_usage_failure_resumes_same_thread_with_edits_and_isolated_credentials(self):
        with self.execution() as (root, workspace):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root) as summary:
                self.assertIn("original edits", summary)
                self.assertEqual((workspace / "work.txt").read_text(), "existing edits\nfinished\n")
                self.call("progress", stage="testing")
                self.call("progress", stage="publishing")
            self.call("finish", status="completed")
        self.assertEqual(len(self.servers), 2)
        self.assertTrue(all(server.closed for server in self.servers))
        self.assertEqual(self.deadlines, [18000, 18000])
        row = self.service.runs.get(fixtures.KEY)[0]
        self.assertEqual(row["thread_id"], "thread-one")
        self.assertEqual(row["turn_id"], "turn-2")
        self.assertEqual(json.loads(row["exhausted_account_ids"]), [self.identities[0]])
        segments = json.loads(row["usage_segments"])
        self.assertEqual([segment["account_id"] for segment in segments], self.identities[:2])
        self.assertEqual(segments[0]["usage_after"]["windows"][1]["used_percent"], 27)
        self.assertEqual(segments[1]["usage_before"]["windows"][1]["used_percent"], 30)
        report = attempt_comment(row, self.config["repository"], finished=True)
        for text in ("Account changes: 1", "account-a@example.com", "account-b@example.com",
                     "usage change +7 percentage points", "usage change +2 percentage points"):
            self.assertIn(text, report)
        self.assertNotIn("usage change +12", report)
        self.assertTrue(all(not path.exists() for path in self.homes))
        for token in ("access-a", "access-b", "access-c", "account-a", "account-b", "account-c"):
            with self.assertRaises(worker.PublicationBlocked):
                self.broker.publication_guard.check(token, static=False)

    def test_replacement_ranking_uses_the_same_weekly_preference(self):
        self.other_usage = (20, 30)
        inspect = self.inspect
        def quotas(home, model, effort):
            details, result = inspect(home, model, effort)
            identity = json.loads((home / "auth.json").read_text())["tokens"]["account_id"]
            return details, limits(65, 10) if identity == "account-b" else result
        with self.execution() as (root, _), patch.object(worker, "inspect_account", side_effect=quotas):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                pass
        segments = json.loads(self.service.runs.get(fixtures.KEY)[0]["usage_segments"])
        self.assertEqual(segments[-1]["account_id"], self.identities[1])

    def test_successful_resume_still_validates_before_publication(self):
        events = []
        head = ["b" * 40]
        config = {**self.config, "auto_merge": False, "validation_commands": [["verify-edits"]]}
        branch = worker.worker_branch(config["issue"]["identifier"], fixtures.KEY)

        def command(args, **kwargs):
            if args == ["verify-edits"]:
                self.assertEqual(len(self.servers), 2)
                self.assertTrue(self.servers[0].closed)
                self.assertFalse(self.servers[1].closed)
                self.assertEqual(Path("work.txt").read_text(), "existing edits\nfinished\n")
                events.append("validation")
            elif args[:2] == ["git", "rev-parse"]:
                return head[0] if args[2] == "HEAD" else "b" * 40
            elif args[:2] == ["git", "branch"]:
                return branch
            elif args[:2] == ["git", "status"]:
                return " M work.txt"
            elif args[:3] == ["git", "diff", "--name-only"]:
                return "work.txt"
            elif "commit" in args:
                head[0] = "c" * 40
            elif args[:2] == ["git", "push"]:
                self.assertEqual(events, ["validation"])
                events.append("push")
            return ""

        def publish(*args, **kwargs):
            self.assertEqual(events, ["validation", "push"])
            self.assertIn("original edits", args[2]["body"])
            events.append("PR")
            return {"html_url": "https://github.com/test/repo/pull/1"}

        with self.execution() as (root, _), patch.object(worker, "command", side_effect=command), \
             patch.object(worker, "find_pr", return_value=None), patch.object(worker, "json_request", side_effect=publish):
            status, pull = worker.execute(self.broker, config, self.heartbeat, root)
        self.assertEqual((status, pull), ("completed", "https://github.com/test/repo/pull/1"))
        self.assertEqual(events, ["validation", "push", "PR"])
        stages = [data["stage"] for action, data in self.calls if action == "progress" and "stage" in data]
        self.assertEqual(stages, ["selecting_account", "coding", "coding", "testing", "publishing"])

    def test_usage_limit_during_correction_keeps_thread_deadline_and_validation_callback(self):
        self.limit_on_correction = True
        validated = []
        def validate(summary, *, deadline, failure_signals):
            self.assertFalse(self.servers[-1].closed)
            self.assertEqual(deadline, 18000)
            self.assertFalse(any(event.is_set() for event in failure_signals))
            validated.append(summary)
            self.broker_call("progress", stage="testing")
            if len(validated) == 1:
                self.broker_call("progress", stage="coding")
                return "Fix validation/1 in the existing workspace without committing or changing authentication."
            return None
        with self.execution() as (root, _):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root, validate=validate):
                self.call("progress", stage="publishing")
        self.assertEqual(len(validated), 2)
        self.assertEqual(len(self.servers), 2)
        self.assertEqual(self.deadlines, [18000, 18000])
        turns = [params for method, params in self.servers[0].methods if method == "turn/start"]
        self.assertEqual(len(turns), 2)
        self.assertIn("Fix validation/1", turns[1]["input"][0]["text"])
        resumed = [params for method, params in self.servers[1].methods if method == "thread/resume"]
        self.assertEqual(resumed[0]["threadId"], "thread-one")
        self.assertEqual(json.loads(self.service.runs.get(fixtures.KEY)[0]["exhausted_account_ids"]), [self.identities[0]])

    def test_initial_preflight_uses_the_weekly_preference(self):
        # A new attempt still delegates initial pool selection to preflight.
        other = {**fixtures.claims(), "run_attempt": "2"}
        self.call("finish", status="failed")
        self.service.github.worker_result = Mock(return_value={"status": "completed"})
        self.config = self.call("start", identity=fixtures.selector_claims(other))
        self.broker.call = lambda action, **data: self.call(action, identity=fixtures.selector_claims(other), **data)
        inspect = self.inspect
        def quotas(home, model, effort):
            details, result = inspect(home, model, effort)
            identity = json.loads((home / "auth.json").read_text())["tokens"]["account_id"]
            return details, limits(65, 10) if identity == "account-a" else result
        with self.execution(), patch.object(worker, "inspect_account", side_effect=quotas):
            self.assertTrue(worker.select_account_for_run(self.broker, self.config, self.heartbeat))
        self.assertEqual(self.call("start", identity=other)["account_id"], self.identities[0])

    def test_missing_exhausted_usage_is_reported_separately_from_replacement_usage(self):
        final_usage = self.final_usage
        def missing(home):
            if json.loads((home / "auth.json").read_text())["tokens"]["account_id"] == "account-a":
                raise CodexError("usage unavailable")
            return final_usage(home)
        with self.execution() as (root, _), patch.object(worker, "read_usage", side_effect=missing):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                pass
        self.call("finish", status="completed")
        row = self.service.runs.get(fixtures.KEY)[0]
        segments = json.loads(row["usage_segments"])
        self.assertNotIn("usage_after", segments[0])
        self.assertIn("usage_after", segments[1])
        report = attempt_comment(row, self.config["repository"], finished=True)
        self.assertIn("final reading unavailable", report)
        self.assertIn("usage change +2 percentage points", report)

    def test_quota_failure_retains_thread_binding_when_registration_temporarily_fails(self):
        original = self.broker.call
        attempts = []
        def unavailable(action, **data):
            if action == "steering_open":
                attempts.append(data)
                if len(attempts) == 1:
                    raise OSError("registration response unavailable")
            return original(action, **data)
        self.broker.call = unavailable
        with self.execution() as (root, _), patch.object(agent_session, "POLL_SECONDS", 0):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                pass
        self.assertEqual(len(self.servers), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.service.runs.get(fixtures.KEY)[0]["thread_id"], "thread-one")

    def test_unrelated_errors_and_interruption_never_rotate_accounts(self):
        for info, status in (("rateLimitExceeded", "failed"), ("contextWindowExceeded", "failed"),
                             ({"httpConnectionFailed": {"httpStatusCode": 429}}, "failed"),
                             ("usageLimitExceeded", "interrupted")):
            with self.subTest(info=info, status=status):
                self.setUp()
                self.error_info, self.error_status = info, status
                with self.execution() as (root, _), self.assertRaises(CodexError):
                    with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                        self.fail("failed turn cannot complete")
                self.assertEqual(len(self.servers), 1)
                self.assertFalse(any(action == "exhaust_account" for action, _ in self.calls))
                self.assertTrue(all(not path.exists() for path in self.homes))

    def test_terminal_turn_error_also_confirms_usage_exhaustion(self):
        self.error_in_turn = True
        with self.execution() as (root, _):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root) as summary:
                self.assertIsNotNone(summary)
        self.assertEqual(len(self.servers), 2)

    def test_retryable_limit_notification_does_not_authorize_rotation(self):
        self.error_retry = True
        with self.execution() as (root, _), self.assertRaises(CodexError):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                self.fail("retryable errors do not confirm quota exhaustion")
        self.assertFalse(any(action == "exhaust_account" for action, _ in self.calls))

    def test_every_exhausted_account_is_excluded_and_capacity_wait_terminates(self):
        self.all_exhausted = True
        with self.execution() as (root, _):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root) as summary:
                self.assertIsNone(summary)
            self.call("finish", status="waiting_for_quota")
        row = self.service.runs.get(fixtures.KEY)[0]
        self.assertEqual(len(self.servers), 3)
        self.assertEqual(set(json.loads(row["exhausted_account_ids"])), set(self.identities))
        self.assertIn("no eligible replacement account", attempt_comment(row, self.config["repository"], finished=True))
        self.assertTrue(all(not account["owner"] for account in self.service.accounts.table.list()))

    def test_ineligible_replacements_end_without_starting_another_turn(self):
        self.replacement_usage = self.other_usage = (95, 10)
        with self.execution() as (root, _):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root) as summary:
                self.assertIsNone(summary)
        self.assertEqual(len(self.servers), 1)

    def test_original_deadline_includes_replacement_selection(self):
        self.config["agent_timeout_minutes"] = 1
        self.probe_delay = 40
        with self.execution() as (root, _), self.assertRaises(worker.WorkerFailure):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                self.fail("replacement probes must not reset the timeout")
        self.assertEqual(len(self.servers), 1)
        self.assertEqual(self.deadlines, [60])

    def test_lease_loss_or_transport_failure_does_not_enable_failover(self):
        for lost_lease in (True, False):
            with self.subTest(lost_lease=lost_lease):
                self.setUp()
                if lost_lease:
                    self.turn_hook = self.heartbeat.failed.set
                else:
                    def broken_transport():
                        raise CodexError("app-server stopped")
                    self.turn_hook = broken_transport
                with self.execution() as (root, _), self.assertRaises(CodexError):
                    with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                        self.fail("lease/transport errors must stop coding")
                self.assertEqual(len(self.servers), 1)
                self.assertFalse(any(action == "exhaust_account" for action, _ in self.calls))

    def test_quota_failure_without_stored_history_cannot_start_a_new_conversation(self):
        self.keep_history = False
        with self.execution() as (root, _), self.assertRaises(worker.WorkerFailure):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                self.fail("missing history cannot resume")
        self.assertEqual(len(self.servers), 1)
        self.assertFalse(any(action == "exhaust_account" for action, _ in self.calls))

    def coding_account(self):
        first = self.identities[0]
        leased = self.call("claim", account_id=first)
        self.call("progress", stage="coding", account_id=first, usage_before=usage())
        self.call("steering_open", thread_id="thread-one", turn_id="turn-1")
        return leased

    def exhaust(self):
        first = self.identities[0]
        leased = self.coding_account()
        self.call("save", account_id=first, lease=leased["lease"], release=True)
        self.call("exhaust_account", account_id=first, thread_id="thread-one")

    def test_broker_requires_successful_release_and_the_original_coding_thread(self):
        first = self.identities[0]
        leased = self.coding_account()
        with self.assertRaises(WorkerError):
            self.call("exhaust_account", account_id=first, thread_id="thread-one")
        # Direct expiry/release is insufficient: the worker's fenced save must succeed.
        self.service.accounts.save(first, "123:1", leased["lease"], release=True)
        with self.assertRaises(WorkerError):
            self.call("exhaust_account", account_id=first, thread_id="thread-one")
        leased = self.call("claim", account_id=first)
        self.call("save", account_id=first, lease=leased["lease"], release=True)
        with self.assertRaises(WorkerError):
            self.call("exhaust_account", account_id=first, thread_id="another-thread")
        self.call("exhaust_account", account_id=first, thread_id="thread-one")
        self.assertEqual(self.call("accounts")["accounts"], self.identities[1:])
        for action in ("accounts", "claim", "bind_account", "exhaust_account"):
            with self.subTest(action=action), self.assertRaises(WorkerError):
                self.call(action, identity=fixtures.selector_claims(), account_id=first, thread_id="thread-one")

    def test_broker_fences_one_probe_and_rejects_exhausted_accounts(self):
        self.exhaust()
        first, second, third = self.identities
        leased = self.call("claim", account_id=second)
        for action, identity in (("claim", third), ("claim", first), ("bind_account", third), ("save", third)):
            with self.subTest(action=action, identity=identity), self.assertRaises(WorkerError):
                self.call(action, account_id=identity, lease=leased["lease"], release=True)
        self.call("save", account_id=second, lease=leased["lease"], release=True)
        self.call("bind_account", account_id=second)
        leased = self.call("claim", account_id=second)
        with self.assertRaises(WorkerError):
            self.call("bind_account", account_id=third)
        self.call("progress", stage="coding", account_id=second, usage_before=usage(20, 30))
        with self.assertRaises(WorkerError):
            self.call("progress", usage_account_id=first, usage_after=usage(100, 27))
        self.call("save", account_id=second, lease=leased["lease"], release=True)
        self.assertEqual(self.call("start")["account_id"], second)
        self.assertEqual(json.loads(self.service.runs.get(fixtures.KEY)[0]["exhausted_account_ids"]), [first])

    def test_lost_probe_claim_response_does_not_expose_another_account(self):
        self.exhaust()
        original = self.service.accounts.claim
        def lost_response(identity, owner):
            original(identity, owner)
            raise OSError("claim response lost after leasing")
        with patch.object(self.service.accounts, "claim", side_effect=lost_response), self.assertRaises(OSError):
            self.call("claim", account_id=self.identities[1])
        for action in ("claim", "bind_account"):
            with self.subTest(action=action), self.assertRaises(WorkerError):
                self.call(action, account_id=self.identities[2])
        self.assertEqual(self.service.runs.get(fixtures.KEY)[0]["probe_account_id"], self.identities[1])

    def test_busy_probe_is_released_from_selection_and_another_account_can_resume(self):
        self.service.accounts.claim(self.identities[1], "another-worker")
        with self.execution() as (root, _):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                pass
        segments = json.loads(self.service.runs.get(fixtures.KEY)[0]["usage_segments"])
        self.assertEqual(segments[-1]["account_id"], self.identities[2])

    def test_failed_release_cleans_auth_and_stops_before_pool_access(self):
        original = self.broker.call
        def fail_release(action, **data):
            if action == "save" and data.get("release") and self.servers:
                raise OSError("save response unavailable")
            return original(action, **data)
        self.broker.call = fail_release
        with self.execution() as (root, _), self.assertRaises(OSError):
            with worker.run_codex(self.broker, self.config, self.heartbeat, root):
                self.fail("a failed release cannot authorize failover")
        self.assertTrue(all(not path.exists() for path in self.homes))
        self.assertFalse(any(action in {"accounts", "exhaust_account"} for action, _ in self.calls))


class SessionTransferTests(unittest.TestCase):
    def test_only_rollouts_cross_homes_and_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.mkdir()
            (source / "auth.json").write_text("fake auth")
            (source / "rollout-session.jsonl").write_text("conversation")
            copy_sessions(source, target)
            self.assertEqual([path.name for path in target.iterdir()], ["rollout-session.jsonl"])
            (source / "rollout-linked.jsonl").symlink_to(source / "auth.json")
            with self.assertRaises(CodexError):
                copy_sessions(source, target)

    def test_destination_directory_symlinks_cannot_redirect_session_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, outside = root / "source", root / "target", root / "outside"
            (source / "2026").mkdir(parents=True)
            target.mkdir()
            outside.mkdir()
            (source / "2026/rollout-session.jsonl").write_text("conversation")
            (target / "2026").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(CodexError):
                copy_sessions(source, target)
            self.assertFalse(list(outside.iterdir()))


if __name__ == "__main__":
    unittest.main()
