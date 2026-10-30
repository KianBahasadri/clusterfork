#!/usr/bin/env python3
"""Coordinate CI, worker draining, and verification for GitHub Azure deployments."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pilot

HEALTH = "https://clusterfork-dispatch-7604cac329.azurewebsites.net/api/health"
REPOSITORY = "KianBahasadri/clusterfork"
STORAGE = "https://cfdispatch7604cac329.table.core.windows.net"


def github(path):
    request = Request(f"https://api.github.com/repos/{REPOSITORY}/{path}", headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json", "User-Agent": "clusterfork-deployment"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def health():
    try:
        with urlopen(HEALTH, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            kind = json.load(exc).get("runtime_error", "")
            if isinstance(kind, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", kind):
                return {"http_status": exc.code, "runtime_error": kind}
        except (ValueError, AttributeError):
            pass
        return {}
    except (URLError, OSError, ValueError):
        return {}


def output(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a") as target:
        target.write(f"{name}={str(value).lower()}\n")


def control():
    from azure.data.tables import TableClient
    from azure.identity import AzureCliCredential
    from deployment_control import DeploymentControl
    from worker_store import AzureTable

    client = TableClient(STORAGE, "ClusterforkOperations", credential=AzureCliCredential())
    return DeploymentControl(AzureTable.with_client(client, "control"))


def active_workers():
    active = []
    for workflow in ("clusterfork-worker.yml", "clusterfork-cursor-smoke.yml"):
        page = 1
        while True:
            try:
                data = github(f"actions/workflows/{workflow}/runs?per_page=100&page={page}")
            except HTTPError as exc:
                if exc.code == 404 and workflow == "clusterfork-cursor-smoke.yml":
                    break  # GitHub may not have indexed this new workflow yet.
                raise
            runs = data["workflow_runs"]
            active.extend(run["id"] for run in runs if run["status"] != "completed")
            if len(runs) < 100:
                break
            page += 1
    return active


def notify_resume(owner, revision):
    """Use the existing app storage/queue; no new endpoint or role grant."""
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    try:
        connection = pilot.command([
            "az", "functionapp", "config", "appsettings", "list",
            "--resource-group", "rg-clusterfork-automation", "--name", "clusterfork-dispatch-7604cac329",
            "--query", "[?name=='AzureWebJobsStorage'].value | [0]", "--output", "tsv"], sensitive=True)
        client = QueueClient.from_connection_string(
            connection, "clusterfork-events", message_encode_policy=TextBase64EncodePolicy(),
            connection_timeout=2, read_timeout=2, retry_total=0)
        # Commit the wake-up before unpausing. If resume fails, the consumer
        # still requires its matching, explicitly unpaused deployment record.
        client.send_message(json.dumps({"type": "DeploymentResume", "id": owner, "revision": revision}),
                            visibility_timeout=5)
    except Exception:
        raise RuntimeError("Unable to queue deployment recovery; worker dispatch remains paused") from None


def ci_state(revision):
    data = github(f"actions/workflows/test.yml/runs?head_sha={revision}&event=push&per_page=100")
    runs = [run for run in data["workflow_runs"]
            if run["head_sha"] == revision and run["head_branch"] == "main" and run["event"] == "push"]
    if not runs:
        return "pending"
    latest = max(runs, key=lambda run: (run["id"], run["run_attempt"]))
    return latest.get("conclusion") if latest["status"] == "completed" else "pending"


def prepare(revision, owner):
    state = control()
    if github("commits/main")["sha"] != revision:
        output("proceed", False)
        return
    state.pause(owner, revision)
    deadline = time.monotonic() + 330 * 60
    while time.monotonic() < deadline:
        if github("commits/main")["sha"] != revision:
            print("Superseded by a newer main commit; leaving the pause for its deployment.")
            output("proceed", False)
            return
        ci = ci_state(revision)
        if ci not in {"pending", "success"}:
            raise RuntimeError("Main CI did not pass; worker dispatch remains paused")
        active = active_workers()
        if ci == "success" and not active:
            pilot.package(Path("/tmp/clusterfork-function.zip"), revision=revision)
            output("proceed", True)
            return
        print(f"Waiting for CI ({ci}) and active workers ({len(active)}).", flush=True)
        time.sleep(15)
    raise RuntimeError("Active workers did not drain in time; dispatch remains paused")


def verify(revision, owner):
    expected = {"revision": revision, "digest": pilot.source_digest()}
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        result = health()
        if result.get("runtime_error"):
            print(f"Waiting for healthy Azure startup ({result['runtime_error']}).", flush=True)
        if (result.get("deployment") == expected and result.get("serial_admission") is True
                and result.get("missing") == []):
            if github("commits/main")["sha"] == revision:
                notify_resume(owner, revision)
                control().resume(owner, revision)
                print(f"Verified deployed revision {revision}; worker dispatch resumed.")
            else:
                print("Verified package; a newer main commit keeps dispatch paused for its deployment.")
            return
        time.sleep(5)
    raise RuntimeError("Azure did not confirm the expected healthy revision; dispatch remains paused")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "prepare", "verify"])
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("A full commit SHA is required")
    owner = f"github:{os.environ.get('GITHUB_RUN_ID', '')}:{os.environ.get('GITHUB_RUN_ATTEMPT', '')}"
    if args.action == "check":
        current = health()
        required = (current.get("deployment", {}).get("digest") != pilot.source_digest()
                    or current.get("deployment_paused") is not False or current.get("missing") != [])
        output("required", required)
    elif args.action == "prepare":
        prepare(args.revision, owner)
    else:
        verify(args.revision, owner)


if __name__ == "__main__":
    main()
