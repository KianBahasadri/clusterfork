"""Native MCP/HTTP and both headless provider paths, without model calls."""

from collections import deque
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import agent_session
from codex_runtime import AppServer
from common.context import CONTEXT_PROTOCOL, SERVER, TOOL
from context_transport import ContextBridge
from controller.server import Application, http_handler
from controller.model import Role
from context_fixtures import ContextFixture, COMMIT, CONTRACT
import cursor_runtime as cursor
import test_cursor_accounts as cursor_fixtures
from test_controller_authorization import claims
import worker


def mcp_request(server, arguments, *, readonly_home=None, workspace=None):
    command = [server["command"], *server["args"]]
    if readonly_home:
        command = cursor.read_only_command(readonly_home, workspace, command)
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": TOOL, "arguments": arguments}}]
    result = subprocess.run(command, input="".join(json.dumps(r) + "\n" for r in requests),
                            cwd=workspace or ROOT, env={"PATH": os.environ["PATH"]},
                            text=True, capture_output=True, timeout=20, check=True)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    return json.loads(replies[-1]["result"]["content"][0]["text"])


def published_contract(call, pull):
    def complete(arguments):
        response = call(arguments)
        text = response.get("content", "")
        while response.get("next_cursor"):
            response = call({**arguments, "cursor": response["next_cursor"]})
            text += response.get("content", "")
        if response.get("status") != "ok" or not response.get("complete"):
            raise AssertionError(response)
        return json.loads(text)
    complete({"operation": "list"})
    complete({"operation": "issue", "target": "KIAN-941"})
    complete({"operation": "pull", "target": pull})
    return complete({"operation": "file", "target": pull, "path": "docs/account.md"})


