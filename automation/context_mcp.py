#!/usr/bin/env python3
"""Credential-free stdio MCP facade for the trusted worker's local read socket."""

import json
from pathlib import Path
import socket
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.context import CONTEXT_PROMPT, TOOL, ContextError, tool_definition, validate_request


def read(path, arguments):
    validate_request(arguments)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(18)
        connection.connect(path)
        connection.sendall(json.dumps(arguments).encode() + b"\n")
        with connection.makefile("rb") as stream:
            raw = stream.readline(128001)
        if len(raw) > 128000 or not raw.endswith(b"\n"):
            raise ContextError("incomplete")
        return json.loads(raw)


def dispatch(path, message):
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "clusterfork-context", "version": "1"}, "instructions": CONTEXT_PROMPT}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [tool_definition()]}
    if method == "tools/call":
        try:
            if params.get("name") != TOOL:
                raise ContextError("denied")
            result = read(path, params.get("arguments"))
        except (ContextError, OSError, ValueError) as exc:
            result = {"status": exc.status if isinstance(exc, ContextError) else "unavailable", "complete": False}
        return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": result.get("status") != "ok"}
    raise ContextError("unsupported_method")


def main():
    while True:
        raw = sys.stdin.buffer.readline(16001)
        if not raw:
            break
        if len(raw) > 16000:
            return 1
        message = {}
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                return 1
            if "id" not in message:
                continue
            result = {"result": dispatch(sys.argv[1], message)}
        except (ValueError, TypeError, KeyError):
            result = {"error": {"code": -32602, "message": "Unsupported context request"}}
        print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), **result}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
