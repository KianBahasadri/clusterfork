#!/usr/bin/env python3
"""Manage cloud ownership, register independent local logins, or read account snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_runtime import write_private
from rotate_auth import (CLOUD_DIRECTORY, REMOTE_MANIFEST, SUFFIX_RE, load_remote_profiles,
                         cloud_enrolled, profile_is_local)
from common.accounts import WorkerError, account_identity, login_fingerprints, login_id
from account_client import AccountClient

MANIFEST = REMOTE_MANIFEST


def connect(provider="codex"):
    return AccountClient(provider)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, auth):
    """Publish without replacing a login created concurrently or following links."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".local-login-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(auth, stream)
        os.link(temporary, path)
    except FileExistsError:
        raise WorkerError("Destination already exists; refusing to overwrite a login") from None
    finally:
        os.unlink(temporary)


def saved_profiles(store):
    return {p.name[len("auth.json."):]: p for p in store.glob("auth.json.*")
            if p.is_file() and not p.is_symlink() and p.name != "auth.json.lock"}


def availability(store, provider):
    """Strictly read-only: no migration, network, refresh, or credential output."""
    manifest = load_remote_profiles(store, migrate=False)
    profiles = saved_profiles(store)
    current = store / "current"
    selected = Path(os.readlink(current)).name.removeprefix("auth.json.") if current.is_symlink() else None
    rows = {}
    for name in sorted(set(manifest) | set(profiles)):
        record = manifest.get(name)
        try:
            identity = record["account_id"] if record else account_identity(json.loads(profiles[name].read_text()), provider)
        except (OSError, ValueError, WorkerError):
            continue
        row = rows.setdefault(identity, {"provider": provider, "account_id": identity, "aliases": [],
                                        "cloud_available": False, "local_available": False,
                                        "local_profiles": [], "selected_local_profile": None})
        row["aliases"].append(name)
        row["cloud_available"] |= bool(record and cloud_enrolled(record))
        if profile_is_local(store, name, manifest, provider):
            row["local_profiles"].append(name)
            row["local_available"] = True
            if selected == name:
                row["selected_local_profile"] = name
    return {"schema_version": 1, "generated_at": time.time(), "provider": provider,
            "local_scope": "host", "accounts": [rows[key] for key in sorted(rows)]}


def consumer_snapshot(store, provider, client):
    local = availability(store, provider)
    try:
        cloud = client.snapshot()
        cloud_error = None
    except WorkerError as error:
        cloud = {"accounts": []}
        cloud_error = "operator_authentication_unavailable" if error.status in {401, 403} else "controller_unavailable"
    rows = {row["account_id"]: row for row in cloud["accounts"]}
    for row in rows.values():
        row.update(local_available=False, local_profiles=[], selected_local_profile=None)
    for row in local["accounts"]:
        identity = row["account_id"]
        if identity in rows:
            merged = rows[identity]
            merged["aliases"] = sorted(set(merged["aliases"]) | set(row["aliases"]))
            merged.update({key: row[key] for key in ("local_available", "local_profiles", "selected_local_profile")})
        else:
            rows[identity] = {**row, "cloud_state": "unknown" if row["cloud_available"] else None,
                              "usage": {"observed_at": None, "attempted_at": None, "age_seconds": None,
                                        "max_age_seconds": 300, "freshness": "unknown",
                                        "error": cloud_error, "windows": []}}
    return {**local, "cloud_error": cloud_error, "accounts": [rows[key] for key in sorted(rows)]}


