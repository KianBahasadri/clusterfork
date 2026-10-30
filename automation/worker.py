#!/usr/bin/env python3
"""Run one Linear issue on an ephemeral runner using one assigned provider account."""

from __future__ import annotations

from contextlib import contextmanager
import base64
import json
import logging
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent / "azure"))
from codex_runtime import (CodexError, CodexUsageLimit, copy_sessions, inspect_account,
                           quota_snapshot, read_usage, write_private)
from agent_session import run_agent
import cursor_runtime
from worker_core import account_provider, valid_account_id
from dispatch_core import AgentProfile
from quota_selection import SelectionPolicy, select_account
from worker_reporting import usage_report, worker_branch
from readiness_policy import PROTOCOL, RESULT_SCHEMA, review_prompt, workflow_publication_policy
from publication_guard import PublicationBlocked, PublicationGuard, PublicationScanError, changed_lines
from issue_images import image_prompt, materialize_images
from diagnostics import CONTEXT, context, failure, failure_summary, http_operation, identifiers, operation, record


class WorkerFailure(RuntimeError):
    pass


class DeploymentDeferred(RuntimeError):
    pass


def json_request(url, token, data=None, method=None, *, operation_name=None):
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                    "Content-Type": "application/json", "User-Agent": "clusterfork-worker"},
                      data=None if data is None else json.dumps(data).encode(), method=method)
    with operation(operation_name or http_operation(url, data), quiet=True, timeout_seconds=20):
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None


class Broker:
    def __init__(self, url, dispatch_key):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "clusterfork-dispatch-7604cac329.azurewebsites.net":
            raise WorkerFailure("Unrecognized credential broker")
        self.url, self.key = url.rstrip("/"), dispatch_key
        self.lock = threading.RLock()
        self.token, self.expires = "", 0
        self.stage = "initializing"
        self.deployment_deferred = False
        self.publication_guard = PublicationGuard()

    def call(self, action, **data):
        if action == "progress" and data.get("stage"):
            self.stage = data["stage"]
            CONTEXT.set({**CONTEXT.get(), "stage": self.stage})
        with self.lock, context(dispatch_key=self.key, run_id=os.environ.get("GITHUB_RUN_ID", ""),
                                run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""), stage=self.stage,
                                request_id=str(uuid4())):
            if self.expires < time.time() + 60:
                oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
                oidc_url += ("&" if "?" in oidc_url else "?") + urlencode({"audience": "clusterfork-worker"})
                with operation("http/oidc", timeout_seconds=20):
                    response = json_request(oidc_url, os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"], operation_name="http/oidc")
                self.token = response["value"]
                self.publication_guard.remember(self.token)
                payload = self.token.split(".")[1]
                self.expires = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
            with operation(f"broker/{action}", quiet=action in {"heartbeat", "save", "instructions"}, timeout_seconds=20):
                try:
                    result = json_request(f"{self.url}/{action}", self.token,
                                          {"dispatch_key": self.key, **data, "request_id": CONTEXT.get()["request_id"]})
                    if result.get("status") == "deployment_deferred" and result.get("reason_code") == "deployment_paused":
                        self.deployment_deferred = True
                        deferred = DeploymentDeferred()
                        deferred.clusterfork_failure = {"operation": f"broker/{action}", "kind": "worker_error",
                                                       "reason_code": "deployment_paused"}
                        raise deferred
                    if action in {"checkout", "github"}:
                        self.publication_guard.remember(result["token"])
                    return result
                except Exception as exc:
                    exc.clusterfork_action = action
                    # The broker action is more useful than the generic HTTP transport name.
                    if getattr(exc, "clusterfork_failure", {}).get("operation") == "http/other":
                        exc.clusterfork_failure["operation"] = f"broker/{action}"
                    raise


class Heartbeat:
    def __init__(self, operation):
        self.operation = operation
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(20):
            try:
                self.operation()
            except Exception as exc:
                self.error = exc
                deferred = isinstance(exc, DeploymentDeferred)
                record("heartbeat_deferred" if deferred else "heartbeat_failed",
                       level=logging.INFO if deferred else logging.ERROR, **failure(exc, "worker/heartbeat"))
                self.failed.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=30)


@contextmanager
def account_lease(broker, identity):
    claimed = broker.call("claim", account_id=identity)
    broker.publication_guard.remember_auth(claimed["auth"])
    provider = account_provider(identity)
    with tempfile.TemporaryDirectory(prefix=f"clusterfork-{provider}-") as directory:
        home = Path(directory)
        path = cursor_runtime.auth_path(home) if provider == "cursor" else home / "auth.json"
        write_private(path, claimed["auth"])

        def save(release=False):
            try:
                auth = json.loads(path.read_text())
            except (OSError, ValueError):
                # A rejected login may remove the cache. Still release the lease,
                # preserving the original auth error rather than masking it.
                auth = None
            broker.publication_guard.remember_auth(auth)
            broker.call("save", account_id=identity, lease=claimed["lease"],
                        auth=auth, release=release)

        with broker.publication_guard.watch_auth(path):
            try:
                with Heartbeat(save) as heartbeat:
                    yield home, heartbeat
            except BaseException:
                # A cleanup failure must not replace the original operational failure.
                try:
                    save(release=True)
                except Exception as exc:
                    record("account_release_failed", level=logging.ERROR, **failure(exc, "worker/account_release"))
                raise
            else:
                save(release=True)


