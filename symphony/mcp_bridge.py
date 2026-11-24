"""Minimal stdlib stdio MCP server bridging Antigravity CLI tool calls to Symphony."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .antigravity import TOOLS


def _call_symphony(name, arguments):
    url = os.environ.get("SYMPHONY_INTERNAL_URL", "http://127.0.0.1:8080/internal/tool")
    token = os.environ.get("SYMPHONY_INTERNAL_TOKEN", "")
    payload = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=55) as response:
            body = json.loads(response.read().decode("utf-8"))
        return bool(body.get("ok")), str(body.get("text") or "")
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error") or error.reason
        except (ValueError, OSError):
            detail = str(error)
        return False, str(detail)
    except (URLError, OSError, ValueError) as error:
        return False, str(error)


def _send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_message(message):
    if not isinstance(message, dict):
        return None
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "symphony", "version": "3.0.0"},
            },
        }
    if method in {"notifications/initialized", "notifications/cancelled"} or msg_id is None:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "Invalid tool call arguments."}],
                },
            }
        ok, text = _call_symphony(name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "isError": not ok,
                "content": [{"type": "text", "text": text[:60000]}],
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Unsupported method: {method}"},
    }


def main():
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle_message(message)
        if reply is not None:
            _send(reply)


if __name__ == "__main__":
    main()
