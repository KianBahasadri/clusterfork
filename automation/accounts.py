#!/usr/bin/env python3
"""Enroll, list or reclaim saved Codex and Cursor CLI accounts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_runtime import write_private
from rotate_auth import REMOTE_MANIFEST, load_remote_profiles
from common.accounts import WorkerError, account_identity
from account_client import AccountClient

MANIFEST = REMOTE_MANIFEST


def connect(provider="codex"):
    return AccountClient(provider)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enroll", "reclaim", "list"])
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--provider", choices=["codex", "cursor"], default="codex")
    parser.add_argument("--store", type=Path)
    args = parser.parse_args()
    args.store = args.store or Path.home() / f".local/share/clusterfork-auth/{args.provider}"
    manifest_path = args.store / MANIFEST
    try:
        manifest = load_remote_profiles(args.store)
    except (OSError, ValueError, TypeError):
        raise WorkerError("Cannot read or restore local cloud-reserved profiles") from None
    if args.action == "list":
        print(f"Cloud-reserved {args.provider} profiles: {len(manifest)}")
        for name, record in sorted(manifest.items()):
            print(f"{name}: {record['account_id']}")
        return
    accounts = connect(args.provider)
    profiles = {p.name[len("auth.json."):]: p for p in args.store.glob("auth.json.*")
                if p.is_file() and p.name != "auth.json.lock"}
    names = args.profile or sorted(profiles if args.action == "enroll" else manifest)
    if any(name not in profiles or Path(name).name != name for name in names):
        raise WorkerError("Unknown saved profile")
    identities = {name: account_identity(json.loads(path.read_text()), args.provider) for name, path in profiles.items()}
    changed = 0
    for name in names:
        path = profiles[name]
        if args.action == "enroll":
            if name in manifest:
                continue
            auth = json.loads(path.read_text())
            try:
                identity = accounts.enroll(auth)
            except WorkerError:
                # An existing/staged cloud record owns the latest refresh token.
                # Validate identity without ever copying the stale local login.
                identity = account_identity(auth, args.provider)
                existing, _ = accounts.get(identity)
                if account_identity(json.loads(existing["auth"]), args.provider) != identity:
                    raise WorkerError("Cloud credentials do not match this account") from None
            for alias, alias_path in profiles.items():
                if identities[alias] == identity and alias not in manifest:
                    manifest[alias] = {"account_id": identity, "source_sha256": digest(alias_path)}
            write_private(manifest_path, manifest)
        else:
            if name not in manifest:
                continue
            record = manifest[name]
            aliases = [alias for alias, value in manifest.items() if value["account_id"] == record["account_id"]]
            if any(Path(alias).name != alias or alias not in profiles for alias in aliases):
                raise WorkerError("Unknown saved profile alias")
            if any(digest(profiles[alias]) != manifest[alias].get("source_sha256")
                   or identities[alias] != record["account_id"] for alias in aliases):
                raise WorkerError("Local credentials changed; refusing to overwrite them with cloud state")
            destinations = {alias: args.store / f"auth.json.{alias}" for alias in aliases}
            leased = accounts.claim(record["account_id"], "local-reclaim", allow_staged=True)
            try:
                for alias in aliases:
                    write_private(destinations[alias], leased["auth"])
                row, _ = accounts.get(record["account_id"])
                if row["fence"] != leased["lease"]:
                    raise WorkerError("Reclaim lease was lost")
                accounts.delete(record["account_id"], leased['lease'])
            except Exception:
                # Keep the local profile reserved until ownership transfer finishes.
                for alias in aliases:
                    if destinations[alias].exists():
                        manifest[alias]["source_sha256"] = digest(destinations[alias])
                write_private(manifest_path, manifest)
                raise
            for alias in aliases:
                del manifest[alias]
            write_private(manifest_path, manifest)
        changed += 1
    print(f"{args.action}: {changed} profiles; {len(manifest)} reserved for remote work")
    if args.action == "enroll":
        print("Enrollment includes the active local account.")


if __name__ == "__main__":
    try:
        main()
    except WorkerError as exc:
        print(f"Account transfer failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        # SDK errors can include request details. Do not echo credentials or bodies.
        print("Account transfer failed. Check login, account leases and local/cloud ownership; no secrets were logged.", file=sys.stderr)
        raise SystemExit(1)
