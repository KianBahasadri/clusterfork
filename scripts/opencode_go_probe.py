#!/usr/bin/env python3
"""Probe which OpenCode Go models can drive a Claude Code agent loop.

The only test that matters is the tool-result round trip: a model can look
healthy (emits `tool_use`, streams fine) and still return an empty reply when
the tool output is fed back, which stalls the agent loop with no error. This
script drives that round trip over the Anthropic `/v1/messages` route the same
way Claude Code does — streaming — and reports per-model pass/fail.

    python scripts/opencode_go_probe.py                     # sweep whole catalog
    python scripts/opencode_go_probe.py deepseek-v4-flash   # one model
    python scripts/opencode_go_probe.py deepseek-v4-flash -n 10   # repeat it

Exits non-zero if any probed model fails, so it works as a regression check.
See docs/opencode-go.md for what the results mean.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go")
AUTH = os.path.expanduser("~/.local/share/opencode/auth.json")
CACHE = os.path.expanduser("~/.cache/opencode/models.json")

# Cloudflare 403s a default urllib user agent with a bare `error code: 1010`
# body, before the request ever reaches the gateway. Look like a real client.
HEADERS = {
    "content-type": "application/json",
    "anthropic-version": "2023-06-01",
    "user-agent": "claude-cli/2.1.223 (external, cli)",
    "accept": "text/event-stream",
}

SYSTEM = "You are a helpful coding assistant with file access. Use the tools provided."
PROMPT = "Read the file /etc/hostname and tell me exactly what it contains."
SENTINEL = "clusterfork-workstation-42"
TOOL = {
    "name": "read_file",
    "description": "Read a file from disk and return its contents.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to read"}},
        "required": ["path"],
    },
}


def api_key():
    key = os.environ.get("OPENCODE_API_KEY")
    if key:
        return key
    with open(AUTH) as fh:
        return json.load(fh)["opencode-go"]["key"]


def catalog():
    with open(CACHE) as fh:
        return sorted(json.load(fh)["opencode-go"]["models"])


def stream(key, body):
    """POST /v1/messages with stream:true; return collected text and tool calls."""
    req = urllib.request.Request(
        f"{BASE}/v1/messages",
        data=json.dumps(dict(body, stream=True)).encode(),
        headers=dict(HEADERS, **{"x-api-key": key}),
    )
    events, text, tools, cur = [], "", [], {}
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode().rstrip("\n")
                if line.startswith("event: "):
                    events.append(line[7:])
                elif line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                    except ValueError:
                        continue
                    kind = evt.get("type")
                    if kind == "content_block_start":
                        block = evt.get("content_block", {})
                        if block.get("type") == "tool_use":
                            cur = {"id": block.get("id"), "name": block.get("name"), "json": ""}
                    elif kind == "content_block_delta":
                        delta = evt.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text += delta.get("text", "")
                        elif delta.get("type") == "input_json_delta":
                            cur["json"] = cur.get("json", "") + delta.get("partial_json", "")
                    elif kind == "content_block_stop" and cur:
                        tools.append(cur)
                        cur = {}
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode()[:120]}"}
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        return {"error": repr(exc)}
    return {"events": Counter(events), "text": text, "tools": tools}


def probe(key, model):
    """Return (model, status, detail). Status "OK" means the loop closed."""
    first = stream(key, {
        "model": model, "max_tokens": 1024, "system": SYSTEM,
        "messages": [{"role": "user", "content": PROMPT}], "tools": [TOOL],
    })
    if "error" in first:
        return model, "FAIL-turn1", first["error"]
    if not first["tools"]:
        return model, "FAIL-no-tool_use", repr(first["text"][:80])

    call = first["tools"][-1]
    try:
        args = json.loads(call["json"] or "{}")
    except ValueError:
        args = {}

    second = stream(key, {
        "model": model, "max_tokens": 1024, "system": SYSTEM, "tools": [TOOL],
        "messages": [
            {"role": "user", "content": PROMPT},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": args}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call["id"], "content": SENTINEL}]},
        ],
    })
    if "error" in second:
        return model, "FAIL-turn2", second["error"]
    if not second["text"].strip():
        # The failure this script exists to catch: no error, just nothing.
        return model, "FAIL-empty-reply", "silent stall on tool_result"
    if SENTINEL not in second["text"]:
        return model, "WARN-no-echo", repr(second["text"][:80])
    return model, "OK", f"{len(second['text'])} chars"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="*", help="models to probe (default: whole catalog)")
    ap.add_argument("-n", "--repeat", type=int, default=1, help="probe each model N times")
    ap.add_argument("-j", "--jobs", type=int, default=12, help="parallel requests")
    args = ap.parse_args()

    key = api_key()
    targets = (args.models or catalog()) * args.repeat
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda m: probe(key, m), targets))

    for model, status, detail in sorted(results, key=lambda r: (r[1] != "OK", r[0])):
        print(f"{status:20} {model:20} {detail}")

    failed = [r for r in results if r[1] != "OK"]
    print(f"\n{len(results) - len(failed)}/{len(results)} OK", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
