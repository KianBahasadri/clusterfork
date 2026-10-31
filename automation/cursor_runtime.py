"""Pinned Cursor ACP adapter. Auth, transcripts and provider errors stay private."""

from collections import deque
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from codex_runtime import AppServer, CodexError, write_private
from common.selection import AccountQuota, QuotaWindow, finite_number
from common.accounts import account_identity

VERSION = "2026.10.22-27dcf5f"
MODEL = "grok-4.7-high"
EFFORT = "high"  # Cursor's variant ID encodes effort; there is no separate CLI effort flag.
API = "https://api2.cursor.sh/aiserver.v1.DashboardService"


class CursorError(CodexError):
    def __init__(self, reason="cursor_execution_failed"):
        from common.diagnostics import FAILURE_REASONS
        self.reason_code = reason
        self.clusterfork_failure = {"operation": "worker/agent", "kind": "worker_error", "reason_code": reason}
        super().__init__(FAILURE_REASONS[reason])


def environment(home, base):
    # Both credential and data paths are private. Never inherit API keys, OIDC,
    # the runner's auth/config home, or another provider's credential directory.
    return {**{key: value for key, value in base.items() if key in {"PATH", "USER", "LANG", "LC_ALL", "SYSTEMROOT"}},
            "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
            "CURSOR_CONFIG_DIR": str(home / ".config/cursor"), "CURSOR_DATA_DIR": str(home / ".cursor"),
            "NO_OPEN_BROWSER": "1", "AGENT_CLI_CREDENTIAL_STORE": "file"}


def read_only_command(home, workspace, args):
    # Repository hooks/MCP/permission overrides must not run during admission.
    # Mask only executable configuration; rules and other context stay readable.
    empty = Path(home) / "readiness-empty.json"
    write_private(empty, {})
    masks = [part for name in (".cursor/hooks.json", ".cursor/mcp.json", ".cursor/cli.json")
             if (Path(workspace) / name).exists() or (Path(workspace) / name).is_symlink()
             for part in ("--ro-bind", str(empty), str(Path(workspace) / name))]
    return ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid",
            "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--bind", str(home), str(home),
            "--ro-bind", str(workspace), str(workspace), *masks, "--", *args]


def auth_path(home):
    return Path(home) / ".config/cursor/auth.json"


def rpc(home, method):
    try:
        auth = json.loads(auth_path(home).read_text())
        account_identity(auth, "cursor")
        request = Request(f"{API}/{method}", data=b"{}", headers={
            "Authorization": f"Bearer {auth['accessToken']}", "Content-Type": "application/json",
            "Connect-Protocol-Version": "1", "User-Agent": f"cursor-agent/{VERSION}"})
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(1_000_001))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except HTTPError as exc:
        raise CursorError("cursor_authentication_unavailable" if exc.code in {401, 403}
                          else "cursor_usage_unknown") from None
    except (OSError, ValueError, URLError):
        raise CursorError("cursor_usage_unknown") from None


class CursorServer(AppServer):
    """Use the existing bounded NDJSON transport, with ACP's initialization."""
    def __init__(self, home, *, env, cwd, read_only=False):
        self.timeout, self.sequence = 30, 0
        args = ["cursor-agent", "--trust", "--model", MODEL, "acp"]
        if read_only:
            write_private(Path(home) / ".config/cursor/cli-config.json", {
                "version": 1, "editor": {"vimMode": False}, "autoAcceptWebSearch": False,
                "permissions": {"allow": ["Read(*)"], "deny": [
                    "Write(*)", "Shell(*)", "WebFetch(*)", "WebSearch(*)", "Mcp(*:*)"]}})
            # Ask mode limits tools; Linux additionally enforces read-only files
            # even if a provider/tool implementation regresses. Only auth/data
            # and scratch space can be written. No runner credentials in env.
            args = read_only_command(home, cwd, args)
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, cwd=cwd,
                                        env=environment(home, env), start_new_session=True)
        self.messages, self.notifications = queue.Queue(), deque()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            result = self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                               "clientInfo": {"name": "clusterfork", "version": "1"}})
            if result.get("protocolVersion") != 1:
                raise CursorError()
            self.call("authenticate", {"methodId": "cursor_login"})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        super().send({"jsonrpc": "2.0", **value})