class ContextTransportTests(unittest.TestCase):
    def test_http_signed_review_and_coding_have_identical_read_transport(self):
        fx = ContextFixture(self)
        config = fx.start_review()
        role = Role.REVIEW
        app = Application(fx.f.controller, fx.f.broker, Mock(), webhook_secret="fixture", metadata={},
                          verify=lambda value: claims(role) if value == "Bearer fixture-phase" else {})
        server = ThreadingHTTPServer(("127.0.0.1", 0), http_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def call(action, **data):
            request = Request(f"http://127.0.0.1:{server.server_port}/api/worker/{action}",
                              data=json.dumps({"dispatch_key": fx.f.key, **data}).encode(),
                              headers={"Authorization": "Bearer fixture-phase", "Content-Type": "application/json"})
            with urlopen(request, timeout=20) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                return json.load(response)

        for phase in ("review", "coding"):
            if phase == "coding":
                fx.approve(config)
                fx.start_coding()
                role = Role.CODING
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(prefix="cf-context-") as directory:
                with ContextBridge(SimpleNamespace(call=call), directory, deadline=time.monotonic() + 60) as bridge:
                    result = published_contract(lambda args: mcp_request(bridge.stdio(), args), fx.pull)
                    self.assertEqual(result["commit"], COMMIT)
                    self.assertEqual(result["result"], CONTRACT)
                    config_text = json.dumps(bridge.stdio())
                    self.assertNotIn("Bearer", config_text)
                    self.assertNotIn("CONTROLLER_ONLY", config_text)

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI unavailable")
    def test_native_codex_mcp_can_read_contract_in_readonly_and_coding_threads(self):
        fx = ContextFixture(self)
        config = fx.start_review()
        role = Role.REVIEW
        for sandbox in ("read-only", "danger-full-access"):
            if sandbox == "danger-full-access":
                fx.approve(config)
                fx.start_coding()
                role = Role.CODING
            with self.subTest(sandbox=sandbox), tempfile.TemporaryDirectory(prefix="cf-codex-context-") as directory:
                home = Path(directory)
                workspace = home / "workspace"
                workspace.mkdir()
                transport = SimpleNamespace(call=lambda action, **data: fx.call(role, action, **data))
                with ContextBridge(transport, home, deadline=time.monotonic() + 60) as bridge:
                    native = AppServer(home, env={"PATH": os.environ["PATH"]}, cwd=workspace,
                        config=(*bridge.codex_config(), f'sandbox_mode="{sandbox}"', 'approval_policy="never"',
                                'model_provider="fixture"',
                                'model_providers.fixture={name="Fixture",base_url="http://127.0.0.1:1/v1",wire_api="responses",requires_openai_auth=false}'))
                    try:
                        status = native.call("mcpServerStatus/list", {"serverName": SERVER})
                        self.assertEqual(set(status["data"][0]["tools"]), {TOOL})
                        thread = native.call("thread/start", {"cwd": str(workspace), "model": "fixture",
                                             "sandbox": sandbox, "approvalPolicy": "never"})
                        def call(args):
                            result = native.call("mcpServer/tool/call", {"threadId": thread["thread"]["id"],
                                                 "server": SERVER, "tool": TOOL, "arguments": args})
                            return json.loads(result["content"][0]["text"])
                        self.assertEqual(published_contract(call, fx.pull)["result"], CONTRACT)
                        denied = call({"operation": "delete", "target": fx.pull})
                        self.assertEqual(denied["status"], "invalid_request")
                    finally:
                        native.close()

    @unittest.skipUnless(shutil.which("bwrap"), "Linux read-only sandbox unavailable")
    def test_cursor_acp_both_phases_fetch_through_mcp_with_readonly_enforcement(self):
        fx = ContextFixture(self)
        config = fx.start_review()
        for readonly in (True, False):
            role = Role.REVIEW if readonly else Role.CODING
            if not readonly:
                fx.approve(config)
                fx.start_coding()
            config = {**config, "model": cursor.MODEL, "effort": "high"}
            with self.subTest(readonly=readonly), tempfile.TemporaryDirectory(prefix="cf-cursor-context-") as directory:
                home = Path(directory) / "home"
                workspace = Path(directory) / "workspace"
                home.mkdir()
                workspace.mkdir()
                target = workspace / "protected"
                target.write_text("original")
                server = cursor_fixtures.FakeServer()
                captured = []
                def prompt(inner, value):
                    update = {"toolCallId": "context-call", "title": SERVER + ": " + TOOL, "kind": "other",
                              "rawInput": {"providerIdentifier": SERVER, "toolName": TOOL, "args": {"operation": "list"}}}
                    inner.messages.put({"method": "session/update", "params": {"sessionId": "cursor-thread",
                                        "update": {"sessionUpdate": "tool_call", **update}}})
                    inner.messages.put({"id": 700, "method": "session/request_permission", "params": {
                        "toolCall": update, "options": [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]}})
                    # Retrieval is executed through the exact MCP launch data
                    # handed to session/new, in the actual Linux boundary.
                    mcp = next(params["mcpServers"][0] for method, params in inner.calls if method == "session/new")
                    captured.append(published_contract(lambda args: mcp_request(mcp, args,
                        readonly_home=home if readonly else None, workspace=workspace), fx.pull))
                    inner.complete(value["id"], "Read exact dependency contract")
                server.on_prompt = prompt
                broker = SimpleNamespace(key=fx.f.key, call=lambda action, **data: fx.call(role, action, **data)
                                         if action == "context" else {"accepting": True})
                result = cursor.run_agent(broker, config, home, workspace, "Assess contract", {}, read_only=readonly,
                                          steering=False, server_factory=lambda *args, **kw: server)
                self.assertEqual(result, "Read exact dependency contract")
                self.assertEqual(captured[0]["result"], CONTRACT)
                self.assertIn(("session/set_mode", {"sessionId": "cursor-thread", "modeId": "ask" if readonly else "agent"}), server.calls)
                reply = next(item for item in server.sent if item.get("id") == 700)
                self.assertEqual(reply["result"]["outcome"]["optionId"], "yes")
                self.assertEqual(target.read_text(), "original")

    def test_cursor_only_grants_the_exact_context_tool(self):
        for provider, tool in ((SERVER, "delete"), ("unrelated", TOOL)):
            with self.subTest(provider=provider, tool=tool), tempfile.TemporaryDirectory(prefix="cf-denied-") as directory:
                server = cursor_fixtures.FakeServer()
                def prompt(inner, value):
                    inner.messages.put({"id": 700, "method": "session/request_permission", "params": {
                        "toolCall": {"rawInput": {"providerIdentifier": provider, "toolName": tool}},
                        "options": [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]}})
                    inner.complete(value["id"])
                server.on_prompt = prompt
                cursor.run_agent(Mock(), {"model": cursor.MODEL, "effort": "high", "context_capability": CONTEXT_PROTOCOL},
                                 Path(directory), ROOT, "Review", {}, steering=False, read_only=True,
                                 server_factory=lambda *a, **kw: server)
                reply = next(item for item in server.sent if item.get("id") == 700)
                self.assertEqual(reply["result"]["outcome"]["optionId"], "no")

    def test_context_transport_stops_at_cancellation_or_deadline_and_hides_failures(self):
        broker = Mock()
        stopped = threading.Event()
        with tempfile.TemporaryDirectory(prefix="cf-stop-") as directory:
            with ContextBridge(broker, directory, [stopped], deadline=time.monotonic() + 60) as bridge:
                stopped.set()
                result = mcp_request(bridge.stdio(), {"operation": "list"})
                self.assertEqual(result["status"], "canceled")
                broker.call.assert_not_called()
                stopped.clear()
                broker.call.side_effect = OSError("PRIVATE_TOKEN_MUST_NOT_LEAK")
                result = mcp_request(bridge.stdio(), {"operation": "list"})
                self.assertEqual(result, {"status": "unavailable", "complete": False})
                bridge.deadline = time.monotonic() - 1
                self.assertEqual(mcp_request(bridge.stdio(), {"operation": "list"})["status"], "canceled")
            self.assertFalse(bridge.path.exists())

    def test_cursor_launch_configuration_allows_one_tool_and_preserves_denials(self):
        with tempfile.TemporaryDirectory(prefix="cf-permissions-") as directory:
            home = Path(directory)
            process = Mock(stdout=io.StringIO(""), stdin=io.StringIO(), pid=999999)
            with ContextBridge(Mock(), home, deadline=time.monotonic() + 60) as bridge, \
                 patch.object(cursor.subprocess, "Popen", return_value=process) as launch, \
                 patch.object(cursor.CursorServer, "call", side_effect=[{"protocolVersion": 1}, {}]), \
                 patch.object(cursor.os, "killpg"):
                server = cursor.CursorServer(home, env={"PATH": "/usr/bin", "GITHUB_TOKEN": "PRIVATE"},
                                             cwd=ROOT, read_only=True, context_bridge=bridge)
                permissions = json.loads((home / ".cursor/cli-config.json").read_text())["permissions"]
                self.assertEqual(permissions["allow"], ["Read(*)", f"Mcp({SERVER}:{TOOL})"])
                for forbidden in ("Write(*)", "Shell(*)", "WebFetch(*)", "WebSearch(*)"):
                    self.assertIn(forbidden, permissions["deny"])
                self.assertEqual(launch.call_args.args[0][0], "bwrap")
                self.assertNotIn("GITHUB_TOKEN", launch.call_args.kwargs["env"])
                server.close()

    def test_worker_review_exec_receives_mcp_overrides_and_readonly_policy(self):
        fx = ContextFixture(self)
        config = fx.start_review()
        selection = SimpleNamespace(account_id=fx.f.account_a, model=config["model"], effort=config["effort"])
        heartbeat = SimpleNamespace(failed=threading.Event())
        broker = SimpleNamespace(call=lambda action, **data: fx.call(Role.REVIEW, action, **data))
        with tempfile.TemporaryDirectory(prefix="cf-worker-review-") as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            from contextlib import contextmanager
            @contextmanager
            def leased(*args):
                yield home, heartbeat
            def command(args, **kwargs):
                if args[0] == "codex":
                    self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
                    overrides = [args[i + 1] for i, arg in enumerate(args) if arg == "-c"]
                    mcp = next(value for value in overrides if value.startswith("mcp_servers."))
                    server = tomllib.loads(mcp)["mcp_servers"][SERVER]
                    self.assertEqual(published_contract(lambda request: mcp_request(server, request), fx.pull)["result"], CONTRACT)
                    output = Path(args[args.index("--output-last-message") + 1])
                    import test_readiness
                    output.write_text(json.dumps(test_readiness.ready()))
                return fx.f.spec.context_sha if args[:2] == ["git", "rev-parse"] else ""
            with patch.object(worker, "command", side_effect=command), \
                 patch.object(worker, "choose_account", return_value=([selection], {}, None)), \
                 patch.object(worker, "select_account", return_value=selection), \
                 patch.object(worker, "account_lease", side_effect=leased), \
                 patch.object(worker, "inspect_account", return_value=({}, {})), \
                 patch.object(worker, "quota_snapshot"):
                self.assertEqual(worker.review_readiness(broker, config, heartbeat, root), "passed")

    def test_codex_coding_session_installs_and_calls_the_same_context_tool(self):
        fx = ContextFixture(self)
        config = fx.start_review()
        fx.contract()
        fx.approve(config)
        config = fx.start_coding()
        with tempfile.TemporaryDirectory(prefix="cf-coding-session-") as directory:
            home = Path(directory)
            events = deque()
            server = Mock()
            captured = []
            def factory(*args, **kwargs):
                mcp = next(value for value in kwargs["config"] if value.startswith("mcp_servers."))
                captured.append(tomllib.loads(mcp)["mcp_servers"][SERVER])
                return server
            def call(method, params):
                if method == "thread/start":
                    self.assertEqual(params["sandbox"], "danger-full-access")
                    self.assertEqual(params["approvalPolicy"], "never")
                    return {"thread": {"id": "fixture-thread"}}
                if method == "turn/start":
                    self.assertEqual(published_contract(lambda request: mcp_request(captured[0], request), fx.pull)["result"], CONTRACT)
                    events.extend([{"method": "turn/completed", "params": {"threadId": "fixture-thread",
                                   "turn": {"id": "fixture-turn", "status": "completed"}}}, None])
                    return {"turn": {"id": "fixture-turn"}}
                raise AssertionError(method)
            server.call.side_effect = call
            server.next_event.side_effect = lambda **kwargs: events.popleft() if events else None
            broker = SimpleNamespace(call=lambda action, **data: fx.call(Role.CODING, action, **data)
                                     if action == "context" else {"accepting": True})
            agent_session.run_agent(broker, config, home, ROOT, "Implement the issue", {}, server_factory=factory)
            self.assertTrue(captured[0]["required"])
            self.assertEqual(captured[0]["enabled_tools"], [TOOL])
            server.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
