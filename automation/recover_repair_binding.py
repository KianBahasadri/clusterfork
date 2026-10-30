#!/usr/bin/env python3
"""Inspect or recover an undispatched repair's exact generated-content binding."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

import pilot

sys.path.insert(0, str(Path(__file__).parent / "azure"))
from auto_repair import repair_content, trigger_markdown
from codex_runtime import write_private
from dispatch_clients import Linear
from dispatch_core import Registry
from readiness_policy import issue_revision
from worker_core import WorkerError
from worker_reporting import worker_branch
from worker_store import AzureTable

HEALTH = "https://clusterfork-dispatch-7604cac329.azurewebsites.net/api/health"


def require(condition, message):
    if not condition:
        raise WorkerError(message)


def require_absent(table, key):
    try:
        table.get(key)
    except WorkerError as exc:
        if exc.status == 404:
            return
        raise
    raise WorkerError("Repair already has a dispatch or worker record; inspect that attempt instead")


@dataclass
class BindingPlan:
    repository: str
    parent: dict
    child: dict
    slot: dict
    versions: tuple
    revision: str

    def summary(self):
        return {"repository": self.repository, "issue": self.parent["identifier"],
                "repair": self.child["identifier"], "parent_key": self.parent["RowKey"],
                "repair_key": self.child["RowKey"], "before_revision": self.parent["repair_revision"],
                "after_revision": self.revision,
                "binding_change_needed": self.parent["repair_revision"] != self.revision}


def plan_rebind(issue, route, table, receipts, runs, reviews, linear):
    """Read-only proof that only the known generated-link formatting changed."""
    slot, slot_version = table.get("slot")
    require(slot.get("active_key") and not slot.get("owner"), "Repair must own an idle repository slot")
    child, child_version = table.get(slot["active_key"])
    require(child.get("is_auto_repair") is True and child.get("repair_parent_key")
            and child.get("status") == "active", "The admitted task must be a pending automatic repair")
    parent, parent_version = table.get(child["repair_parent_key"])
    require(issue["id"] in {parent["issue_id"], child["issue_id"]}, "Requested issue does not own this slot")
    require(parent.get("repair_attempted") is True and parent.get("status") == "repairing"
            and not parent.get("repair_outcome") and not parent.get("is_auto_repair")
            and parent.get("repair_key") == child["RowKey"]
            and parent.get("repair_issue_id") == child["issue_id"]
            and child.get("repair_parent_issue_id") == parent["issue_id"], "Repair intent is inconsistent")
    require(all(row["project_id"] == route.project_id and row["repository"].lower() == route.repository.lower()
                for row in (parent, child)), "Repair repository or project changed")
    require_absent(receipts, child["RowKey"])
    require_absent(runs, child["RowKey"])
    original = issue if issue["id"] == parent["issue_id"] else linear.issue(parent["issue_id"])
    repair = issue if issue["id"] == child["issue_id"] else linear.issue(child["issue_id"])
    require(original and repair and all((item.get("project") or {}).get("id") == route.project_id
                                       for item in (original, repair)), "Issue context is unavailable or moved")
    require(all(item.get("state", {}).get("type") not in {"completed", "canceled"}
                and not item.get("archivedAt") for item in (original, repair)), "Issue was completed, canceled, or archived")
    receipt, _ = receipts.get(parent["RowKey"])
    review, _ = reviews.get(receipt["readiness_review_key"])
    require(review.get("status") == "passed"
            and issue_revision(original) == json.loads(review["binding"])["issue_revision"],
            "Original task changed after its passing review")
    context = json.loads(parent["repair_context"])
    require(context["target_dispatch_key"] == parent["RowKey"]
            and context["target_identifier"] == parent["identifier"]
            and context["target_branch"] == worker_branch(parent["identifier"], parent["RowKey"])
            and all(child.get(name) == context[name] for name in
                    ("target_branch", "target_dispatch_key", "target_identifier", "target_pull_request")),
            "Repair target differs from its fenced parent")
    content = repair_content(context, route, original)
    expected = {"id": repair["id"], "project": {"id": route.project_id}, **content}
    require(issue_revision(repair) == issue_revision(expected), "Repair contains substantive title or description edits")
    # Accept only the previous unbracketed trigger-link form or an already-fixed
    # binding. Do not bless arbitrary tracker content by hashing its readback.
    before = "Triggered by: " + trigger_markdown(route.repository, context["trigger"])
    legacy = {**expected, "description": content["description"].replace(before, "Triggered by: " + context["trigger"], 1)}
    revision = issue_revision(expected)
    require(parent.get("repair_revision") in {issue_revision(legacy), revision}, "Unrecognized saved repair binding")
    return BindingPlan(route.repository, parent, child, slot,
                       (slot_version, parent_version, child_version), revision)


def apply_rebind(plan, table, health, enqueue, backup_dir, *, expected_digest):
    require(health.get("deployment", {}).get("digest") == expected_digest
            and health.get("deployment_paused") is False and health.get("serial_admission") is True
            and health.get("missing") == [], "Deploy the matching healthy broker and finish its pause before recovery")
    backup = Path(backup_dir) / f"binding-{int(time.time())}-{uuid4().hex[:8]}.json"
    audit = {**plan.summary(), "repair_issue_id": plan.child["issue_id"],
             "slot_owner": plan.slot.get("owner", ""), "versions": plan.versions,
             "binding_applied": False, "reconciliation_queued": False}
    write_private(backup, audit)
    slot_version, parent_version, child_version = plan.versions
    table.transaction([(plan.slot, slot_version),
                       ({**plan.parent, "repair_revision": plan.revision}, parent_version),
                       (plan.child, child_version)])
    audit["binding_applied"] = True
    write_private(backup, audit)
    enqueue({"type": "Project", "id": plan.parent["project_id"], "enqueued_at": time.time()})
    audit["reconciliation_queued"] = True
    write_private(backup, audit)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True, help="Parent or repair Linear identifier/UUID")
    parser.add_argument("--apply", action="store_true", help="Apply the verified rebind and request reconciliation")
    parser.add_argument("--env-file", type=Path, default=pilot.ROOT / ".env")
    parser.add_argument("--backup-dir", type=Path,
                        default=Path.home() / ".local/state/clusterfork/recovery/repair-bindings")
    args = parser.parse_args()
    logging.getLogger("azure").setLevel(logging.WARNING)
    values = pilot.settings(args.env_file)
    linear = Linear(values.get("LINEAR_API_KEY"), client_id=values.get("LINEAR_OAUTH_CLIENT_ID"),
                    client_secret=values.get("LINEAR_OAUTH_CLIENT_SECRET"))
    issue = linear.issue(args.issue)
    require(issue and issue.get("project"), "Issue is unavailable or has no mapped project")
    route = Registry.load(pilot.SOURCE / "projects.json").routes.get(issue["project"]["id"])
    require(route, "Issue project has no repository mapping")
    result = subprocess.run(["az", "storage", "account", "show-connection-string", "--name", "cfdispatch7604cac329",
                             "--resource-group", "rg-clusterfork-automation", "--query", "connectionString", "-o", "tsv",
                             "--only-show-errors"], capture_output=True, text=True)
    require(result.returncode == 0, "Unable to open runtime storage configuration")
    connection = result.stdout.strip()
    table = AzureTable(connection, "ClusterforkRepositoryQueue", hashlib.sha256(route.repository.lower().encode()).hexdigest())
    plan = plan_rebind(issue, route, table, AzureTable(connection, "ClusterforkDispatches", "pilot"),
                       AzureTable(connection, "ClusterforkWorkers", "worker"),
                       AzureTable(connection, "ClusterforkReadiness", "readiness"), linear)
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", **plan.summary()}))
    if args.apply:
        from azure.storage.queue import QueueClient, TextBase64EncodePolicy
        with urlopen(HEALTH, timeout=20) as response:
            health = json.load(response)
        client = QueueClient.from_connection_string(connection, "clusterfork-events", message_encode_policy=TextBase64EncodePolicy())
        backup = apply_rebind(plan, table, health, lambda event: client.send_message(json.dumps(event)),
                              args.backup_dir, expected_digest=pilot.source_digest())
        print(json.dumps({"binding_applied": True, "reconciliation_queued": True, "backup": str(backup)}))


if __name__ == "__main__":
    try:
        main()
    except WorkerError as exc:
        print(f"Recovery refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # SDK and HTTP exceptions can contain credential-bearing request data.
        print(f"Recovery failed ({type(exc).__name__}); inspect the private backup before retrying.", file=sys.stderr)
        raise SystemExit(1)