def open_session(server, workspace, model, effort, *, read_only=False):
    if (model, effort) != (MODEL, EFFORT):
        raise CursorError("cursor_profile_unavailable")
    result = server.call("session/new", {"cwd": str(workspace), "mcpServers": []})
    models = (result.get("models") or {}).get("availableModels", [])
    if not any(row.get("modelId") == MODEL for row in models):
        raise CursorError("cursor_profile_unavailable")
    session = result["sessionId"]
    try:
        selected = server.call("session/set_config_option", {
            "sessionId": session, "configId": "model", "value": MODEL})
    except CodexError:
        raise CursorError("cursor_profile_unavailable") from None
    if not any(row.get("id") == "model" and row.get("currentValue") == MODEL
               for row in selected.get("configOptions", [])):
        raise CursorError("cursor_profile_unavailable")
    server.call("session/set_mode", {"sessionId": session, "modeId": "ask" if read_only else "agent"})
    return session


def inspect_account(home, model, effort, *, server_factory=CursorServer):
    # A status command can report 'authenticated' even if GetMe failed. Require
    # an actual authenticated response and compare its identity to the saved JWT.
    account = rpc(home, "GetMe")
    auth = json.loads(auth_path(home).read_text())
    payload = auth["accessToken"].split(".")[1]
    subject = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
    if account.get("authId") != subject:
        raise CursorError("cursor_authentication_unavailable")
    server = server_factory(home, env={key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}, cwd=home)
    try:
        open_session(server, home, model, effort)
    finally:
        server.close()
    return {"account": {"email": account.get("email"), "planType": None}}, read_usage(home)


def read_usage(home):
    return rpc(home, "GetCurrentPeriodUsage")


def quota_snapshot(identity, model, data, *, allow_exhausted=False):
    if model != MODEL or data.get("enabled") is not True:
        raise CursorError("cursor_usage_unknown")
    plan = data.get("planUsage") or {}
    # Grok is a named API model. Auto quota and paid/on-demand availability do
    # not establish included API headroom. Never change spending settings.
    if model in data.get("autoBucketModels", []):
        raise CursorError("cursor_usage_unknown")
    used = plan.get("apiPercentUsed")
    if not finite_number(used):
        raise CursorError("cursor_usage_unknown")
    try:
        reset = int(data["billingCycleEnd"]) / 1000
        start = int(data["billingCycleStart"]) / 1000
    except (KeyError, ValueError, TypeError):
        raise CursorError("cursor_usage_unknown") from None
    if reset <= start or start > time.time():
        raise CursorError("cursor_usage_unknown")
    windows = [QuotaWindow(used, reset, "cursor/api", (reset - start) / 60)]
    total = plan.get("totalPercentUsed")
    if total is not None:
        windows.append(QuotaWindow(total, reset, "cursor/included", (reset - start) / 60))
    # No default zero, unlimited balance or inferred weekly allowance.
    return AccountQuota(identity, "cursor", model, time.time(), tuple(windows))


