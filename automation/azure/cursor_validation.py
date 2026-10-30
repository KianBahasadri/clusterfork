"""One authorized, post-deployment Cursor probe on a separate fresh runner."""

from dataclasses import replace
import hashlib
import json
import re
import time

from deployment_control import build_metadata
from dispatch_core import DispatchError
from linear_sync import comment_id
from quota_selection import AccountQuota, QuotaWindow, select_account
from worker_core import WorkerError, account_identity
from worker_reporting import usage_lines, validate_usage
from pending_writes import PendingWrites
from diagnostics import failure, record

ISSUE = "4f33c7d7-968c-40a1-8de5-1bc25549c32d"
IDENTITY = "856ba3056a374a4b0921288ef8dd02ae2bbd3ea0f0f8cd2d1defa264d8d8553a"
REFERENCE = f"cursor:{IDENTITY}"
KEY = hashlib.sha256(b"clusterfork:KIAN-922:cursor-cloud-validation-v1").hexdigest()
REPOSITORY = "KianBahasadri/clusterfork"
WORKFLOW = "clusterfork-cursor-smoke.yml"
ACTIONS = {"validation_start", "validation_heartbeat", "validation_claim", "validation_save",
           "validation_begin", "validation_finish"}
REASONS = {"cursor_profile_unavailable", "cursor_authentication_unavailable", "cursor_usage_unknown",
           "cursor_execution_failed", "cursor_turn_stopped", "account_unavailable", "file_check_failed",
           "worker_stopped", "dispatch_failed", "dispatch_unconfirmed", "passed",
           "broker_authorization_failed", "credential_persistence_failed"}


