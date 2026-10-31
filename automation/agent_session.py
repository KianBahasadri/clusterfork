"""A live Codex conversation, including validation and bounded correction turns."""

import time
from urllib.error import HTTPError

from codex_runtime import AppServer, CodexError, CodexRejected, CodexUsageLimit
from context_transport import agent_context
from common.context import CONTEXT_PROMPT

POLL_SECONDS = 10
WORKER_RULES = (
    "Read and follow AGENTS.md and the repository docs. Make the requested change, "
    "run appropriate tests, and update the owning documentation when behavior changes. "
    "Do not commit, push, open a PR, change authentication, or deploy anything; "
    "the worker publishes the result. Work as one agent without spawning subagents. "
    "Authorized follow-up instructions arrive as user messages from the broker. "
    "Continue this task in its existing workspace and retain the work so far. "
    "Finish with a concise description of changes and validation." + CONTEXT_PROMPT
)


def run_agent(broker, config, home, workspace, prompt, env, failure_signals=(), *,
              timeout=18_000, server_factory=AppServer, image_paths=(), thread_id=None, deadline=None,
              validate=None):
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    with agent_context(broker, config, home, failure_signals, deadline=deadline) as bridge:
        return _run_agent(broker, config, home, workspace, prompt, env, failure_signals,
                          timeout=timeout, server_factory=server_factory, image_paths=image_paths,
                          thread_id=thread_id, deadline=deadline, validate=validate, bridge=bridge)


