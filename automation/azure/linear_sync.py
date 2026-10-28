"""Project-scoped Linear lifecycle updates from durable workers and actual PRs."""

from __future__ import annotations

import hashlib
import json
import time
from uuid import UUID, uuid4

from worker_core import WorkerError


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
        names = ", ".join(sorted(set(" ".join(name.split())[:120] for name in failures)))
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
        import logging

        for row in self.runs.list():
            try:
                self.sync(row["RowKey"])
            except Exception:
                logging.error("Linear lifecycle synchronization deferred for %s", row["RowKey"])

    def sync(self, key):
        receipt, _ = self.receipts.get(key)
        route = self.registry.routes.get(receipt["project_id"])
        if not route or not route.linear_states or receipt["repository"] != route.repository:
            return
        run, _ = self.runs.get(key)
        owner = run["owner"]
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
            # Preserve completion, including a manually completed issue.
            if issue["state"]["id"] == states.done:
                return
            status = run["status"]
            if status == "running" and run.get("lease_until", 0) <= self.clock():
                result = self.github.worker_result(route.repository, owner)
                if result["status"] == "completed":
                    status = "failed"  # Includes cancellation and missing finish callbacks.
            pull = None
            if status in {"completed", "failed"}:
                url = run.get("pull_request") or (note.get("pull_request", "") if note.get("owner") == owner else "")
                if url or status == "failed":
                    pull = self.github.worker_pull(route, receipt, key, url)
            if pull:
                state, reason = pull_outcome(pull, route.auto_merge)
                if status == "failed" and state == "in_progress":
                    state, reason = "blocked", "Worker execution or PR publication failed. See the worker run."
            elif status == "running":
                state, reason = "in_progress", "Worker is running."
            elif status == "waiting_for_quota":
                state, reason = "ready", "Waiting for an account with enough quota; retries automatically."
            elif status == "failed":
                state, reason = "blocked", "Worker failed, was canceled, or ended without recording a result. See the worker run."
            elif status == "completed":
                state, reason = "done", "Worker completed successfully; no repository changes were needed."
            else:
                raise ValueError("Unknown worker outcome")
            target = route.ready_state_ids[0] if state == "ready" else getattr(states, state)
            run_id, attempt = owner.split(":")
            body = (f"Clusterfork: {reason}\n\n"
                    + (f"[Pull request]({pull['url']})\n\n" if pull else "")
                    + f"[Worker run](https://github.com/{route.repository}/actions/runs/{run_id}/attempts/{attempt})")
            signature = hashlib.sha256(json.dumps([target, body]).encode()).hexdigest()
            current, _ = self.runs.get(key)
            if any(current.get(field) != run.get(field) for field in ("owner", "status", "pull_request")):
                return  # A newer attempt/outcome supersedes this observation.
            updated, _ = self.notes.get(key)
            if updated.get("fence") != fence or updated["lease_until"] <= self.clock():
                raise WorkerError("Linear synchronization lease was lost")
            latest = self.linear.issue(issue["id"])
            if (not latest or latest["state"]["id"] != issue["state"]["id"]
                    or latest.get("project") != project or latest.get("archivedAt")):
                return
            if note.get("signature") != signature:
                # Linear validates supplied IDs as UUIDv4. Keep the value stable
                # across retries while using the accepted version/variant bits.
                digest = hashlib.sha256(f"clusterfork:{key}:status".encode()).digest()
                comment_id = str(UUID(bytes=digest[:16], version=4))
                self.linear.status_comment(issue["id"], comment_id, body)
            # Save the explanation before the transition, including when the
            # state write fails and must be retried after a successful comment.
            if issue["state"]["id"] != target:
                self.linear.set_state(issue["id"], target)
            updated, etag = self.notes.get(key)
            if updated.get("fence") != fence or updated["lease_until"] <= self.clock():
                raise WorkerError("Linear synchronization lease was lost")
            updated.update(signature=signature, owner=owner, terminal=state == "done",
                           pull_request=pull["url"] if pull else "")
            self.notes.replace(updated, etag)
        finally:
            updated, etag = self.notes.get(key)
            if updated.get("fence") == fence:
                updated["lease_until"] = 0
                self.notes.replace(updated, etag)