def register_local(args, manifest, client, name):
    if not args.fresh_login or args.auth_file is None or args.local_profile is None:
        raise WorkerError("register-local requires --fresh-login, --auth-file and a separate --local-profile")
    target = args.local_profile
    if not SUFFIX_RE.fullmatch(target) or target == "lock" or target == name:
        raise WorkerError("Choose a separate valid local profile name")
    source = args.auth_file.expanduser()
    if source.is_symlink() or not source.is_file() or source.resolve().is_relative_to(args.store.resolve()):
        raise WorkerError("Fresh login must be a regular file outside the saved auth store; unhook before signing in")
    auth = json.loads(source.read_text())
    identity = manifest[name]["account_id"]
    if account_identity(auth, args.provider) != identity:
        raise WorkerError("Fresh local login belongs to another provider account")
    fingerprints = login_fingerprints(auth, args.provider)
    for alias, record in manifest.items():
        if record["account_id"] != identity or record.get("ownership", "cloud") != "cloud":
            continue
        archive = args.store / CLOUD_DIRECTORY / f"{alias}.json"
        if archive.exists() and set(fingerprints).intersection(login_fingerprints(json.loads(archive.read_text()), args.provider)):
            raise WorkerError("Local login reuses a known cloud token or provider session")
    session = login_id(auth, args.provider)
    destination = args.store / f"auth.json.{target}"
    existing = manifest.get(target)
    expected = hashlib.sha256(json.dumps(auth).encode()).hexdigest()
    if existing and (existing.get("ownership") != "local" or existing.get("session_id") != session):
        raise WorkerError("Refusing to overwrite an independent login or cloud alias")
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not existing or digest(destination) != expected:
            raise WorkerError("Destination already exists; refusing to overwrite a login")
    checked = client.register_local(identity, session, fingerprints)
    for record in manifest.values():
        if record["account_id"] == identity and record.get("ownership", "cloud") == "cloud":
            record["fingerprints"] = sorted(set(record.get("fingerprints", [])) | set(checked["cloud_fingerprints"]))
    manifest[target] = {"account_id": identity, "ownership": "local", "session_id": session,
                        "fingerprints": fingerprints}
    # Missing credentials are never available. Publishing the registration first
    # permits retry after interruption without mistaking the file for cloud auth.
    write_private(args.store / MANIFEST, manifest)
    if not destination.exists():
        write_new(destination, auth)
    client.describe(identity, aliases=[alias for alias, record in manifest.items() if record["account_id"] == identity])


def restore_local_registration(args, manifest, client, identity):
    """Re-enrollment after reclaim retains previously established local ownership."""
    changed = False
    for alias, record in manifest.items():
        if record["account_id"] != identity or record.get("ownership") != "local" or cloud_enrolled(record):
            continue
        fingerprints = set(record["fingerprints"])
        path = args.store / f"auth.json.{alias}"
        if path.is_file() and not path.is_symlink():
            auth = json.loads(path.read_text())
            if account_identity(auth, args.provider) != identity:
                raise WorkerError("Independent local login belongs to another provider account")
            fingerprints.update(login_fingerprints(auth, args.provider))
        client.register_local(identity, record["session_id"], sorted(fingerprints))
        record["cloud_enrolled"] = True
        record["fingerprints"] = sorted(fingerprints)
        changed = True
    if changed:
        client.describe(identity, aliases=[alias for alias, record in manifest.items() if record["account_id"] == identity])
        write_private(args.store / MANIFEST, manifest)