def command(args, *, env=None, timeout=1200, failure_signals=(), stdin=None, allowed_returncodes=(0,), diagnostic_operation=None):
    binary = Path(args[0]).name
    name = diagnostic_operation or "command/" + ({"git": "git", "gh": "gh", "python3": "python", "python": "python", "codex": "codex"}.get(binary, "other"))
    with operation(name, quiet=diagnostic_operation is None, timeout_seconds=timeout):
        return _command(args, env=env, timeout=timeout, failure_signals=failure_signals,
                        stdin=stdin, allowed_returncodes=allowed_returncodes, diagnostic_operation=name)


def _command(args, *, env, timeout, failure_signals, stdin, allowed_returncodes, diagnostic_operation):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="surrogateescape", newline="") as log:
        process = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                   stdout=log, stderr=log, env=env, text=True, start_new_session=True)
        if stdin is not None:
            process.stdin.write(stdin)
            process.stdin.close()
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if time.monotonic() > deadline or any(event.is_set() for event in failure_signals):
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                exc = WorkerFailure("Command stopped after timeout or loss of its credential lease")
                failure(exc, diagnostic_operation, kind="lease_lost" if any(event.is_set() for event in failure_signals) else "timeout",
                        timeout_seconds=timeout)
                raise exc
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                continue
        if process.returncode not in allowed_returncodes:
            # Never copy raw Codex/auth/transport output into public Actions logs.
            exc = WorkerFailure("Command failed")
            failure(exc, diagnostic_operation, kind="command_failed", exit_code=process.returncode)
            raise exc
        log.seek(0)
        return log.read()


def clean_environment():
    # OIDC/job credentials stay in the parent. Agents receive only leased auth.
    return {key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}


def choose_account(broker, config, worker_heartbeat, *, preflight=False, replacement=False, deadline=None):
    profiles = ({name: AgentProfile(**profile) for name, profile in config["profiles"].items()}
                if config.get("profiles") else {config.get("provider", "codex"): AgentProfile(config["model"], config["effort"])})
    policy = SelectionPolicy(**config["selection"])
    snapshots = []
    if worker_heartbeat.failed.is_set() or deadline is not None and time.monotonic() >= deadline:
        raise WorkerFailure("Codex stopped after timeout or loss of its credential lease")
    # Initial pool access belongs to preflight. Coding may probe replacements
    # only after the broker has recorded release of its exhausted identity.
    identities = broker.call("accounts")["accounts"] if preflight or replacement else [config["account_id"]]
    reasons = config["quota_wait"] = {}
    for provider in profiles:
        if not any(account_provider(identity) == provider for identity in identities):
            reasons[provider + "_no_accounts"] = 1
    print(f"Inspecting quota for {len(identities)} enrolled agent accounts", flush=True)
    # Probe every Codex candidate before opening Cursor credentials. A current
    # Cursor binding is rechecked in place; only new decisions apply precedence.
    identities = sorted(identities, key=lambda identity: account_provider(identity) == "cursor")
    for identity in identities:
        provider = account_provider(identity)
        if provider not in profiles or replacement and provider != "codex":
            continue
        if provider == "cursor" and select_account(snapshots, profiles, policy, now=time.time()):
            break
        profile = profiles[provider]
        if (worker_heartbeat.failed.is_set() or deadline is not None and time.monotonic() >= deadline):
            raise WorkerFailure("Codex stopped after timeout or loss of its credential lease")
        try:
            with account_lease(broker, identity) as (home, heartbeat):
                inspect = cursor_runtime.inspect_account if provider == "cursor" else inspect_account
                snapshot = cursor_runtime.quota_snapshot if provider == "cursor" else quota_snapshot
                _, limits = inspect(home, profile.model, profile.effort)
                snapshots.append(snapshot(identity, profile.model, limits))
                if heartbeat.failed.is_set() or worker_heartbeat.failed.is_set():
                    raise WorkerFailure("Worker lease was lost during quota inspection")
        except HTTPError as exc:
            if exc.code not in {404, 409} or getattr(exc, "clusterfork_action", "claim") != "claim":
                raise
            print(f"Account {identity[:8]} is no longer available", flush=True)
            code = provider + "_busy"
            reasons[code] = reasons.get(code, 0) + 1
            continue
        except CodexError as exc:
            # Unknown/busy/unavailable accounts never count as unused quota.
            reason = str(exc) if isinstance(exc, cursor_runtime.CursorError) else type(exc).__name__
            code = (exc.reason_code if isinstance(exc, cursor_runtime.CursorError) and exc.reason_code in {
                "cursor_profile_unavailable", "cursor_authentication_unavailable", "cursor_usage_unknown"}
                else provider + "_inspection_unavailable")
            reasons[code] = reasons.get(code, 0) + 1
            print(f"{provider} account {identity[-64:-56]} unavailable for quota inspection ({reason})", flush=True)
            continue
    for provider in profiles:
        count = sum(snapshot.agent == provider for snapshot in snapshots)
        if count and not select_account([snapshot for snapshot in snapshots if snapshot.agent == provider], profiles, policy, now=time.time()):
            reasons[provider + "_usage_ineligible"] = count
    return snapshots, profiles, policy


