#!/usr/bin/env python3
"""Check, package, configure, and deploy the shared Clusterfork routing pilot."""

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
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(__file__).resolve().parent / "azure"
sys.path.insert(0, str(SOURCE))
from dispatch_core import Registry  # noqa: E402

CONFIG_NAMES = ("LINEAR_API_KEY", "LINEAR_WEBHOOK_SECRET", "GITHUB_APP_ID",
                "GITHUB_APP_PRIVATE_KEY_FILE", "COMMAND_CODE_API_KEY")
PACKAGE_FILES = ("function_app.py", "dispatch_core.py", "dispatch_clients.py", "quota_selection.py",
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
    missing = [name for name in ("LINEAR_API_KEY", "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_FILE")
               if not values.get(name)]
    key = private_key_path(values)
    if key and not key.is_file():
        missing.append("GITHUB_APP_PRIVATE_KEY_FILE (file not found)")
    if values.get("GITHUB_APP_ID") and not values["GITHUB_APP_ID"].isdigit():
        missing.append("GITHUB_APP_ID (use the numeric App ID)")
    return missing


def package(destination):
    Registry.load(SOURCE / "projects.json")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        # Explicit allowlist: never include .env, PEMs, caches, or local settings.
        for name in PACKAGE_FILES:
            archive.write(SOURCE / name, name)


def command(args, *, sensitive=False):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        detail = "" if sensitive else result.stderr.strip()[:800]
        raise RuntimeError(f"{args[0]} command failed" + (f": {detail}" if detail else ""))
    return result.stdout.strip()


def azure_names(args):
    account = json.loads(command(["az", "account", "show", "-o", "json", "--only-show-errors"]))
    if account.get("state") != "Enabled":
        raise ValueError("the active Azure subscription must be enabled")
    suffix = hashlib.sha256(account["id"].encode()).hexdigest()[:10]
    return {"group": args.resource_group, "app": f"clusterfork-dispatch-{suffix}",
            "storage": f"cfdispatch{suffix}"}


def function_hostname(response):
    hostname = response.get("defaultHostName") or response.get("properties", {}).get("defaultHostName")
    if not hostname:
        raise ValueError("Azure did not return a Function App hostname")
    return hostname


def configure(values, names):
    key = private_key_path(values).read_text()
    if "PRIVATE KEY-----" not in key:
        raise ValueError("GITHUB_APP_PRIVATE_KEY_FILE is not a PEM private key")
    runtime = {name: values.get(name, "") for name in ("LINEAR_API_KEY", "LINEAR_WEBHOOK_SECRET", "GITHUB_APP_ID")}
    runtime["GITHUB_APP_PRIVATE_KEY"] = key
    with tempfile.TemporaryDirectory(prefix="clusterfork-settings-") as directory:
        path = Path(directory) / "settings.json"
        # Set restrictive permissions at creation, before writing the secrets.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(runtime, output)
        command(["az", "functionapp", "config", "appsettings", "set", "-g", names["group"],
                 "-n", names["app"], "--settings", f"@{path}", "-o", "none", "--only-show-errors"],
                sensitive=True)
    # COMMAND_CODE_API_KEY stays local. The routing pilot does not invoke agents.


def deploy(args, values, names):
    command(["az", "group", "create", "-n", names["group"], "-l", args.location,
             "--tags", "application=clusterfork-routing-pilot", "-o", "none", "--only-show-errors"])
    command(["az", "storage", "account", "create", "-n", names["storage"], "-g", names["group"],
             "-l", args.location, "--sku", "Standard_LRS", "--kind", "StorageV2", "--https-only", "true",
             "--min-tls-version", "TLS1_2", "--allow-blob-public-access", "false", "-o", "none", "--only-show-errors"])
    command(["az", "storage", "queue", "create", "--account-name", names["storage"],
             "--name", "clusterfork-events", "--auth-mode", "key", "-o", "none", "--only-show-errors"],
            sensitive=True)
    exists = subprocess.run(["az", "functionapp", "show", "-g", names["group"], "-n", names["app"],
                             "-o", "none", "--only-show-errors"], capture_output=True, text=True)
    if exists.returncode:
        if not any(reason in exists.stderr for reason in ("ResourceNotFound", "NotFound")):
            raise RuntimeError("unable to check the existing Function App")
        command(["az", "functionapp", "create", "-g", names["group"], "-n", names["app"],
                 "--storage-account", names["storage"], "--flexconsumption-location", args.location,
                 "--runtime", "python", "--runtime-version", "3.12", "--instance-memory", "512",
                 "--maximum-instance-count", "40", "--disable-app-insights", "true",
                 "-o", "none", "--only-show-errors"])
    configure(values, names)
    with tempfile.TemporaryDirectory(prefix="clusterfork-package-") as directory:
        path = Path(directory) / "function.zip"
        package(path)
        command(["az", "functionapp", "deployment", "source", "config-zip", "-g", names["group"],
                 "-n", names["app"], "--src", str(path), "--build-remote", "true",
                 "-o", "none", "--only-show-errors"], sensitive=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "package", "deploy", "configure"])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resource-group", default="rg-clusterfork-automation")
    parser.add_argument("--location", default="eastus")
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
        print("Mode: routing pilot (no coding agent)")
        for route in registry.routes.values():
            print(f"Mapping: {route.project_id} -> {route.repository} ({route.ref})")
        print(f"Project enrollment label: {registry.enrollment_label}")
        for agent, profile in registry.agent_profiles.items():
            print(f"Agent profile: {agent} -> {profile.model} (effort: {profile.effort})")
        print("Agent selection: one agent per issue, lowest usage across applicable quota windows")
        print(f"Quota reserve: {registry.agent_selection.min_remaining_percent:g}% remaining; "
              f"maximum snapshot age: {registry.agent_selection.max_snapshot_age_seconds:g}s")
        for name in CONFIG_NAMES:
            print(f"{name}: {'set' if values.get(name) else 'blank'}")
        if missing:
            print("Required before deployment: " + ", ".join(missing))
        if not values.get("LINEAR_WEBHOOK_SECRET"):
            print("Webhook signing secret can be filled after the Linear webhook is created.")
        return 1 if missing else 0
    if missing:
        raise ValueError("fill .env before deploying: " + ", ".join(missing))
    names = azure_names(args)
    if args.action == "deploy":
        deploy(args, values, names)
    else:
        configure(values, names)
    hostname = function_hostname(json.loads(command([
        "az", "functionapp", "show", "-g", names["group"], "-n", names["app"],
        "-o", "json", "--only-show-errors"])))
    print(f"Function App: {names['app']} (resource group: {names['group']})")
    print(f"Webhook URL: https://{hostname}/api/linear")
    print(f"Health URL: https://{hostname}/api/health")
    if not values.get("LINEAR_WEBHOOK_SECRET"):
        print("Next: create the Linear webhook, fill LINEAR_WEBHOOK_SECRET, then run configure.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
