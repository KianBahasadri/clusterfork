#!/usr/bin/env python3
"""Run one Linear issue on an ephemeral GitHub runner using leased Codex auth."""

from __future__ import annotations

from contextlib import contextmanager
import base64
import json
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

sys.path.insert(0, str(Path(__file__).parent / "azure"))
from codex_runtime import CodexError, inspect_account, quota_snapshot, read_usage, write_private
from agent_session import run_agent
from dispatch_core import AgentProfile
from quota_selection import SelectionPolicy, select_account
from worker_reporting import usage_report
from readiness_policy import PROTOCOL, RESULT_SCHEMA, review_prompt


class WorkerFailure(RuntimeError):
    pass


def json_request(url, token, data=None, method=None):
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                    "Content-Type": "application/json", "User-Agent": "clusterfork-worker"},
                      data=None if data is None else json.dumps(data).encode(), method=method)
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

    def call(self, action, **data):
        with self.lock:
            if self.expires < time.time() + 60:
                oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
                oidc_url += ("&" if "?" in oidc_url else "?") + urlencode({"audience": "clusterfork-worker"})
                response = json_request(oidc_url, os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
                self.token = response["value"]
                payload = self.token.split(".")[1]
                self.expires = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
            try:
                return json_request(f"{self.url}/{action}", self.token, {"dispatch_key": self.key, **data})
            except HTTPError as exc:
                exc.clusterfork_action = action
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
            except Exception:
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
    with tempfile.TemporaryDirectory(prefix="clusterfork-codex-") as directory:
        home = Path(directory)
        write_private(home / "auth.json", claimed["auth"])

        def save(release=False):
            try:
                auth = json.loads((home / "auth.json").read_text())
            except (OSError, ValueError):
                # A rejected login may remove the cache. Still release the lease,
                # preserving the original auth error rather than masking it.
                auth = None
            broker.call("save", account_id=identity, lease=claimed["lease"],
                        auth=auth, release=release)

        with Heartbeat(save) as heartbeat:
            try:
                yield home, heartbeat
            finally:
                # Persist rotated tokens even if Codex or repository validation fails.
                save(release=True)


def command(args, *, env=None, timeout=1200, failure_signals=(), stdin=None, allowed_returncodes=(0,)):
    with tempfile.TemporaryFile(mode="w+") as log:
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
                raise WorkerFailure("Command stopped after timeout or loss of its credential lease")
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                continue
        if process.returncode not in allowed_returncodes:
            # Never copy raw Codex/auth/transport output into public Actions logs.
            raise WorkerFailure(f"Command failed: {Path(args[0]).name} (exit {process.returncode})")
        log.seek(0)
        return log.read()


def clean_environment():
    # OIDC/job credentials stay in the parent. Codex receives only its leased auth file.
    return {key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}


def choose_account(broker, config, worker_heartbeat):
    profiles = {"codex": AgentProfile(config["model"], config["effort"])}
    policy = SelectionPolicy(**config["selection"])
    snapshots = []
    identities = broker.call("accounts")["accounts"]
    print(f"Inspecting quota for {len(identities)} enrolled Codex accounts", flush=True)
    for identity in identities:
        try:
            with account_lease(broker, identity) as (home, heartbeat):
                _, limits = inspect_account(home, config["model"], config["effort"])
                snapshots.append(quota_snapshot(identity, config["model"], limits))
                if heartbeat.failed.is_set() or worker_heartbeat.failed.is_set():
                    raise WorkerFailure("Worker lease was lost during quota inspection")
        except HTTPError as exc:
            if exc.code not in {404, 409} or getattr(exc, "clusterfork_action", "claim") != "claim":
                raise
            print(f"Account {identity[:8]} is no longer available", flush=True)
            continue
        except CodexError as exc:
            # Unknown/busy/unavailable accounts never count as unused quota.
            print(f"Account {identity[:8]} unavailable for quota inspection ({type(exc).__name__})", flush=True)
            continue
    return snapshots, profiles, policy


@contextmanager
def run_codex(broker, config, worker_heartbeat, directory):
    # Keep the selected account leased through validation and publication so the
    # final reading covers the full attempt without another worker consuming it.
    broker.call("progress", stage="selecting_account")
    snapshots, profiles, policy = choose_account(broker, config, worker_heartbeat)
    while True:
        selected = select_account(snapshots, profiles, policy, now=time.time())
        if not selected:
            yield None
            return
        snapshots = [row for row in snapshots if row.account_id != selected.account_id]
        ran_agent = False
        try:
            with account_lease(broker, selected.account_id) as (home, heartbeat):
                details, limits = inspect_account(home, config["model"], config["effort"])
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
                    f"Issue URL: {issue['url']}\n\n{issue.get('description') or ''}"
                )
                env = {**clean_environment(), "CODEX_HOME": str(home)}
                ran_agent = True
                try:
                    summary = run_agent(broker, config, home, Path.cwd(), prompt, env,
                                        timeout=config.get("agent_timeout_minutes", 300) * 60,
                                        failure_signals=[heartbeat.failed, worker_heartbeat.failed])
                    yield summary
                    return
                finally:
                    try:
                        if heartbeat.failed.is_set() or worker_heartbeat.failed.is_set():
                            raise WorkerFailure("Credential lease was lost")
                        final = quota_snapshot(selection.account_id, selection.model, read_usage(home),
                                               allow_exhausted=True)
                        broker.call("progress", usage_after=usage_report(final))
                    except Exception:
                        # Keep the execution outcome even if provider usage is
                        # unavailable. The finish comment explicitly says so.
                        print("Final quota reading unavailable; usage change will be reported as unknown", flush=True)
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
def git_auth(token, directory):
    askpass = directory / "git-askpass"
    askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s" "x-access-token";; *) printf "%s" "$CLUSTERFORK_GIT_TOKEN";; esac\n')
    askpass.chmod(0o700)
    yield {**clean_environment(), "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
           "CLUSTERFORK_GIT_TOKEN": token}


