"""Linear-to-live-agent steering, with durable storage and no cloud/model calls."""

from collections import deque
import copy
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
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]

from agent_session import run_agent
from codex_runtime import AppServer, CodexError, CodexRejected
from common.clients import Linear
from common.routing import Registry, webhook_event
from test_linear_dispatch import SECRET, signed
from test_remote_worker import ISSUE, NOW
from test_controller_authorization import claims
from controller.model import Role
from controller.instructions import Instructions, rows
from controller_fixtures import coding_fixture
from test_worker_reporting import usage
from call_budget import record_api_calls
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




class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        self.f = coding_fixture(self, NOW)
        self.service = self.f.broker
        self.linear = Tracker(self.f.registry)
        self.service.linear = self.linear
        self.service.images.download = lambda *args: None
        # Preserve the admitted content while exercising the real instruction adapter.
        self.linear.current.update(title=self.f.issue['title'],description=self.f.issue['description'])
        self.steering = Instructions(self.f.controller,self.f.registry,self.linear,clock=lambda:NOW)
        self.service.steering = self.steering
        from test_remote_worker import auth
        identity = self.f.pool.enroll(auth())
        self.call('start',protocol=worker.PROTOCOL)
        self.call('bind_account',account_id=identity)
        self.run = self.call('start',protocol=worker.PROTOCOL)
        self.call('claim',account_id=identity)
        self.call('progress',stage='coding',context_sha=self.f.spec.context_sha,account_id=identity,usage_before=usage())

    def row(self,comment):
        with self.f.database.transaction() as db:
            return next(row for row in rows(db) if row['comment_id']==comment['id'])

    def call(self,action,**data):
        return self.service.handle(action,claims(Role.CODING),{'dispatch_key':self.f.key,**data})

    def run_session(self, server, directory, *, broker_failure=None, open_failure=0, open_exception=None):
        service = self.service
        class Broker:
            key = self.f.key
            failures = 0
            open_failures = 0
            def call(inner, action, **data):
                if action == "steering_open" and inner.open_failures < open_failure:
                    inner.open_failures += 1
                    raise open_exception or OSError("broker unavailable during registration")
                if action == "instruction_result" and broker_failure and inner.failures < broker_failure:
                    inner.failures += 1
                    raise OSError("response unavailable")
                return self.call(action, **data)
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
        self.assertTrue(self.f.controller.inspect(self.f.view.task_id).session("coding")["data"]["steering_closed"])

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
            with patch.object(self.steering, "handle", side_effect=unavailable):
                self.run_session(server, Path(directory))
            self.assertIn("Historical investigation notes", (Path(directory) / "setup.md").read_text())
            self.assertEqual(server.methods.count("turn/start"), 1)
            self.assertNotIn("turn/steer", server.methods)

    def test_turn_completion_during_poll_rejects_delivery_and_never_starts_another_turn(self):
        self.linear.comments = [comment()]
        with tempfile.TemporaryDirectory() as directory:
            server = FakeAgent(Path(directory))
            original = self.steering.handle
            def finish_during_poll(*args):
                instruction = original(*args)
                server.complete()
                return instruction
            with patch.object(self.steering, "handle", side_effect=finish_during_poll):
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
        data = json.loads((ROOT / "automation/controller/projects.json").read_text())
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