def select_account_for_run(broker, config, heartbeat):
    if config.get("account_id"):
        # Retrying the selector must preserve an existing run binding.
        return True
    snapshots, profiles, policy = choose_account(broker, config, heartbeat, preflight=True)
    selected = select_account(snapshots, profiles, policy, now=time.time())
    if selected is None:
        broker.call("finish", status="waiting_for_quota", quota_wait=config.get("quota_wait", {}))
        return False
    broker.call("bind_account", account_id=selected.account_id)
    return True


@contextmanager
def run_codex(broker, config, worker_heartbeat, directory):
    if account_provider(config.get("account_id")) == "cursor":
        with run_cursor(broker, config, worker_heartbeat, directory) as summary:
            yield summary
        return
    # Keep the selected account leased through validation and publication so the
    # final reading covers the full attempt without another worker consuming it.
    image_paths = materialize_images(config, directory)
    broker.call("progress", stage="selecting_account")
    snapshots, profiles, policy = choose_account(broker, config, worker_heartbeat)
    thread_id, deadline, replacement = None, None, False
    timeout = config.get("agent_timeout_minutes", 300) * 60
    sessions = directory / "codex-sessions"
    while True:
        if deadline is not None and (time.monotonic() >= deadline or worker_heartbeat.failed.is_set()):
            raise WorkerFailure("Codex stopped after timeout or loss of its credential lease")
        selected = select_account(snapshots, profiles, policy, now=time.time())
        if not selected:
            if replacement:
                print("Quota unavailable: no eligible replacement Codex account remains for this attempt", flush=True)
            yield None
            return
        snapshots = [row for row in snapshots if row.account_id != selected.account_id]
        ran_agent = False
        limited = None
        try:
            if replacement:
                broker.call("bind_account", account_id=selected.account_id)
            with account_lease(broker, selected.account_id) as (home, heartbeat):
                details, limits = inspect_account(home, config["model"], config["effort"])
                broker.publication_guard.capture_auth(home / "auth.json")
                fresh = quota_snapshot(selected.account_id, config["model"], limits)
                selection = select_account([fresh], profiles, policy, now=time.time())
                if not selection:
                    continue
                account = details.get("account") or {}
                broker.call("progress", stage="coding", account_id=selection.account_id,
                            context_sha=config.get("checkout_sha"),
                            account_email=account.get("email"), account_plan=account.get("planType"),
                            usage_before=usage_report(fresh))
                print(f"Running {selection.model} ({selection.effort}); minimum quota headroom {selection.remaining_percent:g}%", flush=True)
                issue = config["issue"]
                prompt = (
                    f"Implement Linear issue {issue['identifier']}: {issue['title']}\n"
                    f"Issue URL: {issue['url']}\n\n"
                    + workflow_publication_policy(config.get("workflow_changes_allowed"))
                    + f"\n\n{issue.get('description') or ''}"
                    + image_prompt(config.get("images", []))
                )
                env = {**clean_environment(), "CODEX_HOME": str(home)}
                ran_agent = True
                try:
                    copy_sessions(sessions, home / "sessions")
                    if deadline is None:
                        deadline = time.monotonic() + timeout
                    try:
                        with operation("worker/agent", timeout_seconds=timeout):
                            try:
                                summary = run_agent(broker, config, home, Path.cwd(), prompt, env,
                                                    timeout=timeout, deadline=deadline, thread_id=thread_id,
                                                    image_paths=image_paths,
                                                    failure_signals=[heartbeat.failed, worker_heartbeat.failed])
                            except CodexUsageLimit as exc:
                                limited = exc
                    finally:
                        # run_agent has stopped the old app-server before copying.
                        # Neither auth.json nor other account caches cross homes.
                        copy_sessions(home / "sessions", sessions)
                    if limited is None:
                        broker.publication_guard.capture_auth(home / "auth.json")
                        yield summary
                        return
                finally:
                    try:
                        if heartbeat.failed.is_set() or worker_heartbeat.failed.is_set():
                            raise WorkerFailure("Credential lease was lost")
                        final = quota_snapshot(selection.account_id, selection.model, read_usage(home),
                                               allow_exhausted=True)
                        broker.call("progress", usage_account_id=selection.account_id, usage_after=usage_report(final))
                    except Exception as exc:
                        # Keep the execution outcome even if provider usage is
                        # unavailable. The finish comment explicitly says so.
                        print("Final quota reading unavailable; usage change will be reported as unknown", flush=True)
                        record("final_usage_unavailable", level=logging.WARNING, **failure(exc, "worker/final_usage"))
            if limited is not None:
                # Normal context exit must successfully save/release the lease
                # and delete its entire auth home before pool access is granted.
                if home.exists():
                    raise WorkerFailure("Exhausted Codex credential home was not removed")
                if worker_heartbeat.failed.is_set() or time.monotonic() >= deadline:
                    raise WorkerFailure("Codex stopped after timeout or loss of its credential lease")
                if not any(sessions.rglob("rollout-*.jsonl")):
                    raise WorkerFailure("Codex conversation history is unavailable for resume")
                thread_id = limited.thread_id
                broker.call("exhaust_account", account_id=selection.account_id, thread_id=thread_id)
                print(f"Account {selection.account_id[:8]} exhausted; selecting a replacement for the same conversation", flush=True)
                replacement = True
                snapshots, profiles, policy = choose_account(
                    broker, config, worker_heartbeat, replacement=True, deadline=deadline)
        except HTTPError as exc:
            if (exc.code in {404, 409} and not ran_agent
                    and getattr(exc, "clusterfork_action", "claim") == "claim"):
                continue
            raise
        except CodexError:
            if ran_agent:
                raise
            continue