def review_readiness(broker, config, worker_heartbeat, directory):
    """Review in Codex's enforced read-only sandbox, using the normal quota pool."""
    token = broker.call("checkout")["token"]
    with git_auth(token, directory) as env:
        command(["git", "fetch", "origin", f"+refs/heads/{config['ref']}:refs/remotes/origin/{config['ref']}"], env=env)
    command(["git", "checkout", "--detach", config["binding"]["context_sha"]])
    if (command(["git", "rev-parse", "HEAD"]).strip() != config["binding"]["context_sha"]
            or command(["git", "status", "--porcelain"]).strip()):
        raise WorkerFailure("Readiness checkout does not match its clean, reviewed context")
    snapshots, profiles, policy = choose_account(broker, config, worker_heartbeat)
    while True:
        selected = select_account(snapshots, profiles, policy, now=time.time())
        if not selected:
            broker.call("finish", status="waiting_for_quota")
            return "readiness_retry"
        snapshots = [row for row in snapshots if row.account_id != selected.account_id]
        try:
            with account_lease(broker, selected.account_id) as (home, heartbeat):
                _, limits = inspect_account(home, config["model"], config["effort"])
                fresh = quota_snapshot(selected.account_id, config["model"], limits)
                if not select_account([fresh], profiles, policy, now=time.time()):
                    continue
                schema, output = directory / "readiness-schema.json", directory / "readiness-result.json"
                schema.write_text(json.dumps(RESULT_SCHEMA))
                command(["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                         "--color", "never", "--sandbox", "read-only", "--model", config["model"],
                         "-c", 'approval_policy="never"',
                         "-c", f'model_reasoning_effort="{config["effort"]}"',
                         "-c", 'cli_auth_credentials_store="file"',
                         "--output-schema", str(schema), "--output-last-message", str(output), "-"],
                        stdin=review_prompt(config["issue"]),
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


def request_auto_merge(config, token, pull_url, dispatch_key, branch, *, expected_sha=None):
    if not config.get("auto_merge", True):
        return
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


def execute(broker, config, heartbeat, directory):
    issue = config["issue"]
    branch = f"clusterfork/{issue['identifier'].lower()}-{broker.key[:8]}"
    token = broker.call("checkout")["token"]
    existing = find_pr(config, token, branch)
    if existing:
        token = broker.call("github")["token"]
        request_auto_merge(config, token, existing, broker.key, branch)
        return "completed", existing
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
    config = {**config, "checkout_sha": command(["git", "rev-parse", f"origin/{config['ref']}"]).strip()}
    if config.get("readiness", {}).get("context_sha", config["checkout_sha"]) != config["checkout_sha"]:
        raise WorkerFailure("Coding checkout changed after readiness approval; request another review")
    with run_codex(broker, config, heartbeat, directory) as summary:
        if summary is None:
            return "waiting_for_quota", None
        broker.call("progress", stage="testing")
        if (command(["git", "rev-parse", "HEAD"]).strip() != original
                or command(["git", "branch", "--show-current"]).strip() != branch):
            raise WorkerFailure("Codex changed the worker's Git history or branch")
        changed = command(["git", "status", "--porcelain"]).strip()
        paths = command(["git", "diff", "--name-only", "HEAD"]).splitlines()
        paths += command(["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
        if any(path.startswith(".github/workflows/") for path in paths):
            raise WorkerFailure("Workflow file changes require a separate Workflows permission")
        print("Running repository validation", flush=True)
        for args in config["validation_commands"]:
            command(list(args), env=clean_environment(), failure_signals=[heartbeat.failed])
        broker.call("progress", stage="publishing")
        token = broker.call("github")["token"]
        if changed:
            command(["git", "add", "-A"])
            command(["git", "-c", "user.name=clusterfork[bot]", "-c",
                     "user.email=clusterfork[bot]@users.noreply.github.com", "commit", "-m",
                     f"{issue['identifier']}: {issue['title'][:180]}"])
        delta = command(["git", "diff", "--name-only", f"origin/{config['ref']}...HEAD"]).strip()
        if not delta:
            return "completed", None
        with git_auth(token, directory) as env:
            command(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], env=env)
        body = (f"Implements [{issue['identifier']}]({issue['url']}).\n\n{summary[:40_000]}\n\n"
                f"Validation: all configured repository checks passed.\n\n"
                f"<!-- clusterfork-dispatch:{broker.key} -->")
        pull = json_request(f"https://api.github.com/repos/{config['repository']}/pulls", token,
                            {"title": f"{issue['identifier']}: {issue['title']}"[:250],
                             "head": branch, "base": config["ref"], "body": body})
        request_auto_merge(config, token, pull["html_url"], broker.key, branch,
                           expected_sha=command(["git", "rev-parse", "HEAD"]).strip())
        return "completed", pull["html_url"]


def main():
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
        with Heartbeat(lambda: broker.call("heartbeat")) as heartbeat, \
             tempfile.TemporaryDirectory(prefix="clusterfork-worker-") as directory:
            if config["phase"] == "readiness":
                status, pull = review_readiness(broker, config, heartbeat, Path(directory)), None
            else:
                status, pull = execute(broker, config, heartbeat, Path(directory))
        if config["phase"] == "development":
            broker.call("finish", status=status, pull_request=pull or "")
        print(f"Worker outcome: {status}" + (f"; {pull}" if pull else ""))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as stream:
                stream.write(f"### Clusterfork Codex {config['phase']} worker\n\nIssue: {config['issue']['identifier']}\n\n"
                             f"Model: `{config['model']}` / `{config['effort']}`\n\nOutcome: {status}\n\n"
                             + (f"Pull request: {pull}\n" if pull else ""))
                if pull:
                    stream.write("\nMerge policy: " + ("automatic squash merge after required checks\n"
                                 if config.get("auto_merge", True) else "manual merge (project override)\n"))
        return 0
    except Exception as exc:
        if started:
            try:
                broker.call("finish", status="failed")
            except Exception:
                pass
        if isinstance(exc, HTTPError):
            detail = f"broker/{getattr(exc, 'clusterfork_action', 'request')} HTTP {exc.code}"
        else:
            detail = str(exc) if isinstance(exc, WorkerFailure) else type(exc).__name__
        print(f"Worker failed: {detail}. No authentication or agent transcripts were logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
