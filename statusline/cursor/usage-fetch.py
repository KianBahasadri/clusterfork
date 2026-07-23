#!/usr/bin/env python3
"""Maintain ~/.cursor/.usage-cache.json with Cursor auto/api usage %.

Run in the background from statusline.sh on each render. Throttled by TTL.
"""
import fcntl
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
CURSOR_HOME = Path(os.environ.get("CURSOR_HOME", HOME / ".config" / "cursor")).expanduser()
DEFAULT_AUTH = "auth.json"
API_BASE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService"
USER_AGENT = "cursor/3.7.21"
CACHE = HOME / ".cursor" / ".usage-cache.json"
LOCK = HOME / ".cursor" / ".usage-cache.lock"
CONKY_CACHE = HOME / "live-wallpaper" / "conky-linear-HUP" / "cache" / "cursor-usage.json"
TTL = int(os.environ.get("CURSOR_USAGE_TTL", "120"))


def read_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def write_cache(data):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE)


def fresh(cache):
    auto = (cache.get("auto") or {}).get("used_percentage")
    api = (cache.get("api") or {}).get("used_percentage")
    return (auto is not None or api is not None) and time.time() - cache.get("fetched_at", 0) < TTL


def default_auth_path():
    return CURSOR_HOME / DEFAULT_AUTH


def selected_auth_path():
    configured = os.environ.get("CURSOR_AUTH_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    default = default_auth_path()
    if default.is_symlink() or default.is_file():
        return default
    suffixed = sorted(p for p in default.parent.glob(f"{DEFAULT_AUTH}.*") if p.is_file())
    for path in suffixed:
        try:
            if default.resolve() == path.resolve():
                return path
        except OSError:
            if default == path:
                return path
    return default


def read_token(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    token = raw.get("accessToken", "")
    if not token:
        raise RuntimeError(f"no accessToken in {path}")
    return token


def fetch_usage(token):
    body = b"{}"
    req = urllib.request.Request(f"{API_BASE_URL}/GetCurrentPeriodUsage", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Connect-Protocol-Version", "1")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    plan_usage = payload.get("planUsage") or {}
    out = {}
    if plan_usage.get("autoPercentUsed") is not None:
        out["auto"] = {"used_percentage": float(plan_usage["autoPercentUsed"])}
    if plan_usage.get("apiPercentUsed") is not None:
        out["api"] = {"used_percentage": float(plan_usage["apiPercentUsed"])}
    return out


def harvest_from_conky_cache():
    try:
        raw = json.loads(CONKY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    updated = raw.get("updatedAt")
    if not updated:
        return {}
    try:
        from datetime import datetime

        ts = datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
        if time.time() - ts > TTL:
            return {}
    except Exception:
        return {}

    accounts = raw.get("accounts") or []
    selected = next((a for a in accounts if a.get("isSelected")), accounts[0] if accounts else None)
    if not selected or not selected.get("ok"):
        return {}

    out = {}
    for window in selected.get("windows") or []:
        label = window.get("label")
        pct = window.get("usedPercent")
        if label in ("auto", "api") and pct is not None:
            out[label] = {"used_percentage": float(pct)}
    return out


def main():
    cache = read_cache()
    if fresh(cache):
        return

    try:
        lock = open(LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return

    try:
        if fresh(read_cache()):
            return

        harvested = harvest_from_conky_cache()
        if harvested:
            harvested["fetched_at"] = int(time.time())
            harvested["source"] = "conky-cache"
            write_cache(harvested)
            return

        token = read_token(selected_auth_path())
        data = fetch_usage(token)
        if data:
            data["fetched_at"] = int(time.time())
            data["source"] = "api"
            write_cache(data)
    except Exception:
        pass
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