def reclaim(args, manifest, client, name):
    identity = manifest[name]["account_id"]
    aliases = [alias for alias, record in manifest.items() if record["account_id"] == identity]
    local = [alias for alias in aliases if manifest[alias].get("ownership") == "local"]
    if local and args.destination_profile is None:
        raise WorkerError("Independent local login exists; reclaim requires a separate --destination-profile")
    if args.destination_profile:
        if not SUFFIX_RE.fullmatch(args.destination_profile) or args.destination_profile == "lock" or args.destination_profile in aliases:
            raise WorkerError("Reclaim destination must be a separate new profile")
        destinations = [args.destination_profile]
    else:
        destinations = [alias for alias in aliases if alias not in local]
    record = next(manifest[alias] for alias in aliases if alias not in local)
    pending = record.get("reclaim")
    if pending:
        if pending["destinations"] != destinations:
            raise WorkerError("Interrupted reclaim has another destination")
        if hasattr(client, "transfer_id"):
            client.transfer_id = pending["transfer_id"]
    for target in destinations:
        path = args.store / f"auth.json.{target}"
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not pending or digest(path) != pending["sha256"]:
                raise WorkerError("Local credentials changed; refusing to overwrite them with cloud state")
    try:
        lease = client.claim(identity, "local-reclaim", allow_staged=True)
    except WorkerError as error:
        if error.status != 404 or not pending or not all((args.store / f"auth.json.{target}").is_file() for target in destinations):
            raise
    else:
        expected = hashlib.sha256(json.dumps(lease["auth"]).encode()).hexdigest()
        if pending and pending["sha256"] != expected:
            raise WorkerError("Cloud login changed during interrupted reclaim; refusing to overwrite the saved transfer")
        pending = {"destinations": destinations, "sha256": expected,
                   "transfer_id": getattr(client, "transfer_id", "")}
        for alias in aliases:
            if alias not in local:
                manifest[alias]["reclaim"] = pending
        write_private(args.store / MANIFEST, manifest)
        for target in destinations:
            path = args.store / f"auth.json.{target}"
            if not path.exists():
                write_new(path, lease["auth"])
        client.delete(identity, lease["lease"])
    for alias in aliases:
        if alias in local:
            manifest[alias]["cloud_enrolled"] = False
        else:
            archive = args.store / CLOUD_DIRECTORY / f"{alias}.json"
            if archive.exists():
                if archive.is_symlink() or digest(archive) != manifest[alias].get("source_sha256"):
                    raise WorkerError("Cloud transfer archive changed; refusing to remove it")
                archive.unlink()
            del manifest[alias]
    write_private(args.store / MANIFEST, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enroll", "reclaim", "list", "migrate", "register-local", "cloud-only", "availability", "snapshot"])
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--provider", choices=["codex", "cursor"], default="codex")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument("--local-profile")
    parser.add_argument("--destination-profile")
    parser.add_argument("--fresh-login", action="store_true")
    args = parser.parse_args()
    args.store = args.store or Path.home() / f".local/share/clusterfork-auth/{args.provider}"
    if args.action in {"availability", "snapshot"}:
        value = (availability(args.store, args.provider) if args.action == "availability"
                 else consumer_snapshot(args.store, args.provider, connect(args.provider)))
        print(json.dumps(value))
        return
    try:
        manifest = load_remote_profiles(args.store)
    except (OSError, ValueError, TypeError):
        raise WorkerError("Cannot read or restore local cloud-reserved profiles") from None
    if args.action in {"list", "migrate"}:
        print(f"Cloud-managed {args.provider} profiles: {len(manifest)}")
        for name, record in sorted(manifest.items()):
            print(f"{name}: {record['account_id']} ({record.get('ownership', 'cloud')})")
        return
    client = connect(args.provider)
    profiles = saved_profiles(args.store)
    names = args.profile or sorted(set(profiles) | set(manifest) if args.action == "enroll" else manifest)
    if any(name not in set(profiles) | set(manifest) or not SUFFIX_RE.fullmatch(name) for name in names):
        raise WorkerError("Unknown saved profile")
    if args.action == "register-local" and (len(args.profile) != 1 or names[0] not in manifest):
        raise WorkerError("register-local requires one enrolled --profile")
    if args.action == "cloud-only" and any(name not in manifest for name in names):
        raise WorkerError("Profile is not cloud enrolled; enroll it first")
    changed = 0
    for name in names:
        if args.action == "enroll":
            if name in manifest:
                if cloud_enrolled(manifest[name]):
                    client.describe(manifest[name]["account_id"], aliases=[name])
                    restore_local_registration(args, manifest, client, manifest[name]["account_id"])
                elif args.profile:
                    raise WorkerError("Independent local login cannot be enrolled as the cloud login")
                continue
            auth = json.loads(profiles[name].read_text())
            identity = account_identity(auth, args.provider)
            profiles = saved_profiles(args.store)
            fingerprints = set(login_fingerprints(auth, args.provider))
            for alias, record in manifest.items():
                if record.get("ownership") == "local" and record["account_id"] == identity:
                    known = set(record.get("fingerprints", []))
                    if alias in profiles:
                        known.update(login_fingerprints(json.loads(profiles[alias].read_text()), args.provider))
                    if fingerprints.intersection(known):
                        raise WorkerError("Independent local login cannot replace or be enrolled as the cloud login; save its alias with rotate-auth")
            related = [alias for alias, path in profiles.items()
                       if manifest.get(alias, {}).get("ownership") != "local"
                       and account_identity(json.loads(path.read_text()), args.provider) == identity]
            try:
                client.enroll(auth, aliases=related)
            except WorkerError as error:
                if error.status != 409:
                    raise
                client.describe(identity, aliases=related)
            for alias in related:
                if alias not in manifest:
                    manifest[alias] = {"account_id": identity, "source_sha256": digest(profiles[alias]),
                                       "ownership": "cloud", "fingerprints": login_fingerprints(json.loads(profiles[alias].read_text()), args.provider)}
            write_private(args.store / MANIFEST, manifest)
            manifest = load_remote_profiles(args.store)
            restore_local_registration(args, manifest, client, identity)
        elif args.action == "register-local":
            register_local(args, manifest, client, name)
        elif name in manifest:
            if args.action == "cloud-only":
                identity = manifest[name]["account_id"]
                if any(record["account_id"] == identity and record.get("ownership") == "local" for record in manifest.values()):
                    raise WorkerError("Independent local login exists; delete its detached profile before choosing cloud-only")
                client.describe(identity, aliases=[name])
            else:
                reclaim(args, manifest, client, name)
        changed += 1
    print(f"{args.action}: {changed} profiles; {len(manifest)} managed aliases")


if __name__ == "__main__":
    try:
        main()
    except WorkerError as exc:
        print(f"Account operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print("Account operation failed. Check operator access, leases and local/cloud ownership; no secrets were logged.", file=sys.stderr)
        raise SystemExit(1)
