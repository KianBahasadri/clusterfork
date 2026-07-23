#!/usr/bin/env python3
"""Maintain ~/.claude/.usage-cache.json with Claude 5h/weekly usage %.

Run (in the background) by statusline-command.sh on every render, with the
statusline JSON on stdin.

- Fast mode OFF: the JSON already has .rate_limits -> harvest it for free,
  no API call.
- Fast mode ON: Claude Code omits .rate_limits, so make an independent
  "quota_check" request (max_tokens:1) and read the
  anthropic-ratelimit-unified-5h/7d-* response headers. This request is a
  plain request, so it returns the headers regardless of the session's fast
  mode setting. Throttled by TTL to limit API usage.

On any failure the previous cache is left untouched. Credentials are only
read, never written.
"""
import fcntl
import json
import os
import select
import sys
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
CRED = os.path.join(HOME, ".claude", ".credentials.json")
CACHE = os.path.join(HOME, ".claude", ".usage-cache.json")
LOCK = os.path.join(HOME, ".claude", ".usage-cache.lock")

TTL = int(os.environ.get("CLAUDE_USAGE_TTL", "120"))  # min seconds between API fetches
MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SYSTEM_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."


def read_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def write_cache(d):
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, CACHE)


def read_stdin_json():
    if sys.stdin.isatty():
        return None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0.3)
        if not ready:
            return None
        raw = sys.stdin.read().strip()
        return json.loads(raw) if raw else None
    except Exception:
        return None


def harvest_from_statusline(payload):
    """Pull rate_limits straight out of the statusline JSON (no API call)."""
    rl = (payload or {}).get("rate_limits") or {}
    out = {}
    for src, dst in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
        w = rl.get(src)
        if isinstance(w, dict) and w.get("used_percentage") is not None:
            out[dst] = {
                "used_percentage": float(w["used_percentage"]),
                "resets_at": w.get("resets_at"),
            }
    return out


def fetch_from_api():
    """Independent quota_check request; parse unified rate-limit headers."""
    with open(CRED) as f:
        tok = json.load(f)["claudeAiOauth"]["accessToken"]
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1,
        "system": [{"type": "text", "text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": "quota"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST")
    req.add_header("authorization", f"Bearer {tok}")
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("anthropic-beta", "oauth-2025-04-20")
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", "claude-cli/2.1.162 (external, cli)")
    try:
        h = urllib.request.urlopen(req, timeout=20).headers
    except urllib.error.HTTPError as e:
        h = e.headers  # rate-limit headers are present even on 429/4xx

    def util(window):
        v = h.get(f"anthropic-ratelimit-unified-{window}-utilization")
        return float(v) if v not in (None, "") else None

    def reset(window):
        v = h.get(f"anthropic-ratelimit-unified-{window}-reset")
        return int(v) if v not in (None, "") else None

    out = {}
    if util("5h") is not None:
        out["five_hour"] = {"used_percentage": util("5h") * 100, "resets_at": reset("5h")}
    if util("7d") is not None:
        out["seven_day"] = {"used_percentage": util("7d") * 100, "resets_at": reset("7d")}
    return out


def fresh(cache):
    has_data = cache.get("five_hour") or cache.get("seven_day")
    return has_data and (time.time() - cache.get("fetched_at", 0) < TTL)


def main():
    harvested = harvest_from_statusline(read_stdin_json())
    if harvested:
        cache = read_cache()
        cache.update(harvested)
        cache["fetched_at"] = int(time.time())
        cache["source"] = "statusline"
        write_cache(cache)
        return

    # Fast mode (or no rate_limits in payload): refresh via API if stale.
    if fresh(read_cache()):
        return
    try:
        lock = open(LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return  # another fetch is already running
    try:
        if fresh(read_cache()):
            return
        data = fetch_from_api()
        if data:
            data["fetched_at"] = int(time.time())
            data["source"] = "api"
            write_cache(data)
    except Exception:
        pass  # keep the previous cache on any failure
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
