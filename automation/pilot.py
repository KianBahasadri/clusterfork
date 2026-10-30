#!/usr/bin/env python3
"""Check local configuration and package the shared Clusterfork automation source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(__file__).resolve().parent / "azure"
sys.path.insert(0, str(SOURCE))
from dispatch_core import Registry  # noqa: E402

CONFIG_NAMES = ("LINEAR_API_KEY", "LINEAR_OAUTH_CLIENT_ID", "LINEAR_OAUTH_CLIENT_SECRET",
                "LINEAR_WEBHOOK_SECRET", "GITHUB_APP_ID",
                "GITHUB_APP_PRIVATE_KEY_FILE", "COMMAND_CODE_API_KEY")
PACKAGE_FILES = ("function_app.py", "dispatch_core.py", "dispatch_clients.py", "quota_selection.py",
                 "worker_core.py", "worker_store.py", "worker_reporting.py", "linear_sync.py", "repository_queue.py",
                 "selection_reporting.py", "pending_writes.py", "routing_timing.py", "steering.py", "diagnostics.py", "auto_repair.py",
                 "readiness.py", "readiness_policy.py", "deployment_control.py",
                 "host.json", "requirements.txt", "projects.json")


def settings(path):
    """Read only these literal dotenv entries. Never source a user's shell file."""
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
            if match and match[1] in CONFIG_NAMES:
                parts = shlex.split(match[2], comments=True)
                if len(parts) > 1:
                    raise ValueError(f"quote the value of {match[1]} in {path}")
                values[match[1]] = parts[0] if parts else ""
    for name in CONFIG_NAMES:
        if name in os.environ:
            values[name] = os.environ[name]
    return values


def private_key_path(values):
    value = values.get("GITHUB_APP_PRIVATE_KEY_FILE")
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def check(values):
    missing = [name for name in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_FILE")
               if not values.get(name)]
    if not (values.get("LINEAR_API_KEY") or (values.get("LINEAR_OAUTH_CLIENT_ID")
                                            and values.get("LINEAR_OAUTH_CLIENT_SECRET"))):
        missing.insert(0, "LINEAR_API_KEY or (LINEAR_OAUTH_CLIENT_ID and LINEAR_OAUTH_CLIENT_SECRET)")
    key = private_key_path(values)
    if key and not key.is_file():
        missing.append("GITHUB_APP_PRIVATE_KEY_FILE (file not found)")
    if values.get("GITHUB_APP_ID") and not values["GITHUB_APP_ID"].isdigit():
        missing.append("GITHUB_APP_ID (use the numeric App ID)")
    return missing


def source_digest():
    digest = hashlib.sha256()
    for name in sorted(PACKAGE_FILES):
        digest.update(name.encode() + b"\0" + (SOURCE / name).read_bytes() + b"\0")
    return digest.hexdigest()


def package(destination, revision=None):
    Registry.load(SOURCE / "projects.json")
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("deployment revision must be a full commit SHA")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        # Explicit allowlist: never include .env, PEMs, caches, or local settings.
        for name in PACKAGE_FILES:
            archive.write(SOURCE / name, name)
        if revision:
            metadata = zipfile.ZipInfo("deployment.json")
            metadata.create_system = 3
            metadata.external_attr = 0o100644 << 16
            archive.writestr(metadata, json.dumps({"revision": revision, "digest": source_digest()}))


def command(args, *, sensitive=False):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        detail = "" if sensitive else result.stderr.strip()[:800]
        raise RuntimeError(f"{args[0]} command failed" + (f": {detail}" if detail else ""))
    return result.stdout.strip()


def function_hostname(response):
    hostname = response.get("defaultHostName") or response.get("properties", {}).get("defaultHostName")
    if not hostname:
        raise ValueError("Azure did not return a Function App hostname")
    return hostname


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "package"])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = Registry.load(SOURCE / "projects.json")
    if args.action == "package":
        if not args.output:
            parser.error("package requires --output PATH")
        package(args.output)
        print(f"Created {args.output} ({len(PACKAGE_FILES)} source files; no credentials)")
        return 0
    values = settings(args.env_file)
    missing = check(values)
    if args.action == "check":
        workflows = {route.workflow for route in registry.routes.values()}
        if workflows == {"clusterfork-pilot.yml"}:
            mode = "routing pilot (no coding agent)"
        elif "clusterfork-worker.yml" in workflows:
            mode = "Codex worker"
            if len(workflows) > 1:
                mode += " (with other workflow mappings)"
        else:
            mode = "custom workflow mappings" if workflows else "no workflow mappings"
        print(f"Mode: {mode}")
        print("Check: local configuration only; does not launch a worker or verify a live agent login.")
        for route in registry.routes.values():
            print(f"Mapping: {route.project_id} -> {route.repository} ({route.workflow} @ {route.ref})")
        print(f"Project enrollment label: {registry.enrollment_label}")
        for agent, profile in registry.agent_profiles.items():
            print(f"Agent profile: {agent} -> {profile.model} (effort: {profile.effort})")
        print("Agent selection: one agent per phase, lowest usage across applicable quota windows")
        print(f"Quota reserve: {registry.agent_selection.min_remaining_percent:g}% remaining; "
              f"maximum snapshot age: {registry.agent_selection.max_snapshot_age_seconds:g}s")
        for name in CONFIG_NAMES:
            print(f"{name}: {'set' if values.get(name) else 'blank'}")
        if missing:
            print("Required before deployment: " + ", ".join(missing))
        if not values.get("LINEAR_WEBHOOK_SECRET"):
            print("Webhook signing secret can be filled after the Linear webhook is created.")
        return 1 if missing else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
