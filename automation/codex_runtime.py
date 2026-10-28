"""Headless Codex account inspection; never print provider responses or auth."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time


class CodexError(RuntimeError):
    pass


class AppServer:
    def __init__(self, home, *, binary="codex", timeout=30):
        self.timeout, self.sequence = timeout, 0
        env = {**os.environ, "CODEX_HOME": str(home)}
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            env.pop(key, None)
        self.process = subprocess.Popen(
            [binary, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env, cwd=home)
        self.messages = queue.Queue()
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
                raise CodexError("Codex account inspection timed out") from None
            if message is None:
                raise CodexError("Codex account inspection stopped")
            if message.get("id") != self.sequence:
                continue
            if "error" in message:
                raise CodexError(f"Codex rejected {method} during account inspection")
            return message["result"]

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
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
        server.call("account/read", {"refreshToken": True})
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
        return match, limits
    finally:
        server.close()


def quota_snapshot(identity, model, limits):
    from quota_selection import AccountQuota, QuotaWindow
    if limits.get("ordinaryUsageAllowed") is False:
        raise CodexError("Codex account usage is unavailable")
    buckets = limits.get("rateLimitsByLimitId") or {"codex": limits.get("rateLimits")}
    applicable = [row for key, row in buckets.items() if row and
                  (key == "codex" or key == model or row.get("normalModelSlug") == model)]
    if not applicable:
        raise CodexError("No applicable Codex quota data")
    windows = []
    for bucket in applicable:
        if bucket.get("spendControlReached") or bucket.get("rateLimitReachedType"):
            raise CodexError("Codex account limit was reached")
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if window:
                windows.append(QuotaWindow(window.get("usedPercent"), window.get("resetsAt")))
    return AccountQuota(identity, "codex", model, time.time(), tuple(windows))
