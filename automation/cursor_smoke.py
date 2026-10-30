#!/usr/bin/env python3
"""Run the once-only KIAN-922 probe with its dedicated broker role; no secrets in CI."""

import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

import worker
import cursor_runtime
from cursor_validation import KEY, REFERENCE
from quota_selection import SelectionPolicy, select_account
from dispatch_core import AgentProfile
from worker_reporting import usage_report


class ValidationBroker(worker.Broker):
    def call(self, action, **data):
        if action not in {"start", "heartbeat", "claim", "save", "begin", "finish"}:
            raise worker.WorkerFailure("Validation has no account-pool or publication authority")
        return super().call("validation_" + action, **data)


def probe(broker, config, worker_heartbeat, directory):
    with worker.account_lease(broker, REFERENCE) as (home, heartbeat):
        _, limits = cursor_runtime.inspect_account(home, config["model"], config["effort"])
        before = cursor_runtime.quota_snapshot(REFERENCE, config["model"], limits)
        profiles = {"cursor": AgentProfile(config["model"], config["effort"])}
        if not select_account([before], profiles, SelectionPolicy(**config["selection"]), now=worker.time.time()):
            raise cursor_runtime.CursorError("cursor_usage_unknown")
        authorization = broker.call("begin", usage_before=usage_report(before))
        if authorization != {"authorized": True, "timeout_seconds": 120}:
            raise worker.WorkerFailure("Broker did not authorize the bounded turn")
        expected, target, reads = str(uuid4()), directory / "cloud-smoke.txt", {}
        verified = False

        def observe(update):
            nonlocal verified
            if update.get("kind") == "read" and any(
                    Path(location.get("path", "")) in {target, Path(target.name)}
                    for location in update.get("locations", [])):
                reads[update["toolCallId"]] = True
            if (reads.get(update.get("toolCallId")) and update.get("status") == "completed"
                    and expected in json.dumps(update)):
                verified = True

        summary = cursor_runtime.run_agent(
            broker, config, home, directory,
            f"This is a bounded integration probe. Create cloud-smoke.txt containing exactly {expected} followed by a newline. "
            "Read cloud-smoke.txt using the file read tool, then reply with its content. Do only these two file operations. "
            "Do not use the shell, network, subagents, repository publication, authentication or spending settings.",
            worker.clean_environment(), timeout=120, steering=False, observe_tool=observe,
            failure_signals=[heartbeat.failed, worker_heartbeat.failed])
        if (target.is_symlink() or not target.is_file() or target.read_text() != expected + "\n"
                or expected not in summary or not verified):
            raise cursor_runtime.CursorError("file_check_failed")
        after = cursor_runtime.quota_snapshot(REFERENCE, config["model"], cursor_runtime.read_usage(home), allow_exhausted=True)
        return usage_report(after)


def main():
    broker = ValidationBroker(os.environ["CLUSTERFORK_BROKER_URL"], KEY)
    status, reason, after = "failed", "cursor_execution_failed", None
    try:
        config = broker.call("start")
        if (config.get("provider"), config.get("account_id"), config.get("model"), config.get("effort")) != (
                "cursor", REFERENCE, "grok-4.7-high", "high"):
            raise worker.WorkerFailure("Invalid reserved Cursor assignment")
        with worker.Heartbeat(lambda: broker.call("heartbeat")) as heartbeat, \
             tempfile.TemporaryDirectory(prefix="clusterfork-cursor-smoke-") as directory:
            after = probe(broker, config, heartbeat, Path(directory))
        status, reason = "passed", "passed"
    except Exception as exc:
        if isinstance(exc, cursor_runtime.CursorError):
            reason = exc.reason_code
        elif getattr(exc, "clusterfork_action", "") in {
                "validation_start", "validation_claim", "validation_begin", "validation_save"}:
            reason = {"validation_start": "broker_authorization_failed", "validation_claim": "account_unavailable",
                      "validation_begin": "cursor_usage_unknown", "validation_save": "credential_persistence_failed"}[
                          exc.clusterfork_action]
        # Do not print raw exceptions, provider output, auth paths or transcripts.
    try:
        broker.call("finish", status=status, reason=reason, file_verified=status == "passed", usage_after=after)
    except Exception:
        status, reason = "failed", "result_persistence_failed"
    result = (f"KIAN-922 Cursor cloud smoke: {status} ({reason}).\n"
              "Provider: cursor; profile: 21kb60; model: grok-4.7-high; effort: high.\n"
              f"Run: https://github.com/KianBahasadri/clusterfork/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}\n")
    print(result)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(result)
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
