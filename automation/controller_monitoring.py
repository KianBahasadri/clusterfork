#!/usr/bin/env python3
"""Smoke check using the existing deployment/operator Azure CLI identity only."""

import argparse
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import urlopen

from controller_deploy import command, ROOT


def query(workspace, text):
    # Azure CLI carries the authorized identity. Never pass tokens through argv
    # or export Azure credentials to the controller's coding/review runners.
    with tempfile.TemporaryDirectory(prefix="clusterfork-monitoring-") as directory:
        body = Path(directory) / "query.json"
        body.write_text(json.dumps({"query": text, "timespan": "PT30M"}))
        body.chmod(0o600)
        result = json.loads(command([
            "az", "rest", "--method", "post", "--url",
            f"https://api.loganalytics.io/v1/workspaces/{workspace}/query",
            "--resource", "https://api.loganalytics.io", "--body", "@" + str(body), "--output", "json",
        ]))
    if result.get("error") or not result.get("tables"):
        raise RuntimeError("Monitoring query failed; raw diagnostic withheld")
    table = result["tables"][0]
    return [dict(zip((c["name"] for c in table["columns"]), row)) for row in table["rows"]]


def render_query(text, run_id="*", attempt="*"):
    for name, value in {"TimeRange:grain": "1m", "TimeRange": "> ago(30m)",
                        "RunId:escape": run_id, "Attempt:escape": attempt}.items():
        text = text.replace("{" + name + "}", value)
    return text


def smoke(revision, *, timeout=600, run_id="*", attempt="*", sleep=time.sleep, clock=time.monotonic):
    monitoring = json.loads(command(["terraform", "output", "-json", "controller_monitoring"], cwd=ROOT / "infra/azure"))
    config = json.loads(command(["terraform", "output", "-json", "controller_config"], cwd=ROOT / "infra/azure"))
    workbook = json.loads(command([
        "az", "rest", "--method", "get", "--url",
        "https://management.azure.com" + monitoring["workbook_id"] + "?api-version=2022-04-01",
        "--output", "json",
    ]))
    layout = json.loads(workbook["properties"]["serializedData"])
    panels = {item["name"]: item["content"] for item in layout["items"] if item["type"] == 3}
    if not {"coverage", "operations", "selection", "timeline"} <= panels.keys():
        raise RuntimeError("Deployed controller workbook is incomplete")

    # Harmless traffic through the real HTTPS transport supplies both a success
    # and a failure classification without touching task or credential state.
    with urlopen("https://" + config["hostname"] + "/api/health", timeout=15) as response:
        if json.load(response).get("deployment", {}).get("revision") != revision:
            raise RuntimeError("Smoke check reached another controller revision")
    try:
        urlopen("https://" + config["hostname"] + "/api/monitoring-smoke-unknown", timeout=15).close()
    except HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError("Unexpected smoke response") from None
        exc.close()

    deadline = clock() + timeout
    while True:
        try:
            rows = query(monitoring["workspace_id"],
                "ClusterforkController_CL | where TimeGenerated > ago(30m) "
                f"and Revision == '{revision}' | summarize arg_max(TimeGenerated, *) by EventId "
                "| summarize Starts=countif(EventType in ('controller_started','controller_heartbeat')), "
                "Health=sumif(Count, EventType == 'http_summary' and Operation == 'controller/health'), "
                "Failures=sumif(Failures, EventType == 'http_summary' and Operation == 'controller/unknown')")
            if rows and all(rows[0].get(name, 0) > 0 for name in ("Starts", "Health", "Failures")):
                break
        except Exception:
            pass  # RBAC propagation and ingestion are eventually consistent.
        if clock() >= deadline:
            raise RuntimeError("New controller records are not queryable; inspect ingestion, RBAC and workspace cap")
        print("Waiting for bounded controller telemetry to become queryable.", flush=True)
        sleep(15)

    # Execute the actual saved workbook queries, not a separate lookalike query.
    # Empty phase/history tables are valid; coverage and HTTP panels must render
    # real controller records. A selected old run explicitly returns no coverage.
    rendered = {}
    for name, panel in panels.items():
        rendered[name] = query(monitoring["workspace_id"], render_query(panel["query"], run_id, attempt))
    if not rendered["coverage"] or not rendered["operations"] or not rendered["selection"]:
        raise RuntimeError("Workbook did not display controller telemetry")
    print("Verified new controller records and every deployed workbook query. " + monitoring["workbook_url"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", default="*")
    parser.add_argument("--attempt", default="*")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    if (not re.fullmatch(r"[0-9a-f]{40}", args.revision)
            or not re.fullmatch(r"\*|[1-9][0-9]{0,29}", args.run_id)
            or not re.fullmatch(r"\*|[1-9][0-9]{0,9}", args.attempt)
            or not 1 <= args.timeout_seconds <= 600):
        parser.error("Use a full revision, numeric run/attempt (or *), and a timeout of 1–600 seconds")
    smoke(args.revision, timeout=args.timeout_seconds, run_id=args.run_id, attempt=args.attempt)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Controller monitoring smoke check failed; raw diagnostics withheld. "
                         "Use the authorized deployment/operator path to inspect ingestion and workbook access.") from None
