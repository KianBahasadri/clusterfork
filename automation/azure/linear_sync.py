"""Project-scoped Linear lifecycle updates from durable workers and actual PRs."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from uuid import UUID, uuid4

from worker_core import WorkerError
from worker_reporting import STAGES, attempt_comment, quota_wait_text
from readiness_policy import redact_review_text
from diagnostics import context, failure, failure_summary, record


def comment_id(key, kind):
    # Linear accepts client-supplied UUIDv4 IDs. Stable IDs recover ambiguous writes.
    digest = hashlib.sha256(f"clusterfork:{key}:{kind}".encode()).digest()
    return str(UUID(bytes=digest[:16], version=4))


def repair_managed(receipt):
    return bool((receipt.get("repair_attempted") and receipt.get("repair_issue_id"))
                or (receipt.get("is_auto_repair") and receipt.get("repair_parent_key")))


def failed_required_checks(pull):
    """Only completed, failing required checks qualify for automatic repair."""
    commits = pull.get("commits", {}).get("nodes", [])
    rollup = commits[-1]["commit"].get("statusCheckRollup") if commits else None
    if not rollup:
        return []
    contexts = rollup["contexts"]
    if contexts["pageInfo"]["hasNextPage"]:
        raise ValueError("Incomplete PR check results")
    required = [check for check in contexts["nodes"] if check["isRequired"]]
    failures = []
    for check in required:
        if check["__typename"] == "CheckRun":
            if check["status"] != "COMPLETED":
                return []
            if check["conclusion"] in {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
                failures.append((check["name"], check.get("detailsUrl", "")))
        elif check["__typename"] == "StatusContext":
            if check["state"] in {"PENDING", "EXPECTED"}:
                return []
            if check["state"] in {"FAILURE", "ERROR"}:
                failures.append((check["context"], check.get("targetUrl", "")))
        else:
            raise ValueError("Unknown PR check type")
    return failures


def pull_outcome(pull, auto_merge):
    """Return a lifecycle state and explanation; pending CI is normal progress."""
    if pull.get("merged"):
        return "done", "Pull request merged."
    if pull.get("state") == "CLOSED":
        return "blocked", "Pull request was closed without merging."
    if pull.get("state") != "OPEN":
        raise ValueError("Unknown pull request state")
    if pull.get("isDraft"):
        return "blocked", "Pull request is a draft and cannot merge yet."
    if pull.get("mergeable") == "CONFLICTING" or pull.get("mergeStateStatus") == "DIRTY":
        return "blocked", "Pull request has merge conflicts."
    commits = pull.get("commits", {}).get("nodes", [])
    rollup = commits[-1]["commit"].get("statusCheckRollup") if commits else None
    if not rollup:
        return "in_progress", "Waiting for required CI checks to appear."
    contexts = rollup["contexts"]
    if contexts["pageInfo"]["hasNextPage"]:
        raise ValueError("Incomplete PR check results")
    required = [check for check in contexts["nodes"] if check["isRequired"]]
    if not required:
        return "in_progress", "Waiting for required CI checks to appear."
    pending, failures = False, []
    for check in required:
        if check["__typename"] == "CheckRun":
            if check["status"] != "COMPLETED":
                pending = True
            elif check["conclusion"] not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                failures.append(check["name"])
        elif check["__typename"] == "StatusContext":
            if check["state"] in {"PENDING", "EXPECTED"}:
                pending = True
            elif check["state"] in {"FAILURE", "ERROR"}:
                failures.append(check["context"])
            elif check["state"] != "SUCCESS":
                raise ValueError("Unknown commit status")
        else:
            raise ValueError("Unknown PR check type")
    if pending:
        return "in_progress", "Waiting for required CI checks."
    if failures:
        names = ", ".join(sorted(set(" ".join(redact_review_text(name).split())[:120] for name in failures)))
        return "blocked", f"Required CI checks failed or were canceled: {names[:600]}."
    if auto_merge:
        if pull.get("mergeStateStatus") == "BEHIND":
            return "blocked", "GitHub requires the PR branch to be updated before merging."
        if pull.get("mergeable") == "UNKNOWN" or pull.get("mergeStateStatus") == "UNKNOWN":
            return "in_progress", "GitHub is calculating mergeability."
        if not pull.get("autoMergeRequest"):
            return "blocked", "Automatic merge is not enabled on this PR."
        if pull.get("mergeStateStatus") == "BLOCKED":
            return "blocked", "GitHub branch requirements are preventing the merge."
        return "in_progress", "Required CI passed; waiting for GitHub to merge the PR."
    return "in_progress", "Required CI passed; the project is configured for manual merging."


class LinearSync:
    def __init__(self, registry, receipts, runs, notes, linear, github, clock=time.time):
        self.registry, self.receipts, self.runs, self.notes = registry, receipts, runs, notes
        self.linear, self.github, self.clock = linear, github, clock

    def reconcile(self):
        # Isolate failures so one revoked repository grant cannot stall others.
        for row in self.runs.list():
            run_id, _, attempt = row.get("owner", "").partition(":")
            with context(dispatch_key=row["RowKey"], run_id=run_id, run_attempt=attempt):
                try:
                    self.sync(row["RowKey"])
                except Exception as exc:
                    record("linear_sync_deferred", level=logging.ERROR, **failure(exc, "worker/linear_sync"))

    def sync(self, key):
        receipt, _ = self.receipts.get(key)
        managed = repair_managed(receipt)
        if managed and not receipt.get("repair_parent_key"):
            # The root yielded lifecycle ownership to its second attempt.
            return
        route = self.registry.routes.get(receipt["project_id"])
        if not route or not route.linear_states or receipt["repository"] != route.repository:
            return
        run, _ = self.runs.get(key)
        if managed and not run.get("usage_before"):
            return
        owner = run["owner"]
        try:
            note, etag = self.notes.get(key)
        except WorkerError as exc:
            if exc.status != 404:
                raise
            self.notes.create({"RowKey": key, "lease_until": 0})
            note, etag = self.notes.get(key)
        if note.get("terminal") and note.get("owner") == owner:
            return
        if note.get("lease_until", 0) > self.clock():
            return
        fence = str(uuid4())
        note.update(lease_until=int(self.clock()) + 120, fence=fence)
        self.notes.replace(note, etag)
        try:
            if managed:
                # Queue reconciliation owns repair state/stages and its terminal
                # report. Attempt usage still belongs on the tracked Linear issue
                # with this repair dispatch key, including legacy child attempts.
                status = run["status"]
                if status == "running" and run.get("lease_until", 0) <= self.clock():
                    if self.github.worker_result(route.repository, owner)["status"] == "completed":
                        status = "failed"  # No finish callback: report unavailable final usage/time.
                current, _ = self.runs.get(key)
                if any(current.get(field) != run.get(field) for field in
                       ("owner", "status", "report_version")):
                    return
                updated = dict(note)
                self.attempts(key, receipt, {**run, "status": status}, note, updated, self.linear.status_comment)
                current, version = self.notes.get(key)
                if current.get("fence") != fence or current["lease_until"] <= self.clock():
                    raise WorkerError("Linear synchronization lease was lost")
                updated.update(owner=owner, terminal=status != "running")
                self.notes.replace(updated, version)
                return
            issue = self.linear.issue(receipt["issue_id"])
            project = (issue or {}).get("project") or {}
            if (not issue or issue.get("archivedAt") or project.get("archivedAt")
                    or project.get("id") != route.project_id
                    or not any(label["name"] == self.registry.enrollment_label
                               for label in project.get("labels", {}).get("nodes", []))):
                return
            states = route.linear_states
            managed = (*route.ready_state_ids, states.in_progress, states.blocked, states.done)
            if issue["state"]["id"] not in managed or issue["state"].get("type") == "canceled":
                return
            # Preserve manual completion, but repair our own unfinished terminal
            # report even if a previous state write succeeded before its response.
            if issue["state"]["id"] == states.done and note.get("completion_owner") != owner:
                return
            status = run["status"]
            confirmed_failure = status == "failed"
            if receipt.get("deployment_wait_owner") == owner:
                status = "waiting_for_deployment"
            if run.get("dependency_wait") and run.get("stage", "initializing") in {"initializing", "selecting_account"}:
                status = "waiting_for_dependencies"
            if status == "running" and run.get("lease_until", 0) <= self.clock():
                result = self.github.worker_result(route.repository, owner)
                if result["status"] == "completed":
                    status = "failed"  # Includes cancellation and missing finish callbacks.
                    confirmed_failure = result.get("conclusion") == "failure"
            pull = None
            if status in {"completed", "failed"}:
                url = run.get("pull_request") or (note.get("pull_request", "") if note.get("owner") == owner else "")
                if url or status == "failed":
                    pull = self.github.worker_pull(route, receipt, key, url)
            if pull:
                state, reason = pull_outcome(pull, route.auto_merge)
                if status == "failed" and state == "in_progress":
                    state, reason = "blocked", "Worker execution or PR publication failed. See the worker run."
                if status == "failed" and state != "done" and run.get("failure"):
                    reason = failure_summary(json.loads(run["failure"])) + " " + reason
            elif status == "running":
                state, reason = "in_progress", "Worker is running."
            elif status == "waiting_for_quota":
                state, reason = "ready", "Waiting for an account with enough quota; retries automatically."
                if run.get("quota_wait"):
                    reason += " " + quota_wait_text(json.loads(run["quota_wait"]))
            elif status == "waiting_for_dependencies":
                state, reason = "ready", "Waiting for blocked-by issue prerequisites; coding has not started. See the repository queue comment."
            elif status == "waiting_for_deployment":
                state, reason = "ready", "Azure deployment paused startup; waiting for explicit resume and confirmation that the GitHub attempt stopped."
            elif status == "failed":
                before_coding = confirmed_failure and run.get("stage") in {"initializing", "selecting_account"}
                state = "ready" if before_coding else "blocked"
                reason = (failure_summary(json.loads(run["failure"])) if run.get("failure") else
                          "Worker failed, was canceled, or ended without recording a result. See the worker run.")
                if before_coding:
                    reason += " Coding has not started; confirmed startup failures retry after 60 seconds."
            elif status == "completed":
                state, reason = "done", "Worker completed successfully; no repository changes were needed."
            else:
                raise ValueError("Unknown worker outcome")
            if issue["state"]["id"] == states.done and state != "done":
                return
            stage = None
            if state == "in_progress":
                stage = "review" if pull else run.get("stage", "initializing")
            elif status == "waiting_for_quota":
                stage = "waiting_for_quota"
            target = route.ready_state_ids[0] if state == "ready" else getattr(states, state)
            run_id, attempt = owner.split(":")
            body = (f"Clusterfork: {reason}\n\n"
                    + (f"Stage: {STAGES[stage]}.\n\n" if stage else "")
                    + (f"[Pull request]({pull['url']})\n\n" if pull else "")
                    + f"[Worker run](https://github.com/{route.repository}/actions/runs/{run_id}/attempts/{attempt})")
            signature = hashlib.sha256(json.dumps([target, body]).encode()).hexdigest()
            current, _ = self.runs.get(key)
            if any(current.get(field) != run.get(field) for field in
                   ("owner", "status", "pull_request", "stage", "report_version")):
                return  # A newer attempt/outcome supersedes this observation.
            updated, version = self.notes.get(key)
            if updated.get("fence") != fence or updated["lease_until"] <= self.clock():
                raise WorkerError("Linear synchronization lease was lost")
            latest = self.linear.issue(issue["id"])
            if (not latest or latest["state"]["id"] != issue["state"]["id"]
                    or latest.get("project") != project or latest.get("archivedAt")):
                return
            current_receipt, _ = self.receipts.get(key)
            if repair_managed(current_receipt):
                return
            if state == "done":
                # Persist before any external write. A crash, ambiguous state
                # response or failed comment must leave terminal reporting retryable.
                updated["completion_owner"] = owner
                self.notes.replace(updated, version)
            errors = []

            def write(action, *args):
                try:
                    action(*args)
                except Exception as exc:
                    if state != "done":
                        raise
                    # Confirmed completion is independent of comment permissions
                    # and label failures. Keep retrying every unfinished projection.
                    errors.append(exc)
                    return False
                return True

            self.attempts(key, receipt, {**run, "status": status}, note, updated,
                          lambda *args: write(self.linear.status_comment, *args))
            if note.get("signature") != signature:
                if write(self.linear.status_comment, issue["id"], comment_id(key, "status"), body):
                    updated["signature"] = signature
            # Dedicated add/remove mutations preserve unrelated labels, including
            # labels a person adds while synchronization is in flight.
            write(self.linear.set_stage, issue["id"], stage)
            # Nonterminal transitions still require their explanation first.
            # Terminal writes proceed even when a preceding projection failed.
            if issue["state"]["id"] != target:
                write(self.linear.set_state, issue["id"], target)
            saved, etag = self.notes.get(key)
            if saved.get("fence") != fence or saved["lease_until"] <= self.clock():
                raise WorkerError("Linear synchronization lease was lost")
            updated.update(owner=owner, terminal=state == "done" and not errors,
                           pull_request=pull["url"] if pull else "")
            self.notes.replace(updated, etag)
            if errors:
                raise errors[0]
        finally:
            updated, etag = self.notes.get(key)
            if updated.get("fence") == fence:
                updated["lease_until"] = 0
                self.notes.replace(updated, etag)

    @staticmethod
    def attempts(key, receipt, run, note, updated, write):
        if not run.get("usage_before"):
            return
        owner = run["owner"]
        previous_attempt = note.get("attempt_owner") == owner
        updated["attempt_owner"] = owner
        for kind in ("start", "finish"):
            if kind == "finish" and run["status"] == "running":
                continue
            body = attempt_comment(run, receipt["repository"], finished=kind == "finish")
            signature = hashlib.sha256(body.encode()).hexdigest()
            field = f"{kind}_signature"
            if not previous_attempt or note.get(field) != signature:
                updated.pop(field, None)
                if write(receipt["issue_id"], comment_id(key, f"{owner}:{kind}"), body) is not False:
                    updated[field] = signature