def _run_agent(broker, config, home, workspace, prompt, env, failure_signals=(), *,
               timeout, server_factory, image_paths, thread_id, deadline, validate, bridge):
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    if time.monotonic() >= deadline:
        raise CodexError("Codex stopped after timeout or loss of its credential lease")
    server = server_factory(home, env=env, cwd=workspace, config=(
        'cli_auth_credentials_store="file"', f'model_reasoning_effort="{config["effort"]}"',
        *(bridge.codex_config() if bridge else ())))
    session, pending = {}, None
    registered = False
    completed, summary = None, "Codex completed the issue."
    usage_limited, resuming = False, False

    def events(wait=0):
        nonlocal completed, summary, usage_limited
        event = server.next_event(timeout=wait)
        while event:
            if "id" in event:
                # Headless jobs cannot resolve interactive tool/approval requests.
                server.send({"id": event["id"], "error": {"code": -32601, "message": "Interactive requests are unavailable"}})
                raise CodexError("Codex requested unsupported interactive input")
            params = event.get("params", {})
            if params.get("threadId") == session.get("thread_id"):
                if (event["method"] == "turn/completed"
                        and params.get("turn", {}).get("id") == session.get("turn_id")):
                    completed = params["turn"]["status"]
                    error = params["turn"].get("error") or {}
                    if error:
                        usage_limited = error.get("codexErrorInfo") == "usageLimitExceeded"
                if (event["method"] == "error" and params.get("turnId") == session.get("turn_id")
                        and params.get("willRetry") is False):
                    usage_limited = (params.get("error") or {}).get("codexErrorInfo") == "usageLimitExceeded"
                if (event["method"] == "item/completed" and params.get("turnId") == session.get("turn_id")
                        and params.get("item", {}).get("type") == "agentMessage"):
                    summary = params["item"]["text"]
            event = server.next_event(timeout=0)

    def confirm():
        nonlocal pending
        if pending:
            broker.call("instruction_result", **session, **pending)
            pending = None

    def start_turn(inputs):
        nonlocal registered, completed, usage_limited, summary
        if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
            raise CodexError("Codex stopped after timeout or loss of its credential lease")
        turn = server.call("turn/start", {"threadId": session["thread_id"], "effort": config["effort"],
                          "input": inputs})
        session["turn_id"] = turn["turn"]["id"]
        registered, completed, usage_limited = False, None, False
        summary = "Codex completed the issue."

    try:
        if any(event.is_set() for event in failure_signals):
            raise CodexError("Codex stopped after timeout or loss of its credential lease")
        params = {"model": config["model"], "cwd": str(workspace),
                            "approvalPolicy": "never", "sandbox": "danger-full-access",
                            "developerInstructions": WORKER_RULES}
        if thread_id:
            params.update(threadId=thread_id, excludeTurns=True)
        thread = server.call("thread/resume" if thread_id else "thread/start", params)
        session["thread_id"] = thread["thread"]["id"]
        if thread_id and session["thread_id"] != thread_id:
            raise CodexError("Codex resumed a different conversation")
        inputs = ([{"type": "text", "text": (
            "Continue the original task from its current state. The previous turn stopped at an account "
            "usage limit. Retain existing edits and conversation history, follow all original instructions "
            "and authorized follow-ups, and finish the remaining work.")}]
            if thread_id else [{"type": "text", "text": prompt}]
            + [{"type": "localImage", "path": path} for path in image_paths])
        start_turn(inputs)
        next_poll = 0
        while True:
            if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
                raise CodexError("Codex stopped after timeout or loss of its credential lease")
            events(wait=0.2)
            # Retry only the durable result acknowledgement, never turn/steer.
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + POLL_SECONDS
                try:
                    if not registered and (validate is not None or not completed or thread_id or usage_limited):
                        try:
                            registered = broker.call("steering_open", **session).get("accepting") is True
                        except HTTPError as exc:
                            if 400 <= exc.code < 500:
                                raise CodexError("Broker rejected the active agent session") from None
                            raise
                        if not registered:
                            raise CodexError("Broker did not register the active agent session")
                    confirm()
                    instruction = (broker.call("instructions", **session).get("instruction")
                                   if not completed else None)
                except CodexError:
                    raise
                except Exception:
                    # Tracker/broker outages do not throw away the running agent's work.
                    instruction = None
                if instruction:
                    if (instruction.get("issue_id") != config["issue"]["id"]
                            or instruction.get("dispatch_key") != broker.key
                            or instruction.get("owner") != config["owner"]
                            or instruction.get("attempt_id") != config["attempt_id"]
                            or any(instruction.get(field) != value for field, value in session.items())):
                        raise CodexError("Broker instruction binding does not match this agent")
                    pending = {"comment_id": instruction["comment_id"], "delivery": instruction["delivery"],
                               "status": "uncertain"}
                    events()  # It may have finished while the broker request was in flight.
                    if completed:
                        pending["status"] = "failed"
                    else:
                        try:
                            result = server.call("turn/steer", {"threadId": session["thread_id"],
                                "expectedTurnId": session["turn_id"], "clientUserMessageId": instruction["comment_id"],
                                "input": [{"type": "text", "text": instruction["text"]}]})
                            if result.get("turnId") == session["turn_id"]:
                                pending["status"] = "accepted"
                        except CodexRejected:
                            pending["status"] = "failed"
                        except Exception:
                            # A write/response timeout may follow acceptance; never resend.
                            pass
                    try:
                        confirm()
                    except Exception:
                        pass
            if completed:
                if completed != "completed":
                    if completed == "failed" and usage_limited:
                        if not registered:
                            continue  # Retain the thread binding through a transient broker outage.
                        resuming = True
                        raise CodexUsageLimit(session["thread_id"])
                    raise CodexError("Codex turn failed or was interrupted")
                follow_up = validate(summary, deadline=deadline, failure_signals=failure_signals) if validate else None
                if time.monotonic() >= deadline or any(event.is_set() for event in failure_signals):
                    raise CodexError("Codex stopped after timeout or loss of its credential lease")
                if follow_up is None:
                    return summary
                try:
                    confirm()
                except Exception:
                    pass  # An unconfirmed old-turn handoff remains uncertain; never replay it.
                pending = None
                start_turn([{"type": "text", "text": follow_up}])
                next_poll = 0
    finally:
        try:
            confirm()
        except Exception:
            pass  # Azure expires the unconfirmed handoff to uncertain.
        try:
            if session.get("turn_id") and not resuming:
                broker.call("steering_close", **session)
        except Exception:
            pass  # Stage/finish callbacks and reconciliation also close the boundary.
        server.close()