@contextmanager
def run_cursor(broker, config, worker_heartbeat, directory):
    """The selected Cursor account stays leased through testing/publication."""
    image_paths = materialize_images(config, directory)
    broker.call("progress", stage="selecting_account")
    snapshots, profiles, policy = choose_account(broker, config, worker_heartbeat)
    selected = select_account(snapshots, profiles, policy, now=time.time())
    if selected is None:
        yield None
        return
    ran = False
    try:
        with account_lease(broker, selected.account_id) as (home, heartbeat):
            details, limits = cursor_runtime.inspect_account(home, selected.model, selected.effort)
            fresh = cursor_runtime.quota_snapshot(selected.account_id, selected.model, limits)
            if not select_account([fresh], profiles, policy, now=time.time()):
                config.setdefault("quota_wait", {})["cursor_usage_ineligible"] = 1
                yield None
                return
            broker.publication_guard.capture_auth(cursor_runtime.auth_path(home))
            account = details.get("account") or {}
            broker.call("progress", stage="coding", account_id=selected.account_id,
                        context_sha=config.get("checkout_sha"), account_email=account.get("email"),
                        account_plan=account.get("planType"), usage_before=usage_report(fresh))
            print(f"Running cursor {selected.model} ({selected.effort}); minimum quota headroom {selected.remaining_percent:g}%", flush=True)
            issue = config["issue"]
            prompt = (f"Implement Linear issue {issue['identifier']}: {issue['title']}\nIssue URL: {issue['url']}\n\n"
                      + workflow_publication_policy(config.get("workflow_changes_allowed"))
                      + f"\n\n{issue.get('description') or ''}" + image_prompt(config.get("images", [])))
            ran = True
            try:
                timeout = config.get("agent_timeout_minutes", 300) * 60
                summary = cursor_runtime.run_agent(
                    broker, config, home, Path.cwd(), prompt, clean_environment(),
                    timeout=timeout, image_paths=image_paths,
                    failure_signals=[heartbeat.failed, worker_heartbeat.failed])
                broker.publication_guard.capture_auth(cursor_runtime.auth_path(home))
                yield summary
            finally:
                try:
                    if heartbeat.failed.is_set() or worker_heartbeat.failed.is_set():
                        raise WorkerFailure("Credential lease was lost")
                    final = cursor_runtime.quota_snapshot(selected.account_id, selected.model,
                                                          cursor_runtime.read_usage(home), allow_exhausted=True)
                    broker.call("progress", usage_account_id=selected.account_id, usage_after=usage_report(final))
                except Exception:
                    print("Final Cursor quota reading unavailable; usage change is unknown", flush=True)
    except HTTPError as exc:
        if ran or exc.code not in {404, 409} or getattr(exc, "clusterfork_action", "claim") != "claim":
            raise
        yield None
    except CodexError as exc:
        if ran:
            raise
        code = (exc.reason_code if isinstance(exc, cursor_runtime.CursorError) and exc.reason_code in {
            "cursor_profile_unavailable", "cursor_authentication_unavailable", "cursor_usage_unknown"}
            else "cursor_inspection_unavailable")
        config.setdefault("quota_wait", {})[code] = 1
        yield None


