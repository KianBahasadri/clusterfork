"""Headless Codex app-server transport and account inspection; never log auth."""

from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
import queue
import signal
import shutil
import subprocess
import tempfile
import threading
import time


class CodexError(RuntimeError):
    pass


class CodexRejected(CodexError):
    """An explicit RPC rejection, as distinct from an uncertain transport failure."""


class CodexUsageLimit(CodexError):
    """A failed coding turn with the provider's explicit usage-limit signal."""

    def __init__(self, thread_id):
        super().__init__("Codex account usage limit reached")
        self.thread_id = thread_id


def copy_sessions(source, destination):
    """Carry only rollout history between private homes, never credential caches."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or destination.is_symlink():
        raise CodexError("Codex session storage must not be a symlink")
    if not source.exists():
        return
    for path in source.rglob("*"):
        if path.is_symlink():
            raise CodexError("Codex session files must not be symlinks")
        if path.is_file() and path.name.startswith("rollout-") and path.suffix == ".jsonl":
            relative = path.relative_to(source)
            target = destination / relative
            if target.is_symlink() or any((destination / parent).is_symlink() for parent in relative.parents):
                raise CodexError("Codex session destination must not be a symlink")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target)
            target.chmod(0o600)


class AppServer:
    def __init__(self, home, *, binary="codex", timeout=30, env=None, cwd=None, config=()):
        self.timeout, self.sequence = timeout, 0
        env = {**(os.environ if env is None else env), "CODEX_HOME": str(home)}
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            env.pop(key, None)
        args = [binary, "app-server"]
        for value in config:
            args.extend(["-c", value])
        self.process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env, cwd=cwd or home, start_new_session=True)
        self.messages = queue.Queue()
        self.notifications = deque()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "clusterfork", "version": "1.0"}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put(None)

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def call(self, method, params=None):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise CodexError("Codex app-server request timed out") from None
            if message is None:
                raise CodexError("Codex app-server stopped")
            if "method" in message:
                self.notifications.append(message)
                continue
            if message.get("id") != self.sequence:
                continue
            if "error" in message:
                raise CodexRejected(f"Codex rejected {method}")
            return message["result"]

    def next_event(self, timeout=0):
        if self.notifications:
            return self.notifications.popleft()
        while True:
            try:
                message = self.messages.get(timeout=timeout)
            except queue.Empty:
                return None
            if message is None:
                raise CodexError("Codex app-server stopped")
            if "method" in message:
                return message

    def close(self):
        # Tools are in the same process group; lease loss must stop them too.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".clusterfork-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inspect_account(home, model, effort):
    server = AppServer(home)
    try:
        account = server.call("account/read", {"refreshToken": True}).get("account") or {}
        models, cursor = [], None
        while True:
            params = {"limit": 100, "includeHidden": True}
            if cursor:
                params["cursor"] = cursor
            result = server.call("model/list", params)
            models.extend(result["data"])
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise CodexError("Codex model pagination did not advance")
            cursor = next_cursor
        match = next((row for row in models if row.get("model") == model), None)
        if not match or effort not in {x["reasoningEffort"] for x in match["supportedReasoningEfforts"]}:
            raise CodexError("Pinned model/effort is unavailable for this account")
        limits = server.call("account/rateLimits/read")
        return {**match, "account": {key: account.get(key) for key in ("email", "planType")}}, limits
    finally:
        server.close()


def read_usage(home, *, timeout=30):
    server = AppServer(home, timeout=timeout)
    try:
        server.call("account/read", {"refreshToken": True})
        return server.call("account/rateLimits/read")
    finally:
        server.close()


def quota_snapshot(identity, model, limits, *, allow_exhausted=False):
    from common.selection import AccountQuota, QuotaWindow
    if limits.get("ordinaryUsageAllowed") is False:
        raise CodexError("Codex account usage is unavailable")
    buckets = limits.get("rateLimitsByLimitId") or {"codex": limits.get("rateLimits")}
    applicable = [(key, row) for key, row in buckets.items() if row and
                  (key == "codex" or key == model or row.get("normalModelSlug") == model)]
    if not applicable:
        raise CodexError("No applicable Codex quota data")
    windows = []
    for bucket_id, bucket in applicable:
        if not allow_exhausted and (bucket.get("spendControlReached") or bucket.get("rateLimitReachedType")):
            raise CodexError("Codex account limit was reached")
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if window:
                windows.append(QuotaWindow(window.get("usedPercent"), window.get("resetsAt"),
                                           f"{bucket_id}/{key}", window.get("windowDurationMins")))
    return AccountQuota(identity, "codex", model, time.time(), tuple(windows))
