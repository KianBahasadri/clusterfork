"""Adapt saved local profiles to Symphony's shared, quota-based account selector."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.selection import AccountQuota, QuotaWindow, SelectionPolicy, finite_number, select_account
from account_sessions import account_identity, jwt_payload
from account_usage import READERS, UsageError, grok_login, timestamp

EXCLUDED_AUTO_PROFILES = {"kian"}
if "ROTATE_EXCLUDE_PROFILES" in os.environ:
    EXCLUDED_AUTO_PROFILES = {
        name.strip() for name in os.environ["ROTATE_EXCLUDE_PROFILES"].split(",") if name.strip()
    }

CACHE_FILENAMES = {
    "codex": "codex-usage.json",
    "cursor": "cursor-usage.json",
    "claude": "claude-usage.json",
    "grok": "grok-usage.json",
    "opencode": "opencode-usage.json",
    "antigravity": "gemini-usage.json",
}


def selection_config(provider):
    try:
        data = json.loads((ROOT / "automation/controller/projects.json").read_text())
        policy = SelectionPolicy.load(data["agent_selection"])
        row = data["agent_profiles"].get(provider, {"model": "", "effort": ""})
        profile = SimpleNamespace(model=row["model"], effort=row["effort"])
        if not isinstance(profile.model, str) or not isinstance(profile.effort, str):
            raise ValueError("Invalid provider profile")
        return profile, policy
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise UsageError("cannot read Symphony account selection settings; reinstall Clusterfork") from None


def read_credentials(backend, name, provider):
    if provider == "claude":
        source = backend.claude_dir / (".credentials.json." + name)
        return json.loads(source.read_text())
    if provider == "antigravity":
        raw = None
        if name == backend.current_suffix():
            raw = backend._lookup(backend.active_service, backend.active_user)
        if raw is None:
            raw = backend._lookup(backend.profile_service, name)
        if raw is None:
            raise UsageError("saved keyring item is unavailable")
        return json.loads(raw)
    return json.loads((backend.store_dir / ("auth.json." + name)).read_text())


def identity(provider, auth):
    if provider in {"codex", "cursor"}:
        return account_identity(auth, provider)
    if provider == "claude":
        token = auth["claudeAiOauth"]["accessToken"]
    elif provider == "grok":
        login = grok_login(auth)
        token = login.get("email") or login["key"]
    elif provider == "opencode":
        token = auth["opencode-go"]["key"]
    else:
        token = auth["token"]["access_token"]
    if not isinstance(token, str) or not token:
        raise UsageError("missing saved access token")
    subject = jwt_payload(token).get("sub")
    return hashlib.sha256((subject if isinstance(subject, str) and subject else token).encode()).hexdigest()


def measure(provider, auth, account_id, model):
    try:
        windows = READERS[provider](auth, model)
        return AccountQuota(account_id, provider, model, time.time(), windows), None
    except UsageError as error:
        return None, str(error)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, OverflowError):
        return None, "missing or invalid provider quota data"


def measure_logins(provider, logins, account_id, model):
    for name, auth in logins:
        snapshot, error = measure(provider, auth, account_id, model)
        if snapshot is not None:
            return snapshot, None, name
    return None, error, None


def read_cached_snapshots(provider, logins, model, max_age_seconds=300):
    filename = CACHE_FILENAMES.get(provider)
    if not filename:
        return None
    dirs = []
    env = os.environ.get("CONKY_CACHE_DIR") or os.environ.get("CLUSTERFORK_USAGE_CACHE_DIR")
    if env:
        dirs.append(Path(env))
    home = Path.home()
    dirs.extend([
        home / "conky-linear-HUP" / "cache",
        home / "live-wallpaper" / "conky-linear-HUP" / "cache",
    ])
    cache_path = None
    for d in dirs:
        candidate = d / filename
        if candidate.is_file():
            cache_path = candidate
            break
    if cache_path is None:
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
            return None
        cache_by_id = {}
        for row in data["accounts"]:
            if isinstance(row, dict) and row.get("ok") and not row.get("staleCache"):
                aid = str(row.get("accountId") or "")
                if aid:
                    cache_by_id[aid] = row
        now = time.time()
        snapshots = []
        measured_profiles = {}
        for account_id, choices in logins.items():
            cached = cache_by_id.get(account_id)
            if cached is None:
                return None
            observed = cached.get("observedAt")
            if not finite_number(observed) or not (0 <= now - observed <= max_age_seconds):
                return None
            raw_windows = cached.get("windows")
            if not isinstance(raw_windows, list) or not raw_windows:
                return None
            windows = []
            for w in raw_windows:
                if not isinstance(w, dict):
                    continue
                used = w.get("usedPercent")
                if not finite_number(used):
                    continue
                reset = w.get("resetAtEpoch")
                if reset is None and "resetsAt" in w:
                    try:
                        reset = timestamp(w["resetsAt"])
                    except Exception:
                        reset = None
                duration_sec = w.get("windowSeconds")
                duration_min = duration_sec / 60 if finite_number(duration_sec) else None
                label = w.get("label") or ""
                windows.append(QuotaWindow(used, reset, f"{provider}/{label}", duration_min))
            if not windows:
                return None
            snapshots.append(AccountQuota(account_id, provider, model, observed, tuple(windows)))
            measured_profiles[account_id] = choices[0][0]
        if snapshots:
            return snapshots, measured_profiles
    except Exception:
        return None
    return None


def codex_profiles_in_use():
    """Read launch-time account labels from live Codex panes, including detached sessions."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_dead}\t#{pane_current_command}\t#{window_name}"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode:
        return set()
    profiles = set()
    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if (len(fields) == 3 and fields[0] == "0" and fields[1] == "codex"
                and fields[2].startswith("codex - ")):
            profiles.add(fields[2].removeprefix("codex - "))
    return profiles