def run_agent(broker, config, home, workspace, prompt, env, failure_signals=(), *,
              timeout=18_000, deadline=None, image_paths=(), read_only=False,
              steering=True, server_factory=CursorServer, observe_tool=None, validate=None):
    from agent_session import WORKER_RULES
    server = server_factory(home, env=env, cwd=workspace, read_only=read_only)
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    session, pending, active, summary = {}, None, None, []
    pending_reported = False
    next_poll = 0

    def send_prompt(text):
        nonlocal active, summary
        server.sequence += 1
        active, summary = server.sequence, []
        server.send({"id": active, "method": "session/prompt", "params": {
            "sessionId": session["thread_id"], "prompt": [{"type": "text", "text": text}]}})

    def acknowledge():
        nonlocal pending_reported
        if pending and not pending_reported:
            broker.call("instruction_result", **session, **pending)
            pending_reported = True

    try:
        session = {"thread_id": open_session(server, workspace, config["model"], config["effort"], read_only=read_only),
                   "turn_id": str(uuid4())}
        if steering:
            if broker.call("steering_open", **session).get("accepting") is not True:
                raise CursorError()
        if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
            raise CursorError("cursor_turn_stopped")
        send_prompt(("" if read_only else WORKER_RULES + "\n\n") + prompt
                    + "".join(f"\nAttached issue image: {path}" for path in image_paths))
        while True:
            if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
                raise CursorError("cursor_turn_stopped")
            # Unlike app-server, ACP's prompt response arrives only at turn end.
            try:
                event = server.notifications.popleft() if server.notifications else server.messages.get(timeout=0.2)
            except queue.Empty:
                event = {}
            if event is None:
                raise CursorError()
            if "method" in event:
                params = event.get("params", {})
                if "id" in event:
                    if event["method"] == "session/request_permission":
                        options = params.get("options", [])
                        option = next((row for row in options if row.get("kind") == (
                            "reject_once" if read_only else "allow_once")), None)
                        outcome = {"outcome": "selected", "optionId": option["optionId"]} if option else {"outcome": "cancelled"}
                        server.send({"id": event["id"], "result": {"outcome": outcome}})
                    else:
                        server.send({"id": event["id"], "error": {"code": -32601, "message": "Interactive input unavailable"}})
                        raise CursorError()
                if event["method"] == "session/update" and params.get("sessionId") == session["thread_id"]:
                    update = params.get("update", {})
                    if read_only and update.get("sessionUpdate") == "tool_call" and (
                            update.get("kind") not in {"read", "search", "think"}
                            or update.get("title", "").lower().startswith("web search")):
                        raise CursorError("cursor_readiness_tool_denied")
                    if observe_tool and update.get("sessionUpdate") in {"tool_call", "tool_call_update"}:
                        observe_tool(update)
                    if update.get("sessionUpdate") == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                        summary.append(update["content"]["text"])
            elif event.get("id") == active:
                if "error" in event or event.get("result", {}).get("stopReason") != "end_turn":
                    raise CursorError()
                if pending:
                    pending["status"] = "accepted"
                    pending_reported = False
                    try:
                        acknowledge()
                    except Exception:
                        pass
                result = "".join(summary)
                follow_up = validate(result, deadline=deadline, failure_signals=failure_signals) if validate else None
                if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
                    raise CursorError("cursor_turn_stopped")
                if follow_up is None:
                    return result
                pending, pending_reported = None, False
                session["turn_id"] = str(uuid4())
                if steering and broker.call("steering_open", **session).get("accepting") is not True:
                    raise CursorError()
                if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
                    raise CursorError("cursor_turn_stopped")
                send_prompt(follow_up)
                next_poll = 0
            if steering and time.monotonic() >= next_poll:
                next_poll = time.monotonic() + 10
                try:
                    acknowledge()
                    instruction = broker.call("instructions", **session).get("instruction")
                except Exception:
                    instruction = None
                if instruction:
                    if (instruction.get("issue_id") != config["issue"]["id"]
                            or instruction.get("dispatch_key") != broker.key
                            or instruction.get("owner") != config["owner"]
                            or instruction.get("attempt_id") != config["attempt_id"]
                            or any(instruction.get(field) != value for field, value in session.items())):
                        raise CursorError()
                    # ACP's next prompt cancels the active generation and retains
                    # this session/workspace. Never resend an ambiguous handoff.
                    pending = {"comment_id": instruction["comment_id"], "delivery": instruction["delivery"],
                               "status": "uncertain"}
                    pending_reported = False
                    send_prompt("Authorized follow-up. Retain existing work and finish the task.\n" + instruction["text"])
                    try:
                        acknowledge()
                    except Exception:
                        pass
    finally:
        try:
            acknowledge()
            if steering and session:
                broker.call("steering_close", **session)
        except Exception:
            pass
        server.close()
