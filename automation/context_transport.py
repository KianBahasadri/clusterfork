"""A local, task-bound read proxy. OIDC and service auth stay in the worker."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socketserver
import sys
import threading
import time

from common.context import CONTEXT_PROTOCOL, SERVER, TOOL, ContextError, validate_request


class ContextBridge:
    def __init__(self, broker, home, failure_signals=(), *, deadline):
        self.broker, self.home = broker, Path(home)
        self.failure_signals, self.deadline = failure_signals, deadline
        self.path = self.home / "context.sock"
        self.server = None

    def stopped(self):
        return time.monotonic() >= self.deadline or any(event.is_set() for event in self.failure_signals)

    def read(self, value):
        if self.stopped():
            return {"status": "canceled", "complete": False}
        try:
            validate_request(value)
            result = self.broker.call("context", request=value)
            if self.stopped():
                return {"status": "canceled", "complete": False}
            return result
        except ContextError as exc:
            return {"status": exc.status, "complete": False}
        except Exception:
            return {"status": "unavailable", "complete": False}

    def __enter__(self):
        bridge = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(18)
                try:
                    raw = self.rfile.readline(16001)
                    if len(raw) > 16000 or not raw.endswith(b"\n"):
                        result = {"status": "invalid_request", "complete": False}
                    else:
                        result = bridge.read(json.loads(raw))
                    self.wfile.write(json.dumps(result).encode() + b"\n")
                except (OSError, ValueError):
                    pass

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
            request_queue_size = 4

        self.server = Server(str(self.path), Handler)
        os.chmod(self.path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=1)
        self.path.unlink(missing_ok=True)

    def stdio(self):
        return {"command": sys.executable,
                "args": ["-I", "-B", str(Path(__file__).with_name("context_mcp.py").resolve()), str(self.path)]}

    def codex_config(self):
        server = {**self.stdio(), "required": True, "enabled_tools": [TOOL],
                  "startup_timeout_sec": 10, "tool_timeout_sec": 20}
        # JSON arrays/strings/bools are also TOML values in this inline table.
        table = ", ".join(f"{k}={json.dumps(v)}" for k, v in server.items())
        return (f"mcp_servers.{SERVER}={{ {table} }}",)

    def cursor_servers(self):
        return [{"name": SERVER, **self.stdio(), "env": []}]


@contextmanager
def agent_context(broker, config, home, failure_signals=(), *, deadline):
    if config.get("context_capability") == CONTEXT_PROTOCOL:
        with ContextBridge(broker, home, failure_signals, deadline=deadline) as bridge:
            yield bridge
    else:
        yield None


def cursor_context_call(update):
    """ACP includes the resolved provider/tool identity in rawInput."""
    value = update.get("rawInput") or {}
    return isinstance(value, dict) and value.get("providerIdentifier") == SERVER and value.get("toolName") == TOOL
