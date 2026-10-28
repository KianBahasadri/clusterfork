#!/usr/bin/env python3
"""Run one Linear issue on an ephemeral GitHub runner using leased Codex auth."""

from __future__ import annotations

from contextlib import contextmanager
import base64
import json
import os
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
from codex_runtime import CodexError, inspect_account, quota_snapshot, write_private
from dispatch_core import AgentProfile
from quota_selection import SelectionPolicy, select_account


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
            broker.call("save", account_id=identity, lease=claimed["lease"],
                        auth=json.loads((home / "auth.json").read_text()), release=release)

        with Heartbeat(save) as heartbeat:
            try:
                yield home, heartbeat
            finally:
                # Persist rotated tokens even if Codex or repository validation fails.
                save(release=True)


def command(args, *, env=None, timeout=1200, failure_signals=(), stdin=None):
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
        if process.returncode:
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
        except (HTTPError, CodexError) as exc:
            # Unknown/busy/unavailable accounts never count as unused quota.
            print(f"Account {identity[:8]} unavailable for quota inspection ({type(exc).__name__})", flush=True)
            continue
    return snapshots, profiles, policy


def run_codex(broker, config, worker_heartbeat, directory):
    snapshots, profiles, policy = choose_account(broker, config, worker_heartbeat)
    while True:
        selected = select_account(snapshots, profiles, policy, now=time.time())
        if not selected:
            return None
        snapshots = [row for row in snapshots if row.account_id != selected.account_id]
        ran_agent = False
        try:
            with account_lease(broker, selected.account_id) as (home, heartbeat):
                _, limits = inspect_account(home, config["model"], config["effort"])
                fresh = quota_snapshot(selected.account_id, config["model"], limits)
                selection = select_account([fresh], profiles, policy, now=time.time())
                if not selection:
                    continue
                print(f"Running {selection.model} ({selection.effort}); minimum quota headroom {selection.remaining_percent:g}%", flush=True)
                issue = config["issue"]
                prompt = (
                    f"Implement Linear issue {issue['identifier']}: {issue['title']}\n"
                    f"Issue URL: {issue['url']}\n\n{issue.get('description') or ''}\n\n"
                    "Read and follow AGENTS.md and the repository docs. Make the requested change, "
                    "run appropriate tests, and update the owning documentation when behavior changes. "
                    "Do not commit, push, open a PR, change authentication, or deploy anything; "
                    "the worker publishes the result. Work as one agent without spawning subagents. "
                    "Finish with a concise description of changes and validation."
                )
                env = {**clean_environment(), "CODEX_HOME": str(home)}
                summary = directory / "summary.md"
                ran_agent = True
                command(["codex", "exec", "--ignore-user-config", "--ephemeral", "--color", "never",
                         "--dangerously-bypass-approvals-and-sandbox", "--model", selection.model,
                         "-c", f'model_reasoning_effort="{selection.effort}"',
                         "-c", 'cli_auth_credentials_store="file"',
                         "--output-last-message", str(summary), "-"],
                        stdin=prompt, env=env, timeout=1800,
                        failure_signals=[heartbeat.failed, worker_heartbeat.failed])
                return summary.read_text() if summary.exists() else "Codex completed the issue."
        except HTTPError as exc:
            if exc.code == 409 and not ran_agent:
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


def find_pr(config, token, branch):
    repository = config["repository"]
    query = urlencode({"state": "open", "head": f"{repository.split('/')[0]}:{branch}", "base": config["ref"]})
    pulls = json_request(f"https://api.github.com/repos/{repository}/pulls?{query}", token)
    return pulls[0]["html_url"] if pulls else None


def execute(broker, config, heartbeat, directory):
    issue = config["issue"]
    branch = f"clusterfork/{issue['identifier'].lower()}-{broker.key[:8]}"
    token = broker.call("github")["token"]
    existing = find_pr(config, token, branch)
    if existing:
        return "completed", existing
    with git_auth(token, directory) as env:
        # Recover a branch pushed by a previous attempt that stopped before PR creation.
        refs = command(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], env=env)
        if refs.strip():
            command(["git", "fetch", "origin", f"refs/heads/{branch}"], env=env)
            command(["git", "checkout", "-b", branch, "FETCH_HEAD"])
        else:
            command(["git", "checkout", "-b", branch])
    original = command(["git", "rev-parse", "HEAD"]).strip()
    summary = run_codex(broker, config, heartbeat, directory)
    if summary is None:
        return "waiting_for_quota", None
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
    return "completed", pull["html_url"]


def main():
    broker = Broker(os.environ["CLUSTERFORK_BROKER_URL"], os.environ["DISPATCH_KEY"])
    started = False
    try:
        config = broker.call("start")
        started = True
        with Heartbeat(lambda: broker.call("heartbeat")) as heartbeat, \
             tempfile.TemporaryDirectory(prefix="clusterfork-worker-") as directory:
            status, pull = execute(broker, config, heartbeat, Path(directory))
        broker.call("finish", status=status, pull_request=pull or "")
        print(f"Worker outcome: {status}" + (f"; {pull}" if pull else ""))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as stream:
                stream.write(f"### Clusterfork Codex worker\n\nIssue: {config['issue']['identifier']}\n\n"
                             f"Model: `{config['model']}` / `{config['effort']}`\n\nOutcome: {status}\n\n"
                             + (f"Pull request: {pull}\n" if pull else ""))
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