@contextmanager
def git_auth(token, directory):
    askpass = directory / "git-askpass"
    askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s" "x-access-token";; *) printf "%s" "$CLUSTERFORK_GIT_TOKEN";; esac\n')
    askpass.chmod(0o700)
    yield {**clean_environment(), "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
           "CLUSTERFORK_GIT_TOKEN": token}


def review_readiness(broker, config, worker_heartbeat, directory):
    """Review in the selected provider's read-only sandbox using the quota pool."""
    token = broker.call("checkout")["token"]
    with git_auth(token, directory) as env:
        command(["git", "fetch", "origin", f"+refs/heads/{config['ref']}:refs/remotes/origin/{config['ref']}"], env=env)
    command(["git", "checkout", "--detach", config["binding"]["context_sha"]])
    if (command(["git", "rev-parse", "HEAD"]).strip() != config["binding"]["context_sha"]
            or command(["git", "status", "--porcelain"]).strip()):
        raise WorkerFailure("Readiness checkout does not match its clean, reviewed context")
    image_paths = materialize_images(config, directory)
    snapshots, profiles, policy = choose_account(broker, config, worker_heartbeat)
    while True:
        selected = select_account(snapshots, profiles, policy, now=time.time())
        if not selected:
            broker.call("finish", status="waiting_for_quota", quota_wait=config.get("quota_wait", {}))
            return "readiness_retry"
        snapshots = [row for row in snapshots if row.account_id != selected.account_id]
        try:
            with account_lease(broker, selected.account_id) as (home, heartbeat):
                provider = account_provider(selected.account_id)
                inspect = cursor_runtime.inspect_account if provider == "cursor" else inspect_account
                snapshot = cursor_runtime.quota_snapshot if provider == "cursor" else quota_snapshot
                _, limits = inspect(home, config["model"], config["effort"])
                fresh = snapshot(selected.account_id, config["model"], limits)
                if not select_account([fresh], profiles, policy, now=time.time()):
                    continue
                schema, output = directory / "readiness-schema.json", directory / "readiness-result.json"
                schema.write_text(json.dumps(RESULT_SCHEMA))
                if provider == "cursor":
                    text = cursor_runtime.run_agent(
                        broker, config, home, Path.cwd(), review_prompt(config["issue"],
                            workflow_changes_allowed=config.get("workflow_changes_allowed"), images=config.get("images", []))
                        + "\nReturn only JSON matching this schema:\n" + json.dumps(RESULT_SCHEMA),
                        clean_environment(), read_only=True, steering=False, image_paths=image_paths,
                        timeout=config["review_timeout_seconds"],
                        failure_signals=[heartbeat.failed, worker_heartbeat.failed])
                    output.write_text(text)
                else:
                    command(["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                         "--color", "never", "--sandbox", "read-only", "--model", config["model"],
                         "-c", 'approval_policy="never"',
                         "-c", f'model_reasoning_effort="{config["effort"]}"',
                         "-c", 'cli_auth_credentials_store="file"',
                         "--output-schema", str(schema), "--output-last-message", str(output)]
                        + [arg for path in image_paths for arg in ("--image", path)] + ["-"],
                        stdin=review_prompt(config["issue"], workflow_changes_allowed=config.get("workflow_changes_allowed"),
                                            images=config.get("images", [])),
                        env={**clean_environment(), "CODEX_HOME": str(home)},
                        timeout=config["review_timeout_seconds"],
                        failure_signals=[heartbeat.failed, worker_heartbeat.failed])
                if (command(["git", "rev-parse", "HEAD"]).strip() != config["binding"]["context_sha"]
                        or command(["git", "status", "--porcelain"]).strip()):
                    raise WorkerFailure("Readiness review changed repository contents")
                result, result_error, result_location = None, None, None
                try:
                    # Preserve malformed output for the broker's actionable rejection,
                    # without sending file paths, exception messages, or credentials.
                    with output.open(encoding="utf-8") as stream:
                        result = stream.read(28_001)
                    if len(result.encode()) > 28_000:
                        result, result_error = result[:28_000], "output_too_large"
                    else:
                        try:
                            result = json.loads(result)
                        except json.JSONDecodeError as exc:
                            result_error = "invalid_json"
                            result_location = {"line": exc.lineno, "column": exc.colno}
                except UnicodeError:
                    result, result_error = None, "invalid_encoding"
                except OSError:
                    result_error = "missing_output"
            return broker.call("review", binding=config["binding"], result=result,
                               result_error=result_error, result_location=result_location)["status"]
        except HTTPError as exc:
            if exc.code in {404, 409} and getattr(exc, "clusterfork_action", "claim") == "claim":
                continue
            raise
        except CodexError:
            continue