def choose_profile(backend, names):
    provider = backend.command.removeprefix("rotate-").removesuffix("-cli")
    profile, policy = selection_config(provider)
    current = backend.current_suffix()

    excluded_ids = set()
    for name in list(names):
        if name in EXCLUDED_AUTO_PROFILES:
            try:
                auth = read_credentials(backend, name, provider)
                excluded_ids.add(identity(provider, auth))
            except Exception:
                pass

    logins, aliases = {}, {}
    for name in names:
        if name in EXCLUDED_AUTO_PROFILES:
            continue
        try:
            auth = read_credentials(backend, name, provider)
            account_id = identity(provider, auth)
        except (UsageError, OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            print(f"  {name}: saved credentials are unavailable or unsupported", file=sys.stderr)
            continue
        if account_id in excluded_ids:
            continue
        aliases.setdefault(account_id, []).append(name)
        choices = logins.setdefault(account_id, [])
        if name == current:
            choices[:] = [(alias, saved) for alias, saved in choices if saved != auth]
            choices.insert(0, (name, auth))
        elif not any(saved == auth for _, saved in choices):
            choices.append((name, auth))

    if not logins:
        raise UsageError(f"no eligible saved profiles (excluded: {', '.join(sorted(EXCLUDED_AUTO_PROFILES))})")

    if provider == "codex":
        busy_profiles = codex_profiles_in_use()
        busy_ids = {account_id for account_id, names in aliases.items() if busy_profiles.intersection(names)}
        if busy_ids:
            busy_names = sorted(name for account_id in busy_ids for name in aliases[account_id])
            print(f"{backend.command}: skipping accounts in open Codex sessions: {', '.join(busy_names)}", flush=True)
            logins = {account_id: choices for account_id, choices in logins.items() if account_id not in busy_ids}
            if not logins:
                raise UsageError("all eligible saved Codex accounts are already in use")

    now = time.time()
    cached = read_cached_snapshots(provider, logins, profile.model)
    if cached is not None:
        cached_snapshots, cached_profiles = cached
        cached_policy = SelectionPolicy(policy.min_remaining_percent, max(policy.max_snapshot_age_seconds, 300))
        selected = select_account(cached_snapshots, {provider: profile}, cached_policy, now=now)
        if selected is not None:
            print(f"{backend.command}: checking quota from cache for {len(logins)} saved accounts", flush=True)
            print(f"  quota: at least {selected.remaining_percent:g}% remaining")
            return cached_profiles[selected.account_id]

    print(f"{backend.command}: checking quota for {len(logins)} saved accounts", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {account_id: pool.submit(measure_logins, provider, choices, account_id, profile.model)
                   for account_id, choices in logins.items()}
        snapshots = []
        measured_profiles = {}
        for account_id, future in pending.items():
            snapshot, error, name = future.result()
            if snapshot is not None:
                snapshots.append(snapshot)
                measured_profiles[account_id] = name
            else:
                print(f"  {', '.join(aliases[account_id])}: {error}", file=sys.stderr)
    selected = select_account(snapshots, {provider: profile}, policy, now=time.time())
    if selected is None:
        reserve_scope = "the 5h window" if provider == "codex" else "every window"
        raise UsageError(f"no account has fresh quota with at least {policy.min_remaining_percent:g}% remaining in {reserve_scope}")
    print(f"  quota: at least {selected.remaining_percent:g}% remaining")
    return measured_profiles[selected.account_id]
