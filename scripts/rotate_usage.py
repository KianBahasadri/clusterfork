"""Adapt saved local profiles to Symphony's shared, quota-based account selector."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.selection import AccountQuota, SelectionPolicy, select_account
from account_sessions import account_identity, jwt_payload
from account_usage import READERS, UsageError, grok_login


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


def choose_profile(backend, names):
    provider = backend.command.removeprefix("rotate-").removesuffix("-cli")
    profile, policy = selection_config(provider)
    current = backend.current_suffix()
    logins, aliases = {}, {}
    for name in names:
        try:
            auth = read_credentials(backend, name, provider)
            account_id = identity(provider, auth)
        except (UsageError, OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            print(f"  {name}: saved credentials are unavailable or unsupported", file=sys.stderr)
            continue
        aliases.setdefault(account_id, []).append(name)
        choices = logins.setdefault(account_id, [])
        if name == current:
            choices[:] = [(alias, saved) for alias, saved in choices if saved != auth]
            choices.insert(0, (name, auth))
        elif not any(saved == auth for _, saved in choices):
            choices.append((name, auth))
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
        raise UsageError(f"no account has fresh quota with at least {policy.min_remaining_percent:g}% remaining in every window")
    print(f"  quota: at least {selected.remaining_percent:g}% remaining")
    return measured_profiles[selected.account_id]