def find_pr(config, token, branch):
    repository = config["repository"]
    query = urlencode({"state": "all", "head": f"{repository.split('/')[0]}:{branch}", "base": config["ref"]})
    pulls = json_request(f"https://api.github.com/repos/{repository}/pulls?{query}", token)
    # Recover already-merged PRs too: auto-merge may finish before the runner
    # persists its outcome. A retry must not implement the same issue again.
    return next((pull["html_url"] for pull in pulls
                 if pull.get("state") == "open" or pull.get("merged_at")), None)


def published_pull(config, token, pull_url, dispatch_key, branch, *, expected_sha=None):
    repository = config["repository"]
    match = re.fullmatch(re.escape(f"https://github.com/{repository}/pull/") + r"([1-9][0-9]*)", pull_url)
    if not match:
        raise WorkerFailure("Pull request URL does not match the worker repository")
    pull = json_request(f"https://api.github.com/repos/{repository}/pulls/{match[1]}", token)
    head, base = pull.get("head", {}), pull.get("base", {})
    if ((head.get("repo") or {}).get("full_name") != repository or head.get("ref") != branch
            or base.get("ref") != config["ref"] or pull.get("user", {}).get("login") != "clusterfork[bot]"
            or f"<!-- clusterfork-dispatch:{dispatch_key} -->" not in (pull.get("body") or "")
            or not re.fullmatch(r"[0-9a-f]{40}", head.get("sha", ""))
            or (expected_sha is not None and head["sha"] != expected_sha)):
        raise WorkerFailure("Pull request does not match the worker's published change")
    return pull


def request_auto_merge(config, token, pull_url, dispatch_key, branch, *, expected_sha=None):
    if not config.get("auto_merge", True):
        return
    repository = config["repository"]
    pull = published_pull(config, token, pull_url, dispatch_key, branch, expected_sha=expected_sha)
    head = pull["head"]
    if pull.get("merged"):
        return
    if pull.get("state") != "open" or pull.get("draft"):
        raise WorkerFailure("Worker pull request is not ready for auto-merge")
    owner, name = repository.split("/")
    policy = json_request("https://api.github.com/graphql", token, {
        "query": """query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            autoMergeAllowed
          }
        }""",
        "variables": {"owner": owner, "name": name}})
    repo = (policy.get("data") or {}).get("repository") or {}
    if policy.get("errors") or not repo.get("autoMergeAllowed"):
        raise WorkerFailure("Auto-merge must be enabled in the repository")
    env = {**clean_environment(), "GH_TOKEN": token}
    # PR-specific check visibility needs no Administration grant. GitHub may take
    # a few seconds to register new runs. Pending/failed required checks are valid
    # gates: native auto-merge waits for them rather than bypassing them.
    for attempt in range(30):
        raw = command(["gh", "pr", "checks", pull_url, "--required", "--json", "name,bucket"],
                      env=env, allowed_returncodes=(0, 1, 8))
        try:
            checks = json.loads(raw)
        except ValueError:
            checks = None
        if isinstance(checks, list) and checks and all(
            isinstance(check, dict) and isinstance(check.get("name"), str) and check["name"]
            and check.get("bucket") in {"pass", "pending", "fail", "cancel", "skipping"}
            for check in checks
        ):
            break
        if attempt < 29:
            time.sleep(2)
    else:
        raise WorkerFailure("Auto-merge requires observable required CI checks on the pull request")
    # No --admin: GitHub enforces required checks, reviews and merge conflicts.
    # Match the observed commit so a concurrent push cannot be merged blindly.
    command(["gh", "pr", "merge", pull_url, "--auto", "--squash", "--match-head-commit", head["sha"]],
            env=env)
    print(f"Automatic squash merge requested: {pull_url}", flush=True)


def scan_publication(broker, base, metadata, *, staged=False):
    """Check exact values everywhere; static patterns only on non-test additions."""
    with operation("worker/publication_scan"):
        try:
            guard = broker.publication_guard
            guard.refresh_auth()
            args = ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                    "--no-renames", "--text", "--cached" if staged else f"origin/{base}...HEAD"]
            diff = command(args)
            paths = command([*args, "--name-only", "-z"]).split("\0")
            for path in paths:
                if path:
                    guard.check(path, static=False)
                    patch = command([*args, "--unified=0", "--", path])
                    guard.check(changed_lines(patch), static=not path.startswith("tests/"))
                    guard.check(changed_lines(patch, "-"), static=False)
            # Keep the full diff (including removals, context and filenames) for
            # exact-value checks, even for test fixtures and binary files.
            guard.check(diff, static=False)
            for value in metadata:
                guard.check(value)
        except (PublicationBlocked, PublicationScanError) as blocked:
            exc = WorkerFailure(str(blocked))
            failure(exc, "worker/publication_scan",
                    reason_code=("publication_credentials_detected" if isinstance(blocked, PublicationBlocked)
                                 else "publication_scan_failed"))
            raise exc from None


