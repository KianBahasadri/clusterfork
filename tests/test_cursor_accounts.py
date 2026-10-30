"""Provider precedence, fenced Cursor auth, and the normal worker path; no live logins."""

import base64
from collections import deque
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import accounts
import cursor_runtime as cursor
import worker
from dispatch_core import Registry
from publication_guard import PublicationBlocked, PublicationGuard
from quota_selection import select_account
from readiness_policy import PROTOCOL
from worker_core import Accounts, ProviderAccounts, WorkerError, account_identity
from worker_reporting import attempt_comment
import test_remote_worker as fixtures
from test_worker_reporting import limits

NOW = fixtures.NOW


def auth(subject="cursor-user", refresh="synthetic-refresh", expires=NOW + 3600):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": subject, "exp": expires}).encode()).decode().rstrip("=")
    return {"accessToken": f"synthetic.{payload}.signature", "refreshToken": refresh}


def usage(used=10):
    return {"enabled": True, "billingCycleStart": str((NOW - 86400) * 1000),
            "billingCycleEnd": str((NOW + 86400) * 1000),
            "planUsage": {"apiPercentUsed": used, "totalPercentUsed": used}}


class FakeServer:
    def __init__(self, home=None, **kwargs):
        self.home, self.options = home, kwargs
        self.sequence, self.notifications, self.messages = 0, deque(), queue.Queue()
        self.calls, self.sent, self.closed = [], [], False
        self.model, self.current = cursor.MODEL, cursor.MODEL
        self.on_prompt = lambda server, value: server.complete(value["id"])

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "session/new":
            return {"sessionId": "cursor-thread", "models": {"availableModels": [{"modelId": self.model}]}}
        if method == "session/set_config_option":
            return {"configOptions": [{"id": "model", "currentValue": self.current}]}
        return {}

    def send(self, value):
        self.sent.append(value)
        if value.get("method") == "session/prompt":
            self.on_prompt(self, value)

    def complete(self, request, text="Implemented using Cursor"):
        self.messages.put({"method": "session/update", "params": {"sessionId": "cursor-thread",
                          "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
        self.messages.put({"id": request, "result": {"stopReason": "end_turn"}})

    def close(self):
        self.closed = True


class CursorRuntimeTests(unittest.TestCase):
    def test_registry_refuses_substituted_cursor_model_or_effort(self):
        data = json.loads((ROOT / "automation/azure/projects.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            for profile in ({"model": "grok-4.7", "effort": "high"},
                            {"model": cursor.MODEL, "effort": "medium"}):
                data["agent_profiles"]["cursor"] = profile
                path.write_text(json.dumps(data))
                with self.subTest(profile=profile), self.assertRaises(ValueError):
                    Registry.load(path)

    def test_exact_catalog_variant_and_effort_are_required_and_acknowledged(self):
        for model, effort, available, current in (("grok-4.7", "high", cursor.MODEL, cursor.MODEL),
                (cursor.MODEL, "max", cursor.MODEL, cursor.MODEL),
                (cursor.MODEL, "high", "grok-4.7", cursor.MODEL),
                (cursor.MODEL, "high", cursor.MODEL, "auto")):
            with self.subTest(model=model, effort=effort, available=available, current=current):
                server = FakeServer()
                server.model, server.current = available, current
                with self.assertRaisesRegex(cursor.CursorError, "no model was substituted"):
                    cursor.open_session(server, ROOT, model, effort)
        server = FakeServer()
        cursor.open_session(server, ROOT, cursor.MODEL, "high")
        self.assertIn(("session/set_config_option", {"sessionId": "cursor-thread", "configId": "model",
                                                   "value": "grok-4.7-high"}), server.calls)

    def test_usage_requires_known_named_model_allowance_and_preserves_actual_measurements(self):
        with patch.object(cursor.time, "time", return_value=NOW):
            snapshot = cursor.quota_snapshot("cursor:fixture", cursor.MODEL, usage(45))
            self.assertEqual(snapshot.agent, "cursor")
            self.assertEqual([window.used_percent for window in snapshot.windows], [45, 45])
            self.assertEqual(snapshot.windows[0].resets_at, NOW + 86400)
            for data in ({}, {**usage(), "enabled": False}, {**usage(), "planUsage": {}},
                         {**usage(), "planUsage": {"autoPercentUsed": 0}},
                         {**usage(), "planUsage": {"apiPercentUsed": None}},
                         {**usage(), "billingCycleEnd": "unknown"},
                         {**usage(), "autoBucketModels": [cursor.MODEL]}):
                with self.subTest(data=data), self.assertRaises(cursor.CursorError):
                    cursor.quota_snapshot("cursor:fixture", cursor.MODEL, data)
            registry = Registry.load(ROOT / "automation/azure/projects.json")
            for used in (None, -1, float("nan"), 81, 100):
                data = usage(used)
                if used is None or used != used:
                    with self.assertRaises(cursor.CursorError):
                        cursor.quota_snapshot("cursor:fixture", cursor.MODEL, data)
                else:
                    self.assertIsNone(select_account([cursor.quota_snapshot("cursor:fixture", cursor.MODEL, data)],
                        registry.agent_profiles, registry.agent_selection, now=NOW))

    def test_inspection_requires_real_authenticated_identity_not_status_command(self):
        with tempfile.TemporaryDirectory() as directory:
            home, server = Path(directory), FakeServer()
            worker.write_private(cursor.auth_path(home), auth())
            with patch.object(cursor, "rpc", side_effect=[{"authId": "cursor-user", "email": "cursor@example.com"}, usage()]), \
                 patch.object(cursor.time, "time", return_value=NOW):
                details, data = cursor.inspect_account(home, cursor.MODEL, "high", server_factory=lambda *a, **k: server)
            self.assertEqual(details["account"]["email"], "cursor@example.com")
            self.assertEqual(data, usage())
            self.assertTrue(server.closed)
            with patch.object(cursor, "rpc", return_value={"authId": "someone-else"}), self.assertRaises(cursor.CursorError):
                cursor.inspect_account(home, cursor.MODEL, "high")

    def test_turn_uses_acp_reports_success_and_keeps_job_credentials_out_of_environment(self):
        server, broker = FakeServer(), Mock()
        broker.call.return_value = {"accepting": True, "instruction": None}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            result = cursor.run_agent(broker, {"model": cursor.MODEL, "effort": "high"}, home, ROOT,
                                      "Implement issue", {"PATH": "/usr/bin"}, server_factory=lambda *a, **k: server)
            self.assertEqual(result, "Implemented using Cursor")
            self.assertTrue(server.closed)
            self.assertEqual(len([v for v in server.sent if v.get("method") == "session/prompt"]), 1)
            self.assertIn("Do not commit, push", server.sent[0]["params"]["prompt"][0]["text"])
            env = cursor.environment(home, {"PATH": "/usr/bin"})
            self.assertEqual(env["XDG_CONFIG_HOME"], str(home / ".config"))
            self.assertEqual(env["CURSOR_DATA_DIR"], str(home / ".cursor"))
            for key in ("GH_TOKEN", "CODEX_HOME", "CURSOR_API_KEY", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
                self.assertNotIn(key, cursor.environment(home, {key: "synthetic-secret"}))
        self.assertEqual(broker.call.call_args.args, ("steering_close",))

    def test_deadline_and_lease_loss_stop_and_close_cursor_without_a_retry(self):
        for signal in (True, False):
            with self.subTest(lease_loss=signal):
                event, server = threading.Event(), FakeServer()
                if signal:
                    event.set()
                with self.assertRaises(cursor.CursorError):
                    cursor.run_agent(Mock(), {"model": cursor.MODEL, "effort": "high"}, ROOT, ROOT,
                        "Task", {}, failure_signals=[event], steering=False,
                        deadline=0 if not signal else None, server_factory=lambda *a, **k: server)
                self.assertTrue(server.closed)
                self.assertFalse(any(value.get("method") == "session/prompt" for value in server.sent))

    def test_readiness_launch_has_ask_mode_and_read_only_filesystem(self):
        process = Mock(stdout=io.StringIO(""), stdin=io.StringIO(), pid=999999)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cursor.subprocess, "Popen", return_value=process) as popen, \
             patch.object(cursor.CursorServer, "call", side_effect=[{"protocolVersion": 1}, {}]), \
             patch.object(cursor.os, "killpg"):
            server = cursor.CursorServer(Path(directory), env={"PATH": "/usr/bin", "GH_TOKEN": "synthetic"},
                                         cwd=Path("/workspace"), read_only=True)
            server.close()
        args = popen.call_args.args[0]
        self.assertEqual(args[0], "bwrap")
        self.assertIn("--unshare-pid", args)
        self.assertEqual(args[-6:], ["--", "cursor-agent", "--trust", "--model", cursor.MODEL, "acp"])
        self.assertNotIn("GH_TOKEN", popen.call_args.kwargs["env"])
        server = FakeServer()
        cursor.open_session(server, ROOT, cursor.MODEL, "high", read_only=True)
        self.assertIn(("session/set_mode", {"sessionId": "cursor-thread", "modeId": "ask"}), server.calls)

    def test_readiness_masks_hooks_mcp_and_project_permission_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, workspace = root / "home", root / "workspace"
            (workspace / ".cursor").mkdir(parents=True)
            for name in ("hooks.json", "mcp.json", "cli.json"):
                (workspace / ".cursor" / name).write_text('{"untrusted": true}')
            command = cursor.read_only_command(home, workspace, ["cursor-agent", "acp"])
            for name in ("hooks.json", "mcp.json", "cli.json"):
                self.assertIn(str(workspace / ".cursor" / name), command)
                self.assertEqual((workspace / ".cursor" / name).read_text(), '{"untrusted": true}')
            self.assertEqual((home / "readiness-empty.json").read_text(), "{}")

    def test_readiness_rejects_a_network_or_execution_tool(self):
        for kind in ("execute", "fetch", "edit"):
            server = FakeServer()
            def request(inner, value):
                inner.messages.put({"method": "session/update", "params": {"sessionId": "cursor-thread",
                    "update": {"sessionUpdate": "tool_call", "kind": kind, "title": "Outside read-only scope"}}})
            server.on_prompt = request
            with self.subTest(kind=kind), self.assertRaisesRegex(cursor.CursorError, "admission permissions"):
                cursor.run_agent(Mock(), {"model": cursor.MODEL, "effort": "high"}, ROOT, ROOT,
                    "Review", {}, steering=False, read_only=True, server_factory=lambda *a, **k: server)
            self.assertTrue(server.closed)

    def test_steering_stays_in_the_same_cursor_session_and_does_not_replay_a_handoff(self):
        server = FakeServer()
        def prompt(inner, value):
            if value["id"] == 1:
                inner.messages.put({"method": "session/update", "params": {"sessionId": "cursor-thread", "update": {}}})
            else:
                inner.messages.put({"id": 1, "result": {"stopReason": "cancelled"}})
                inner.complete(value["id"], "Task and follow-up complete")
        server.on_prompt = prompt
        broker = SimpleNamespace(key=fixtures.KEY)
        deliveries = []
        def call(action, **data):
            if action == "steering_open":
                return {"accepting": True}
            if action == "instructions":
                return {"instruction": {**data, "issue_id": fixtures.ISSUE, "dispatch_key": fixtures.KEY,
                    "owner": "123:1", "attempt_id": "attempt", "comment_id": "comment", "delivery": "token", "text": "Follow-up"}}
            if action == "instruction_result":
                deliveries.append(data)
            return {}
        broker.call = call
        result = cursor.run_agent(broker, {"model": cursor.MODEL, "effort": "high", "owner": "123:1",
            "attempt_id": "attempt", "issue": {"id": fixtures.ISSUE}}, ROOT, ROOT, "Task", {},
            server_factory=lambda *a, **k: server)
        self.assertEqual(result, "Task and follow-up complete")
        self.assertEqual([delivery["status"] for delivery in deliveries], ["uncertain", "accepted"])
        self.assertEqual(deliveries[0]["delivery"], deliveries[1]["delivery"])
        prompts = [v for v in server.sent if v.get("method") == "session/prompt"]
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0]["params"]["sessionId"], prompts[1]["params"]["sessionId"])


class CursorAccountTests(unittest.TestCase):
    def test_provider_identity_deduplication_and_fenced_refresh(self):
        table, now = fixtures.MemoryTable(), [NOW]
        pool = Accounts(table, lambda: now[0], provider="cursor")
        identity = pool.enroll(auth())
        with self.assertRaises(WorkerError):
            pool.enroll(auth(refresh="new-refresh"))
        first = pool.claim(identity, "one")
        now[0] += 121
        second = pool.claim(identity, "two")
        with self.assertRaises(WorkerError):
            pool.save(identity, "two", second["lease"], auth("another-user"))
        pool.save(identity, "two", second["lease"], auth(refresh="latest-refresh"), release=True)
        with self.assertRaises(WorkerError):
            pool.save(identity, "one", first["lease"], auth())
        self.assertEqual(pool.claim(identity, "three")["auth"]["refreshToken"], "latest-refresh")

    def test_staged_accounts_are_not_in_the_production_pool(self):
        pool = Accounts(fixtures.MemoryTable(), lambda: NOW, provider="cursor")
        identity = pool.enroll(auth())
        row, etag = pool.table.get(identity)
        row["state"] = "staged"
        pool.table.replace(row, etag)
        self.assertEqual(ProviderAccounts({"cursor": pool}).list(), [])
        with self.assertRaises(WorkerError):
            pool.claim(identity, "normal-worker")

    def test_reclaim_reads_reserved_backup_and_restores_latest_cloud_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            backup = store / "remote-staged/auth.json.21kb60"
            worker.write_private(backup, auth())
            pool = Accounts(fixtures.MemoryTable(), provider="cursor")
            identity = pool.enroll(auth())
            lease = pool.claim(identity, "refresh")
            pool.save(identity, "refresh", lease["lease"], auth(refresh="latest-cloud-refresh"), release=True)
            worker.write_private(store / accounts.MANIFEST, {"21kb60": {"account_id": identity,
                "backup_path": str(backup), "source_sha256": accounts.digest(backup)}})
            pool.table.delete = lambda key, etag: pool.table.rows.pop(key)
            with patch.object(accounts, "connect", return_value=pool.table), \
                 patch.object(sys, "argv", ["accounts.py", "reclaim", "--provider", "cursor", "--store", str(store)]), \
                 redirect_stdout(io.StringIO()):
                accounts.main()
            self.assertEqual(json.loads((store / "auth.json.21kb60").read_text())["refreshToken"], "latest-cloud-refresh")
            self.assertEqual(json.loads(backup.read_text()), auth())
            self.assertEqual(json.loads((store / accounts.MANIFEST).read_text()), {})
            self.assertEqual(pool.list(), [])

    def test_enrolling_an_alias_retains_existing_reservation_and_refreshed_cloud_login(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            backup = store / "remote-staged/auth.json.21kb60"
            worker.write_private(backup, auth())
            worker.write_private(store / "auth.json.alias", auth())
            pool = Accounts(fixtures.MemoryTable(), provider="cursor")
            identity = pool.enroll(auth(refresh="latest-cloud-login"))
            reservation = {"account_id": identity, "backup_path": "remote-staged/auth.json.21kb60"}
            worker.write_private(store / accounts.MANIFEST, {"21kb60": reservation})
            with patch.object(accounts, "connect", return_value=pool.table), \
                 patch.object(sys, "argv", ["accounts.py", "enroll", "--provider", "cursor", "--store", str(store)]), \
                 redirect_stdout(io.StringIO()):
                accounts.main()
            manifest = json.loads((store / accounts.MANIFEST).read_text())
            self.assertEqual(manifest["21kb60"], reservation)
            self.assertEqual(manifest["alias"]["account_id"], identity)
            self.assertEqual(json.loads(pool.table.get(identity)[0]["auth"])["refreshToken"], "latest-cloud-login")

    def test_publication_guard_retains_camelcase_credentials_after_cache_removal(self):
        guard = PublicationGuard({})
        for original in (auth(), auth(refresh="refreshed-secret")):
            guard.remember_auth(original)
            for value in original.values():
                with self.assertRaises(PublicationBlocked):
                    guard.check(value, static=False)

    def test_cursor_local_rotation_honors_remote_reservation(self):
        from test_remote_worker import LocalRemoteOwnershipTests
        with tempfile.TemporaryDirectory() as directory:
            backend, rotate = LocalRemoteOwnershipTests().backend(directory)
            backend.command = "rotate-cursor-cli"
            for operation in (lambda: backend.select("remote"), lambda: backend.save("remote"),
                              lambda: backend.delete("remote"), lambda: backend.kickoff(["remote"])):
                with self.subTest(operation=operation), self.assertRaises(rotate.RotateError):
                    operation()
            self.assertEqual(set(backend.local_profiles()), {"local-a", "local-b"})


class CursorWorkerTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.service, _, self.issue = fixtures.WorkerAuthorizationTests.service(self)
        self.codex = self.service.accounts
        self.codex.clock = lambda: NOW
        self.first = self.codex.enroll(fixtures.auth())
        self.second = self.codex.enroll(fixtures.auth("account-b", "access-b"))
        self.cursor = Accounts(fixtures.MemoryTable(), lambda: NOW, provider="cursor")
        self.reference = "cursor:" + self.cursor.enroll(auth())
        self.service.accounts = ProviderAccounts({"codex": self.codex, "cursor": self.cursor})
        self.calls, self.homes, self.used = [], [], 10
        self.selector = True
        self.broker = SimpleNamespace(key=fixtures.KEY, call=self.call, publication_guard=PublicationGuard({}))
        self.heartbeat = SimpleNamespace(failed=threading.Event())
        self.config = self.call("start", protocol=PROTOCOL)

    def call(self, action, **data):
        self.calls.append((action, data))
        try:
            return self.service.handle(action, fixtures.selector_claims() if self.selector else fixtures.claims(),
                                        {"dispatch_key": fixtures.KEY, **data})
        except WorkerError as exc:
            error = HTTPError("https://broker/" + action, exc.status, "rejected", {}, None)
            error.clusterfork_action = action
            raise error from None

    def inspect_codex(self, home, model, effort):
        self.homes.append(home)
        self.assertFalse(cursor.auth_path(home).exists())
        self.assertEqual((model, effort), ("gpt-6.1-sol", "max"))
        return {}, limits(self.used, self.used)

    def inspect_cursor(self, home, model, effort):
        self.homes.append(home)
        self.assertFalse((home / "auth.json").exists())
        self.assertEqual((model, effort), (cursor.MODEL, "high"))
        return {"account": {"email": "cursor@example.com"}}, usage()

    @contextmanager
    def execution(self):
        with patch.object(worker, "inspect_account", side_effect=self.inspect_codex), \
             patch.object(cursor, "inspect_account", side_effect=self.inspect_cursor) as inspect_cursor, \
             patch.object(cursor, "read_usage", return_value=usage(15)), \
             patch.object(worker.time, "time", return_value=NOW), redirect_stdout(io.StringIO()):
            yield inspect_cursor

    def test_preflight_probes_all_codex_before_cursor_and_selects_codex_with_lower_cursor_usage(self):
        self.used = 75
        with self.execution() as inspect_cursor:
            self.assertTrue(worker.select_account_for_run(self.broker, self.config, self.heartbeat))
        inspect_cursor.assert_not_called()
        self.assertIn(self.service.runs.get(fixtures.KEY)[0]["assigned_account_id"], (self.first, self.second))
        self.assertTrue(all(not home.exists() for home in self.homes))

    def test_busy_and_exhausted_codex_allow_cursor_without_exposing_other_credentials_to_agent(self):
        self.used = 100
        self.codex.claim(self.first, "another-worker")
        with self.execution(), tempfile.TemporaryDirectory() as directory:
            self.assertTrue(worker.select_account_for_run(self.broker, self.config, self.heartbeat))
            self.selector = False
            config = self.call("start", protocol=PROTOCOL)
            self.assertEqual((config["account_id"], config["provider"], config["model"], config["effort"]),
                             (self.reference, "cursor", cursor.MODEL, "high"))
            self.calls.clear()
            def run(*args, **kwargs):
                home = args[2]
                worker.write_private(cursor.auth_path(home), auth(refresh="cloud-refreshed"))
                return "Cursor implementation complete"
            with patch.object(cursor, "run_agent", side_effect=run), \
                 worker.run_codex(self.broker, config, self.heartbeat, Path(directory)) as summary:
                self.assertEqual(summary, "Cursor implementation complete")
                self.call("progress", stage="testing")
                self.call("github")
            self.assertFalse(any(action == "accounts" for action, _ in self.calls))
            self.assertEqual({data["account_id"] for action, data in self.calls if action == "claim"}, {self.reference})
            self.assertEqual(json.loads(self.cursor.table.get(self.reference.split(":")[1])[0]["auth"])["refreshToken"], "cloud-refreshed")
            self.call("finish", status="completed")
        run = self.service.runs.get(fixtures.KEY)[0]
        body = attempt_comment(run, "KianBahasadri/clusterfork", finished=True)
        for value in ("Cursor", "grok-4.7-high", "high", "cursor/api", "15% used", "Weekly usage: unavailable"):
            self.assertIn(value, body)
        self.assertNotIn("cloud-refreshed", body)
        self.assertTrue(all(not home.exists() for home in self.homes))

    def test_invalid_codex_auth_and_unsupported_profile_do_not_block_cursor_fallback(self):
        row, etag = self.codex.table.get(self.first)
        row["auth"] = "invalid-json"
        self.codex.table.replace(row, etag)
        with self.execution(), patch.object(worker, "inspect_account", side_effect=worker.CodexError("Pinned model unavailable")):
            self.assertTrue(worker.select_account_for_run(self.broker, self.config, self.heartbeat))
        self.assertEqual(self.service.runs.get(fixtures.KEY)[0]["assigned_account_id"], self.reference)

    def test_unavailable_profile_is_visible_and_neither_pool_starts_duplicate_work(self):
        self.used = 100
        with self.execution(), patch.object(cursor, "inspect_account", side_effect=cursor.CursorError("cursor_profile_unavailable")), \
             patch.object(cursor, "run_agent") as run:
            self.assertFalse(worker.select_account_for_run(self.broker, self.config, self.heartbeat))
        run.assert_not_called()
        row = self.service.runs.get(fixtures.KEY)[0]
        self.assertEqual(row["status"], "waiting_for_quota")
        self.assertEqual(json.loads(row["quota_wait"])["cursor_profile_unavailable"], 1)
        self.assertNotIn("assigned_account_id", row)
        self.assertEqual(self.service.receipts.get(fixtures.KEY)[0]["retry_after"], NOW + 900)

    def test_bound_agent_cannot_access_or_switch_to_another_provider(self):
        self.call("bind_account", account_id=self.first)
        self.selector = False
        self.call("start", protocol=PROTOCOL)
        for action in ("claim", "bind_account", "save"):
            with self.subTest(action=action), self.assertRaises(HTTPError) as caught:
                self.call(action, account_id=self.reference)
            self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.call("accounts"), {"accounts": [self.first]})

    def test_codex_conversation_replacement_never_exposes_cursor_on_the_coding_vm(self):
        from test_worker_reporting import usage as codex_usage
        self.call("bind_account", account_id=self.first)
        self.selector = False
        self.call("start", protocol=PROTOCOL)
        lease = self.call("claim", account_id=self.first)
        self.call("progress", stage="coding", account_id=self.first, usage_before=codex_usage(10, 20))
        row, etag = self.service.runs.get(fixtures.KEY)
        row["thread_id"] = "same-thread"
        self.service.runs.replace(row, etag)
        self.call("save", account_id=self.first, lease=lease["lease"], release=True)
        self.call("exhaust_account", account_id=self.first, thread_id="same-thread")
        self.assertEqual(self.call("accounts"), {"accounts": [self.second]})
        for action in ("claim", "bind_account"):
            with self.subTest(action=action), self.assertRaises(HTTPError):
                self.call(action, account_id=self.reference)


class CursorReadinessTests(unittest.TestCase):
    def test_review_calls_cursor_in_read_only_mode_and_submits_the_same_result_contract(self):
        from test_readiness import ready
        reference = "cursor:" + "a" * 64
        selected = SimpleNamespace(account_id=reference, model=cursor.MODEL, effort="high")
        heartbeat = SimpleNamespace(failed=threading.Event())
        config = {"account_id": reference, "provider": "cursor", "model": cursor.MODEL, "effort": "high",
                  "ref": "main", "binding": {"context_sha": "b" * 40}, "review_timeout_seconds": 600,
                  "issue": {"title": "Fix the documented dry-run flag", "comments": []}}
        calls = []
        broker = Mock()
        def call(action, **data):
            calls.append((action, data))
            if action == "checkout":
                return {"token": "synthetic-read-token"}
            self.assertEqual(action, "review")
            self.assertEqual(data["result"], ready())
            self.assertEqual(data["binding"], config["binding"])
            return {"status": "passed"}
        broker.call.side_effect = call
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            @contextmanager
            def lease(*args):
                yield home, heartbeat
            with patch.object(worker, "choose_account", return_value=([selected], {}, None)), \
                 patch.object(worker, "select_account", return_value=selected), \
                 patch.object(worker, "account_lease", side_effect=lease), \
                 patch.object(cursor, "inspect_account", return_value=({}, usage())), \
                 patch.object(cursor, "run_agent", return_value=json.dumps(ready())) as run, \
                 patch.object(cursor.time, "time", return_value=NOW), \
                 patch.object(worker, "command", side_effect=lambda args, **kw:
                              "b" * 40 if args[:2] == ["git", "rev-parse"] else "") as command, \
                 patch.dict(os.environ, {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private-oidc", "GH_TOKEN": "private-write"}):
                self.assertEqual(worker.review_readiness(broker, config, heartbeat, home), "passed")
        self.assertTrue(run.call_args.kwargs["read_only"])
        self.assertFalse(run.call_args.kwargs["steering"])
        self.assertEqual(run.call_args.kwargs["timeout"], 600)
        self.assertNotIn("GH_TOKEN", run.call_args.args[5])
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", run.call_args.args[5])
        self.assertTrue(all(call.args[0][0] == "git" for call in command.call_args_list))
        self.assertEqual([action for action, _ in calls], ["checkout", "review"])

    def test_cursor_assignment_cannot_bypass_readiness_or_obtain_publication_credentials(self):
        from test_readiness import ReadinessTests
        fixture = ReadinessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        pool = Accounts(fixtures.MemoryTable(), lambda: fixture.now, provider="cursor")
        reference = "cursor:" + pool.enroll(auth())
        facade = ProviderAccounts({"codex": fixture.accounts, "cursor": pool})
        fixture.service.accounts = fixture.gate.accounts = facade
        key, _ = fixture.begin()
        claims = fixtures.selector_claims(fixture.identity_for(key))
        fixture.service.handle("bind_account", claims, {"dispatch_key": key, "account_id": reference})
        config = fixture.call(key, "start")
        self.assertEqual((config["phase"], config["provider"], config["model"], config["effort"]),
                         ("readiness", "cursor", cursor.MODEL, "high"))
        self.assertEqual(fixture.call(key, "accounts"), {"accounts": [reference]})
        with self.assertRaises(WorkerError):
            fixture.call(key, "github")
        with self.assertRaises(WorkerError):
            fixture.call(key, "claim", account_id=fixture.identity)
        lease = fixture.call(key, "claim", account_id=reference)
        fixture.call(key, "save", account_id=reference, lease=lease["lease"],
                     auth=auth(refresh="updated-in-review"), release=True)
        self.assertEqual(json.loads(pool.table.get(reference.split(":")[1])[0]["auth"])["refreshToken"], "updated-in-review")
        self.assertEqual(fixture.receipts.list(), [])  # Coding still needs a passing review.


if __name__ == "__main__":
    unittest.main()