class CursorValidation:
    def __init__(self, registry, table, accounts, linear, github, control, clock=time.time):
        self.registry, self.table, self.accounts = registry, table, accounts
        self.linear, self.github, self.control, self.clock = linear, github, control, clock
        self.writes = PendingWrites("report", clock)

    def route(self):
        base = next(route for route in self.registry.routes.values() if route.repository == REPOSITORY)
        return replace(base, workflow=WORKFLOW, ref="main")

    def reserved(self):
        row, etag = self.accounts.table.get(IDENTITY)
        if (row.get("provider") != "cursor" or row.get("profile_name") != "21kb60"
                or row.get("credential_format") != "cursor-cli-auth-v1"
                or row.get("staged_for_issue") != "KIAN-922" or row.get("staged_for_issue_id") != ISSUE
                or account_identity(json.loads(row["auth"]), "cursor") != IDENTITY):
            raise WorkerError("Reserved Cursor identity or staging metadata does not match KIAN-922", 403)
        return row, etag

    def ensure(self, deployment):
        """Called only after matching health verification and explicit resume.

        Create the once-only receipt before dispatch. An uncertain response never
        causes another dispatch or another billable turn, including after deploys.
        """
        metadata = build_metadata()
        if (deployment.get("paused") is not False or not metadata.get("revision")
                or deployment.get("revision") != metadata["revision"]):
            return
        try:
            self.table.get(KEY)
        except WorkerError as exc:
            if exc.status != 404:
                raise
        else:
            self.reconcile()
            return
        if self.github.branch_sha(self.route()) != metadata["revision"]:
            return  # A newer main must finish its own verified deployment.
        row = {"RowKey": KEY, "status": "dispatching", "revision": metadata["revision"],
               "digest": metadata["digest"], "created_at": int(self.clock()), "report_pending": True, "report_version": 0}
        if not self.table.create(row):
            return
        # Missing/stale/invalid staged credentials are visible failures, never skips.
        try:
            reserved, _ = self.reserved()
            if reserved.get("state") != "staged":
                raise WorkerError("Reserved Cursor account is not staged", 403)
        except Exception:
            self.update(status="failed", reason="account_unavailable")
            self.report()
            return
        try:
            self.github.dispatch(self.route(), {"dispatch_key": KEY, "revision": metadata["revision"]})
        except DispatchError as exc:
            self.update(status="uncertain" if exc.uncertain else "failed",
                        reason="dispatch_unconfirmed" if exc.uncertain else "dispatch_failed")
        else:
            # A fast start/finish may beat dispatch acknowledgement persistence.
            current, etag = self.table.get(KEY)
            if current["status"] == "dispatching":
                current["status"] = "dispatched"
                current["report_version"] += 1
                self.table.replace(current, etag)
        self.report()

    def update(self, **fields):
        for _ in range(16):
            row, etag = self.table.get(KEY)
            row.update(fields, report_pending=True, report_version=row.get("report_version", 0) + 1)
            try:
                self.table.replace(row, etag)
                return row
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Cursor validation changed concurrently")

    def authorize(self, claims, data, row):
        workflow = f"{REPOSITORY}/.github/workflows/{WORKFLOW}@refs/heads/main"
        expected = {"repository": REPOSITORY, "ref": "refs/heads/main", "event_name": "workflow_dispatch",
                    "runner_environment": "github-hosted", "workflow_ref": workflow, "sha": row["revision"]}
        if (data.get("dispatch_key") != KEY or any(claims.get(k) != v for k, v in expected.items())
                or claims.get("job_workflow_ref", workflow) != workflow
                or claims.get("job_workflow_sha", row["revision"]) != row["revision"]
                or any(not re.fullmatch(r"[1-9][0-9]*", str(claims.get(k, "")))
                       for k in ("run_id", "run_attempt", "repository_id"))):
            raise WorkerError("Workflow is not authorized for reserved Cursor validation", 403)
        return f"{claims['run_id']}:{claims['run_attempt']}"

    def handle(self, action, claims, data):
        if action not in ACTIONS:
            raise WorkerError("Cursor validation cannot list accounts, execute tasks or publish", 403)
        row, etag = self.table.get(KEY)
        owner = self.authorize(claims, data, row)
        if action == "validation_start":
            if row["status"] not in {"dispatching", "dispatched", "uncertain", "running"}:
                raise WorkerError("The once-only Cursor validation already finished", 403)
            if row.get("owner") and row["owner"] != owner:
                raise WorkerError("Cursor validation belongs to another run/attempt", 403)
            if self.control.is_paused() or build_metadata() != {"revision": row["revision"], "digest": row["digest"]}:
                raise WorkerError("Cursor validation requires its healthy deployed broker revision", 403)
            self.github.verify_worker_run(REPOSITORY, claims, KEY, workflow=WORKFLOW)
            self.reserved()
            row.update(status="running", owner=owner, assigned_account_id=REFERENCE,
                       deadline=row.get("deadline", int(self.clock()) + 360), report_pending=True,
                       report_version=row.get("report_version", 0) + 1)
            self.table.replace(row, etag)
            self.report()
            return {"provider": "cursor", "account_id": REFERENCE, "model": "grok-4.7-high", "effort": "high",
                    "selection": {"min_remaining_percent": self.registry.agent_selection.min_remaining_percent,
                                  "max_snapshot_age_seconds": self.registry.agent_selection.max_snapshot_age_seconds}}
        if row.get("owner") != owner or row.get("assigned_account_id") != REFERENCE:
            raise WorkerError("Cursor validation has no assignment for this attempt", 403)
        # Fenced cleanup is available even after timeout/finish/deployment changes.
        if action == "validation_save":
            if data.get("account_id") != REFERENCE:
                raise WorkerError("Cursor validation cannot access another account", 403)
            self.accounts.save(IDENTITY, f"validation:{owner}", data.get("lease"), data.get("auth"),
                               release=data.get("release") is True)
            if data.get("release") is True:
                self.update(released=True)
            return {"saved": True}
        if row["status"] != "running":
            raise WorkerError("Cursor validation already finished", 403)
        if action == "validation_finish":
            status, reason = data.get("status"), data.get("reason")
            if (status not in {"passed", "failed"} or reason not in REASONS
                    or (status == "passed") != (reason == "passed")):
                raise WorkerError("Invalid Cursor validation result", 400)
            if status == "passed" and (not row.get("turn_started") or not row.get("released")
                                       or data.get("file_verified") is not True or self.clock() > row["deadline"]):
                raise WorkerError("Cursor validation has no completed bounded probe", 403)
            row.update(status=status, reason=reason, report_pending=True, finished_at=int(self.clock()),
                       report_version=row.get("report_version", 0) + 1)
            if data.get("usage_after") is not None:
                row["usage_after"] = json.dumps(validate_usage(data["usage_after"]))
            self.table.replace(row, etag)
            if status == "passed":
                self.activate()
            self.report()
            return {"status": status}
        if self.clock() > row["deadline"]:
            raise WorkerError("Cursor validation deadline expired", 403)
        if action == "validation_heartbeat":
            return {"running": True}
        if action == "validation_claim":
            if data.get("account_id") != REFERENCE or row.get("claimed"):
                raise WorkerError("Cursor validation may claim its reserved account only once", 403)
            row["claimed"] = True
            self.table.replace(row, etag)  # Fence an ambiguous claim response.
            return self.accounts.claim(IDENTITY, f"validation:{owner}", allow_staged=True)
        if action == "validation_begin":
            if row.get("turn_started") or not row.get("claimed"):
                raise WorkerError("The bounded Cursor turn may run only once", 403)
            usage = validate_usage(data.get("usage_before"))
            snapshot = AccountQuota(REFERENCE, "cursor", "grok-4.7-high", usage["observed_at"],
                                    tuple(QuotaWindow(**window) for window in usage["windows"]))
            if not select_account([snapshot], self.registry.agent_profiles, self.registry.agent_selection, now=self.clock()):
                raise WorkerError("Reserved Cursor account has insufficient known fresh quota", 403)
            row.update(turn_started=True, usage_before=json.dumps(usage), report_pending=True,
                       report_version=row.get("report_version", 0) + 1)
            self.table.replace(row, etag)
            return {"authorized": True, "timeout_seconds": 120}
        raise WorkerError("Unknown Cursor validation action", 403)

    def activate(self):
        try:
            reserved, etag = self.reserved()
        except WorkerError as exc:
            if exc.status != 404:
                raise
            self.update(activated=True, activation_state="reclaimed")
            return  # Reclaim must never be undone by a delayed activation.
        if reserved.get("state") == "staged":
            if reserved.get("owner") or reserved.get("lease_until", 0) > self.clock():
                raise WorkerError("Release the validation account before enrollment")
            reserved["state"] = "active"
            # Preserve the latest cloud tokens and the operator's staging audit.
            self.accounts.table.replace(reserved, etag)
        self.update(activated=True, activation_state="active")

    def report(self):
        token = None
        try:
            row, etag = self.table.get(KEY)
            if not row.get("report_pending"):
                return
            token = self.writes.begin(row)
            self.table.replace(row, etag)
            self.write_report(row)
            self.finish_report(token, version=row.get("report_version", 0))
        except Exception as exc:
            if token:
                try:
                    self.finish_report(token, retry=True)
                except Exception:
                    pass  # The durable in-flight marker also requires recovery.
            record("cursor_validation_reporting_deferred", **failure(exc))

    def finish_report(self, token, *, version=None, retry=False):
        for _ in range(16):
            row, etag = self.table.get(KEY)
            self.writes.finish(row, token, retry=retry or row.get("report_version", 0) != version)
            try:
                self.table.replace(row, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Cursor validation reporting changed concurrently")

    def write_report(self, row):
        body = (f"KIAN-922 post-deployment Cursor cloud validation: **{row['status']}**.\n\n"
                f"Provider: Cursor · Profile: 21kb60 · Account: `{IDENTITY}`\n\n"
                f"Model: `grok-4.7-high` · Reasoning effort: `high` · Revision: `{row['revision']}`.\n\n"
                "One agent turn, 120-second limit, disposable workspace; no repository publication or spending changes.")
        if row.get("reason"):
            body += f"\n\nResult: `{row['reason']}`."
        if row.get("usage_before"):
            body += "\n\n" + usage_lines(json.loads(row["usage_before"]),
                json.loads(row["usage_after"]) if row.get("usage_after") else None)
        else:
            body += "\n\nLive usage: unavailable; this is not a passing integration result."
        if row.get("owner"):
            run, attempt = row["owner"].split(":")
            body += f"\n\n[Validation run](https://github.com/{REPOSITORY}/actions/runs/{run}/attempts/{attempt})"
        self.linear.status_comment(ISSUE, comment_id(KEY, "cursor-validation"), body)

    def reconcile(self):
        try:
            row, _ = self.table.get(KEY)
        except WorkerError as exc:
            if exc.status == 404:
                return
            raise
        if row["status"] == "passed" and not row.get("activated"):
            self.activate()  # Recover a crash between result persistence and activation.
        elif row["status"] not in {"failed", "passed"}:
            # Never redispatch, including an ambiguous response. Discover the run
            # for a durable link and make missing finish callbacks visible.
            runs = self.github.dispatch_runs(self.route(), KEY)
            if runs:
                run = min(runs, key=lambda run: run["id"])
                if not row.get("owner"):
                    row = self.update(owner=f"{run['id']}:{run['run_attempt']}")
                if all(run["status"] == "completed" for run in runs):
                    row = self.update(status="failed", reason="worker_stopped")
            elif self.clock() - row["created_at"] > 900:
                self.update(status="failed", reason="dispatch_unconfirmed")
        self.report()