def execute(broker, config, heartbeat, directory):
    issue = config["issue"]
    repair = config.get("repair")
    target_key = repair["target_dispatch_key"] if repair else broker.key
    branch = worker_branch(repair["target_identifier"] if repair else issue["identifier"], target_key)
    if repair and (repair["target_branch"] != branch or not re.fullmatch(r"[0-9a-f]{64}", target_key)):
        raise WorkerFailure("Auto-repair branch does not match its parent dispatch")
    token = broker.call("checkout")["token"]
    broker.publication_guard.remember(token)
    existing = find_pr(config, token, branch)
    if existing:
        pull = published_pull(config, token, existing, target_key, branch)
        if pull.get("merged"):
            return "completed", existing
        if pull.get("state") != "open":
            raise WorkerFailure("Worker pull request is no longer open")
    with git_auth(token, directory) as env:
        # workflow_dispatch and manual reruns can check out an old SHA. Fetch
        # after repository admission so preceding merged work is always present.
        command(["git", "fetch", "origin", f"+refs/heads/{config['ref']}:refs/remotes/origin/{config['ref']}"], env=env)
        # Recover a branch pushed by a previous attempt that stopped before PR creation.
        refs = command(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], env=env)
        if refs.strip():
            command(["git", "fetch", "origin", f"refs/heads/{branch}"], env=env)
            command(["git", "checkout", "-b", branch, "FETCH_HEAD"])
        else:
            command(["git", "checkout", "-b", branch, f"origin/{config['ref']}"])
    original = command(["git", "rev-parse", "HEAD"]).strip()
    if existing and not repair:
        token = broker.call("github")["token"]
        broker.publication_guard.remember(token)
        pull = published_pull(config, token, existing, target_key, branch, expected_sha=original)
        scan_publication(broker, config["ref"], (pull.get("title") or "", pull.get("body") or ""))
        request_auto_merge(config, token, existing, target_key, branch, expected_sha=original)
        return "completed", existing
    config = {**config, "checkout_sha": command(["git", "rev-parse", f"origin/{config['ref']}"]).strip()}
    if config.get("readiness", {}).get("context_sha", config["checkout_sha"]) != config["checkout_sha"]:
        raise WorkerFailure("Coding checkout changed after readiness approval; request another review")
    with run_codex(broker, config, heartbeat, directory) as summary:
        if summary is None:
            return "waiting_for_quota", None
        broker.call("progress", stage="testing")
        if (command(["git", "rev-parse", "HEAD"]).strip() != original
                or command(["git", "branch", "--show-current"]).strip() != branch):
            raise WorkerFailure("Agent changed the worker's Git history or branch")
        changed = command(["git", "status", "--porcelain"]).strip()
        paths = command(["git", "diff", "--name-only", "HEAD"]).splitlines()
        paths += command(["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
        changed_files = len(set(paths))
        paths += command(["git", "diff", "--name-only", f"origin/{config['ref']}...HEAD"]).splitlines()
        if (any(path.startswith(".github/workflows/") for path in paths)
                and config.get("workflow_changes_allowed") is not True):
            exc = WorkerFailure("Workflow file changes require an approved Workflows write permission")
            failure(exc, reason_code="workflows_permission_missing")
            raise exc
        print("Running repository validation", flush=True)
        for index, args in enumerate(config["validation_commands"], 1):
            command(list(args), env=clean_environment(), failure_signals=[heartbeat.failed],
                    diagnostic_operation=f"validation/{index}")
        broker.call("progress", stage="publishing", **({"repair_validation_passed": True} if repair else {}))
        token = broker.call("github")["token"]
        broker.publication_guard.remember(token)
        title = f"{issue['identifier']}: {issue['title']}"[:250]
        body = (f"Implements [{issue['identifier']}]({issue['url']}).\n\n{summary[:40_000]}\n\n"
                f"Validation: all configured repository checks passed.\n\n"
                f"<!-- clusterfork-dispatch:{target_key} -->")
        metadata = [title, body, summary, issue["title"]]
        if changed:
            command(["git", "add", "-A"])
            scan_publication(broker, config["ref"], metadata, staged=True)
            command(["git", "-c", "user.name=clusterfork[bot]", "-c",
                     "user.email=clusterfork[bot]@users.noreply.github.com", "commit", "-m",
                     f"{issue['identifier']}: {issue['title'][:180]}"])
        delta = command(["git", "diff", "--name-only", f"origin/{config['ref']}...HEAD"]).strip()
        if repair:
            broker.call("progress", stage="publishing", repair_result={
                "commit_sha": command(["git", "rev-parse", "HEAD"]).strip(), "changed_files": changed_files})
        if not delta and not existing:
            return "completed", None
        # Revalidate ownership before updating an existing PR, including when
        # the project uses manual merging. Never publish to a foreign/fork PR.
        if existing:
            pull = published_pull(config, token, existing, target_key, branch)
            metadata += [pull.get("title") or "", pull.get("body") or ""]
        scan_publication(broker, config["ref"], metadata)
        with git_auth(token, directory) as env:
            command(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], env=env)
        if existing:
            request_auto_merge(config, token, existing, target_key, branch,
                               expected_sha=command(["git", "rev-parse", "HEAD"]).strip())
            return "completed", existing
        pull = json_request(f"https://api.github.com/repos/{config['repository']}/pulls", token,
                            {"title": title, "head": branch, "base": config["ref"], "body": body})
        request_auto_merge(config, token, pull["html_url"], target_key, branch,
                           expected_sha=command(["git", "rev-parse", "HEAD"]).strip())
        return "completed", pull["html_url"]


def main(*, select_only=False):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    CONTEXT.set(identifiers({"dispatch_key": os.environ.get("DISPATCH_KEY", ""),
                             "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                             "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""), "stage": "initializing"}))
    broker = Broker(os.environ["CLUSTERFORK_BROKER_URL"], os.environ["DISPATCH_KEY"])
    started = False
    try:
        config = broker.call("start", protocol=PROTOCOL)
        started = True
        if config.get("serial_admission") is not True:
            raise WorkerFailure("Azure dispatcher does not confirm serial repository admission; "
                                "deploy the matching broker before starting workers")
        if config.get("protocol") != PROTOCOL or config.get("phase") not in {"readiness", "development"}:
            raise WorkerFailure("Deploy a compatible readiness worker and broker")
        if config.get("account_selection") is not select_only:
            raise WorkerFailure("Broker did not authorize this job's account access role")
        if not select_only and not valid_account_id(config.get("account_id")):
            raise WorkerFailure("Account preflight must assign one agent account before execution")
        CONTEXT.set({**CONTEXT.get(), "phase": config["phase"]})
        with Heartbeat(lambda: broker.call("heartbeat")) as heartbeat, \
             tempfile.TemporaryDirectory(prefix="clusterfork-worker-") as directory:
            if select_only:
                selected = select_account_for_run(broker, config, heartbeat)
                with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                    output.write(f"selected={str(selected).lower()}\n")
                return 0
            if config["phase"] == "readiness":
                status, pull = review_readiness(broker, config, heartbeat, Path(directory)), None
            else:
                status, pull = execute(broker, config, heartbeat, Path(directory))
        if config["phase"] == "development":
            broker.call("finish", status=status, pull_request=pull or "",
                        **({"quota_wait": config.get("quota_wait", {})} if status == "waiting_for_quota" else {}))
        print(f"Worker outcome: {status}" + (f"; {pull}" if pull else ""))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as stream:
                stream.write(f"### Clusterfork {config.get('provider', 'codex')} {config['phase']} worker\n\nIssue: {config['issue']['identifier']}\n\n"
                             f"Account: `{config['account_id']}`\n\n"
                             f"Model: `{config['model']}` / `{config['effort']}`\n\nOutcome: {status}\n\n"
                             + (f"Pull request: {pull}\n" if pull else ""))
                if pull:
                    stream.write("\nMerge policy: " + ("automatic squash merge after required checks\n"
                                 if config.get("auto_merge", True) else "manual merge (project override)\n"))
        return 0
    except Exception as exc:
        if isinstance(exc, DeploymentDeferred) or getattr(broker, "deployment_deferred", False) is True:
            # A deferred heartbeat can stop a subprocess/quota read with a
            # generic lease error. Preserve the broker's startup classification.
            record("worker_deferred", reason_code="deployment_paused")
            message = ("Azure deployment paused startup. This runner is exiting to allow deployment to drain; "
                       "work resumes after successful deployment and confirmation that prior GitHub attempts stopped.")
            print(message)
            if select_only:
                with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                    output.write("selected=false\n")
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
                    stream.write("### Clusterfork deployment wait\n\n" + message + "\n")
            return 0
        with context(dispatch_key=os.environ.get("DISPATCH_KEY", ""), run_id=os.environ.get("GITHUB_RUN_ID", ""),
                     run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""), stage=getattr(broker, "stage", "initializing")):
            diagnostic = failure(exc)
            record("worker_failed", level=logging.ERROR, **diagnostic)
        if started:
            try:
                broker.call("finish", status="failed", failure=diagnostic)
            except Exception as report_error:
                record("failure_report_deferred", level=logging.ERROR, **failure(report_error))
        detail = failure_summary(diagnostic)
        print(f"Worker failed: {detail} No authentication or agent transcripts were logged.", file=sys.stderr)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
                stream.write(f"### Clusterfork worker failed\n\n{detail}\n\n"
                             f"Run: `{os.environ.get('GITHUB_RUN_ID', '')}` / attempt `{os.environ.get('GITHUB_RUN_ATTEMPT', '')}`\n")
        return 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select-account", action="store_true", help="Run quota preflight without an agent")
    raise SystemExit(main(select_only=parser.parse_args().select_account))
