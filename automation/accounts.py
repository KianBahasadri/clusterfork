#!/usr/bin/env python3
"""Enroll inactive saved Codex accounts for exclusive cloud use, or reclaim them."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent / "azure"))
from codex_runtime import write_private
from worker_core import Accounts, WorkerError, account_identity
from worker_store import AzureTable

MANIFEST = "remote-profiles.json"


def connect():
    result = subprocess.run(
        ["az", "storage", "account", "show-connection-string", "--name", "cfdispatch7604cac329",
         "--resource-group", "rg-clusterfork-automation", "--query", "connectionString",
         "-o", "tsv", "--only-show-errors"], capture_output=True, text=True)
    if result.returncode:
        raise WorkerError("Unable to read cloud account storage configuration")
    return AzureTable(result.stdout.strip(), "ClusterforkAccounts", "codex")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enroll", "reclaim", "list"])
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--store", type=Path,
                        default=Path.home() / ".local/share/clusterfork-auth/codex")
    args = parser.parse_args()
    manifest_path = args.store / MANIFEST
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if args.action == "list":
        print(f"Cloud-reserved Codex profiles: {len(manifest)}")
        return
    table = connect()
    accounts = Accounts(table)
    profiles = {p.name[len("auth.json."):]: p for p in args.store.glob("auth.json.*")
                if p.is_file() and p.name != "auth.json.lock"}
    names = args.profile or sorted(profiles if args.action == "enroll" else manifest)
    if any(name not in profiles or Path(name).name != name for name in names):
        raise WorkerError("Unknown saved profile")
    active = (args.store / "current").resolve()
    active_auth = Path.home() / ".codex/auth.json"
    active_id = account_identity(json.loads(active_auth.read_text())) if active_auth.is_file() else None
    identities = {name: account_identity(json.loads(path.read_text())) for name, path in profiles.items()}
    changed = 0
    for name in names:
        path = profiles[name]
        if args.action == "enroll":
            if path.resolve() == active or identities[name] == active_id or name in manifest:
                continue
            auth = json.loads(path.read_text())
            identity = accounts.enroll(auth)
            for alias, alias_path in profiles.items():
                if identities[alias] == identity:
                    manifest[alias] = {"account_id": identity, "source_sha256": digest(alias_path)}
            write_private(manifest_path, manifest)
        else:
            if name not in manifest:
                continue
            record = manifest[name]
            aliases = [alias for alias, value in manifest.items() if value["account_id"] == record["account_id"]]
            if any(digest(profiles[alias]) != manifest[alias]["source_sha256"] for alias in aliases):
                raise WorkerError("Local credentials changed; refusing to overwrite them with cloud state")
            leased = accounts.claim(record["account_id"], "local-reclaim")
            try:
                for alias in aliases:
                    write_private(profiles[alias], leased["auth"])
                row, etag = table.get(record["account_id"])
                if row["fence"] != leased["lease"]:
                    raise WorkerError("Reclaim lease was lost")
                table.delete(record["account_id"], etag)
            except Exception:
                # Keep the local profile reserved until ownership transfer finishes.
                for alias in aliases:
                    manifest[alias]["source_sha256"] = digest(profiles[alias])
                write_private(manifest_path, manifest)
                raise
            for alias in aliases:
                del manifest[alias]
            write_private(manifest_path, manifest)
        changed += 1
    print(f"{args.action}: {changed} profiles; {len(manifest)} reserved for remote work")
    print("The currently active local account was preserved.")


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
