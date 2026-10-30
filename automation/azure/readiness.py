"""Durable, broker-enforced readiness reviews, independent of coding receipts."""

import json
import logging
import time
from uuid import uuid4

from dispatch_core import DispatchError, eligibility
from linear_sync import comment_id
from readiness_policy import (PROTOCOL, RETRY_SECONDS, REVIEW_TIMEOUT_SECONDS, digest,
                              issue_revision, policy_version, redact_review_text, redact_review_value,
                              result_snapshot, validate_result)
from worker_core import (ACCOUNT_HANDOFF_SECONDS, LEASE_SECONDS, SELECTOR_ACTIONS, WorkerError,
                         account_request, authorize_claims, is_account_selector)

REJECTIONS = {"needs_clarification", "rejected"}
DECISIONS = {"passed", *REJECTIONS}


def optional(table, key):
    try:
        return table.get(key)
    except WorkerError as exc:
        if exc.status != 404:
            raise
        return None, None


class ReadinessGate:
    def __init__(self, registry, table, accounts, linear, github, clock=time.time):
        self.registry, self.table, self.accounts = registry, table, accounts
        self.linear, self.github, self.clock = linear, github, clock
        self.admitted = lambda issue, route: False
        self.review_selection = lambda issue, route, rejected: None

    def pointer(self, issue):
        return "issue-" + digest(issue["id"])

    def binding(self, issue, route):
        return {"issue_revision": issue_revision(issue),
                "policy_version": policy_version(self.registry, route),
                # Conservatively version all repository rules and relevant code.
                "context_sha": self.github.branch_sha(route)}

    def current(self, row, issue, route):
        pointer, _ = optional(self.table, self.pointer(issue))
        return (pointer and pointer["review_key"] == row["RowKey"]
                and json.loads(row["binding"]) == self.binding(issue, route))

    def require(self, issue, route):
        if route.workflow != "clusterfork-worker.yml":
            return {}
        pointer, _ = optional(self.table, self.pointer(issue))
        row, _ = optional(self.table, pointer["review_key"]) if pointer else (None, None)
        if not row or row["status"] != "passed" or not self.current(row, issue, route):
            raise WorkerError("A current passing readiness review is required", 403)
        # Persisted data is also checked; a text/model instruction cannot grant admission.
        try:
            result = validate_result(json.loads(row["result"]))
        except (ValueError, TypeError, KeyError):
            raise WorkerError("Readiness approval is invalid", 403) from None
        if result["verdict"] != "ready":
            raise WorkerError("Readiness approval is invalid", 403)
        return {"review_key": row["RowKey"], **json.loads(row["binding"])}

    def new(self, issue, route, binding, etag):
        key = digest(["readiness", issue["id"], binding, str(uuid4())])
        row = {"RowKey": key, "issue_id": issue["id"], "identifier": issue["identifier"],
               "project_id": route.project_id, "repository": route.repository,
               "binding": json.dumps(binding), "status": "pending", "owner": "",
               "created_at": int(self.clock()), "lease_until": 0, "retry_after": 0}
        cursor = {"RowKey": self.pointer(issue), "review_key": key, "issue_id": issue["id"]}
        self.table.transaction([(cursor, etag), (row, None)])
        return row

    def update(self, key, *, expected=None, **fields):
        for _ in range(32):
            row, etag = self.table.get(key)
            # Ignore server-managed timestamps; compare all application fields.
            if expected is not None and (
                    {k: v for k, v in row.items() if k not in {"Timestamp", "PartitionKey"}}
                    != {k: v for k, v in expected.items() if k not in {"Timestamp", "PartitionKey"}}):
                raise WorkerError("Readiness observation was superseded")
            row.update(fields)
            try:
                self.table.replace(row, etag)
                return row
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Readiness state changed concurrently; retry")

    def comment(self, issue, body, row=None):
        # Each attempt keeps its discussion, including replies to old rejections.
        key = row["RowKey"] if row else issue["id"]
        self.linear.status_comment(issue["id"], comment_id(key, "readiness"),
                                   "Clusterfork readiness review: " + redact_review_text(body))

    def retry(self, row, reason):
        return self.update(row["RowKey"], expected=row, status="retry", reason=reason, lease_until=0,
                           retry_after=int(self.clock()) + RETRY_SECONDS, reported=False)

    def report_hold(self, issue, row):
        current, _ = self.table.get(row["RowKey"])
        pointer, _ = self.table.get(self.pointer(issue))
        if current["status"] in DECISIONS or pointer["review_key"] != row["RowKey"]:
            return
        self.comment(issue, (row.get("reason") or "Waiting for the read-only reviewer.")
                     + "\n\nCoding and publication are held. The timer retries temporary failures "
                     "after 15 minutes, once the previous GitHub attempt has stopped. "
                     "An unconfirmed dispatch is held for run discovery; it is never blindly resent."
                     + self.run_link(row), row)
        # A callback can finish while the Linear request is in flight. Repair a
        # late hold message without reapplying labels or a state transition.
        latest, _ = self.table.get(row["RowKey"])
        if latest["status"] in DECISIONS:
            self.comment(issue, self.review_body(latest), latest)

    @staticmethod
    def run_link(row):
        if not row.get("owner"):
            return ""
        run, attempt = row["owner"].split(":")
        return f"\n\n[Readiness run](https://github.com/{row['repository']}/actions/runs/{run}/attempts/{attempt})"

    @classmethod
    def review_body(cls, row):
        if row["status"] == "rejected":
            # Do not parse rejected output or assume it matches the result schema.
            snapshot = row["rejected_result"].replace("`", "\\u0060")
            body = ("Rejected: invalid reviewer result. Validation failed: " + row["reason"]
                    + "\n\nRejected reviewer output (credentials redacted):\n```json\n" + snapshot + "\n```")
        else:
            result = json.loads(row["result"])
            body = ("Passed. " if row["status"] == "passed" else "Needs clarification. ") + result["summary"]
            for number, blocker in enumerate(result["blockers"], 1):
                body += (f"\n\n{number}. {blocker['decision']}\n"
                         f"Consequence: {blocker['consequence']}\nQuestion: {blocker['question']}")
            if result["policy_references"]:
                body += "\n\nRepository policy/context: " + "; ".join(result["policy_references"])
        binding = json.loads(row["binding"])
        body += (f"\n\nIssue content revision: `{binding['issue_revision']}`; "
                 f"policy: `{binding['policy_version']}`; repository: `{binding['context_sha']}`.")
        if row["status"] == "passed":
            body += "\n\nReady for development in the admitted repository slot. This is not a security certification."
        else:
            body += ("\n\nBlocked; this review will not retry automatically. Address the feedback in the "
                     "description or comments, or record an explicit decision in a reply. For invalid output, "
                     "inspect the validation error and review run. Move this issue from Blocked to Todo "
                     "to request another review; the reviewer will read this discussion.")
        return body + cls.run_link(row)

    def sync(self, row, issue, route):
        if row.get("reported") or not self.current(row, issue, route):
            return
        current, _ = eligibility(issue, self.registry)
        if current != route:
            return  # Preserve cancellation, archive, project moves and human status changes.
        if row["status"] in REJECTIONS:
            self.review_selection(issue, route, True)
        self.comment(issue, self.review_body(row), row)
        latest = self.linear.issue(issue["id"])
        if (not latest or latest.get("state") != issue.get("state")
                or eligibility(latest, self.registry)[0] != route or not self.current(row, latest, route)):
            return
        self.linear.set_clarification(issue["id"], row["status"] == "needs_clarification")
        if row["status"] in REJECTIONS:
            self.linear.set_stage(issue["id"], None)
            blocked = route.linear_states.blocked if route.linear_states else self.linear.blocked_state(issue["id"])
            latest = self.linear.issue(issue["id"])
            if (not latest or latest.get("state") != issue.get("state")
                    or eligibility(latest, self.registry)[0] != route or not self.current(row, latest, route)):
                return
            # Save the intent so a lost state-update response can be recovered.
            self.update(row["RowKey"], rejection_target=blocked)
            self.linear.set_state(issue["id"], blocked)
            self.update(row["RowKey"], applied=True, reported=True)
        else:
            self.update(row["RowKey"], reported=True)

    def stopped(self, row, route):
        runs = self.github.dispatch_runs(route, row["RowKey"])
        if any(run["status"] != "completed" for run in runs):
            return False, runs
        if row.get("owner") and self.github.worker_result(route.repository, row["owner"])["status"] != "completed":
            return False, runs
        return True, runs

    def prework_stopped(self, issue_id, route):
        pointer, _ = optional(self.table, self.pointer({"id": issue_id}))
        row, _ = optional(self.table, pointer["review_key"]) if pointer else (None, None)
        if not row or row["status"] == "pending":
            return True
        stopped, runs = self.stopped(row, route)
        # A dispatching/uncertain attempt without a known run may still arrive.
        return stopped and bool(runs or row.get("owner") or row["status"] == "retry")

    def ensure(self, issue, route):
        if route.workflow != "clusterfork-worker.yml":
            return {"status": "ready"}
        for _ in range(32):
            try:
                return self._ensure(issue, route)
            except WorkerError as exc:
                if exc.status == 409:
                    continue
                raise
            except Exception:
                # Infrastructure cannot become a pass or a clarification verdict.
                logging.error("Readiness review deferred for %s", issue["id"])
                self.comment(issue, "Infrastructure/context lookup failed. Held for a visible retry "
                             "on the next event or timer; coding and publication have not been admitted.")
                return {"status": "readiness_retry"}
        raise WorkerError("Readiness review changed concurrently; retry")

    def _ensure(self, issue, route):
        if eligibility(issue, self.registry)[0] != route:
            return {"status": "not_ready"}
        if not self.admitted(issue, route):
            return {"status": "queued"}
        binding = self.binding(issue, route)
        pointer, etag = optional(self.table, self.pointer(issue))
        row, _ = optional(self.table, pointer["review_key"]) if pointer else (None, None)
        if row and row["status"] in REJECTIONS and row.get("applied"):
            # Another delivery may have fetched Todo before our rejection completed.
            # Only a fresh Todo observation is a resubmission, never that stale snapshot.
            latest = self.linear.issue(issue["id"])
            if not latest or eligibility(latest, self.registry)[0] != route:
                return {"status": "not_ready"}
            if issue_revision(latest) != issue_revision(issue):
                return self.ensure(latest, route)
            if not self.stopped(row, route)[0]:
                return {"status": "readiness_pending"}
        if (not row or json.loads(row["binding"]) != binding
                or (row["status"] in REJECTIONS and row.get("applied"))):
            row = self.new(issue, route, binding, etag)
        if row["status"] in DECISIONS:
            self.sync(row, issue, route)
            return {"status": "ready" if row["status"] == "passed" else row["status"]}
        if row["status"] == "running" and (
                row["lease_until"] <= self.clock() or row["deadline"] <= self.clock()):
            row = self.retry(row, "Reviewer timed out or lost its lease; no readiness decision was accepted.")
        if row["status"] == "retry" and row["retry_after"] <= self.clock():
            stopped, _ = self.stopped(row, route)
            if stopped:
                row = self.new(issue, route, binding, etag)
        if row["status"] == "pending":
            # Branch/context lookup and prior-attempt reconciliation can take
            # time. Recheck current dependencies immediately before dispatch.
            if not self.admitted(issue, route):
                return {"status": "queued"}
            current, version = self.table.get(row["RowKey"])
            if current["status"] != "pending":
                raise WorkerError("Another caller already claimed this review dispatch")
            current["status"] = "dispatching"
            self.table.replace(current, version)  # Exactly one caller crosses dispatch.
            row = current
            self.review_selection(issue, route, False)
            try:
                self.github.dispatch(route, {
                    "linear_issue_id": issue["id"], "linear_identifier": issue["identifier"],
                    "linear_project_id": route.project_id, "dispatch_key": row["RowKey"],
                })
            except DispatchError as exc:
                if exc.uncertain:
                    row = self.update(row["RowKey"], expected=row, status="uncertain",
                                      reason="Readiness dispatch is unconfirmed; waiting for GitHub run discovery.")
                else:
                    row = self.retry(row, "Readiness runner dispatch failed; awaiting retry.")
            else:
                # A fast worker may already have completed. Never overwrite its result.
                current, version = self.table.get(row["RowKey"])
                if current["status"] == "dispatching":
                    current["status"] = "dispatched"
                    self.table.replace(current, version)
                row, _ = self.table.get(row["RowKey"])
        elif row["status"] in {"dispatched", "dispatching", "uncertain"}:
            stopped, runs = self.stopped(row, route)
            if stopped and runs:
                row = self.retry(row, "Reviewer stopped without a valid result; awaiting retry.")
        if row["status"] in DECISIONS:
            self.sync(row, issue, route)
            return {"status": "ready" if row["status"] == "passed" else row["status"]}
        self.report_hold(issue, row)
        return {"status": "readiness_retry" if row["status"] == "retry" else "readiness_pending"}

    def handle(self, action, claims, data, row):
        route = authorize_claims(claims, self.registry, row)
        selector = is_account_selector(claims)
        if selector and action not in SELECTOR_ACTIONS:
            raise WorkerError("Account preflight cannot execute or publish agent work", 403)
        owner = f"{claims['run_id']}:{claims['run_attempt']}"
        issue = self.linear.issue(row["issue_id"])
        # Always permit fenced account cleanup, even after edits/cancellation.
        if action == "save":
            return account_request(self.accounts, action, data, row, self.account_owner(row, owner),
                                   selector=selector, run_owner=owner)
        if not issue or eligibility(issue, self.registry)[0] != route or not self.current(row, issue, route):
            raise WorkerError("Readiness request was superseded or canceled", 403)
        if action in {"start", "checkout", "accounts", "claim", "bind_account"} and not self.admitted(issue, route):
            raise WorkerError("Readiness start held for unresolved or unverifiable issue prerequisites", 403)
        if action == "start":
            if data.get("protocol") != PROTOCOL:
                raise WorkerError("Deploy a compatible readiness worker and broker", 403)
            self.github.verify_worker_run(route.repository, claims, row["RowKey"])
            if row["status"] in DECISIONS:
                raise WorkerError("This review already has a result")
            if row["status"] == "retry" and row.get("owner") == owner:
                raise WorkerError("This readiness attempt already failed; await a retry")
            if row.get("owner") and row["owner"] != owner:
                if self.github.worker_result(route.repository, row["owner"])["status"] != "completed":
                    raise WorkerError("Previous readiness worker has not stopped")
            # A complete, paginated discussion snapshot is required for assessment.
            # Failure leaves the attempt retryable, never silently description-only.
            comments = sorted(self.linear.issue_comments(issue["id"]),
                              key=lambda comment: (comment["createdAt"], comment["id"]))
            current, version = self.table.get(row["RowKey"])
            if current != row:
                raise WorkerError("Readiness worker changed concurrently")
            if row.get("owner") != owner or row["status"] != "running":
                row["deadline"] = int(self.clock()) + REVIEW_TIMEOUT_SECONDS + 120
            if row.get("owner") != owner:
                row.pop("assigned_account_id", None)
                row.pop("execution_started", None)
            if not selector and row.get("assigned_account_id") and not row.get("execution_started"):
                row.update(execution_started=True, deadline=int(self.clock()) + REVIEW_TIMEOUT_SECONDS + 120)
            lease_until = int(self.clock()) + LEASE_SECONDS
            if selector and row.get("assigned_account_id"):
                lease_until = max(row["lease_until"], lease_until)
            row.update(status="running", owner=owner, lease_until=lease_until)
            self.table.replace(row, version)
            profile = self.registry.agent_profiles["codex"]
            return {"protocol": PROTOCOL, "phase": "readiness", "model": profile.model,
                    "account_selection": selector, "account_id": row.get("assigned_account_id"),
                    "effort": profile.effort, "repository": route.repository, "ref": route.ref,
                    "binding": json.loads(row["binding"]), "review_timeout_seconds": REVIEW_TIMEOUT_SECONDS,
                    "selection": {"min_remaining_percent": self.registry.agent_selection.min_remaining_percent,
                                  "max_snapshot_age_seconds": self.registry.agent_selection.max_snapshot_age_seconds},
                    "issue": {**{k: issue.get(k) for k in ("id", "identifier", "title", "description", "url")},
                              "comments": [{k: comment.get(k) for k in
                                            ("id", "body", "createdAt", "user", "botActor", "parent")}
                                           for comment in comments]}}
        if (action == "finish" and data.get("status") in {"failed", "waiting_for_quota"}
                and row.get("owner") == owner and row["status"] == "running"):
            reason = "Reviewer failed or account capacity was unavailable; awaiting retry."
            if data.get("status") == "failed" and "failure" in data:
                from diagnostics import failure_summary, validate_failure

                diagnostic = validate_failure(data["failure"])
                diagnostic.update(dispatch_key=row["RowKey"], run_id=claims["run_id"], run_attempt=claims["run_attempt"])
                row = self.update(row["RowKey"], expected=row, failure=json.dumps(diagnostic))
                reason = failure_summary(diagnostic)
            row = self.retry(row, reason)
            self.report_hold(issue, row)
            return {"status": "readiness_retry"}
        if (row.get("owner") != owner or row["status"] != "running"
                or row["lease_until"] <= self.clock() or row["deadline"] <= self.clock()):
            raise WorkerError("Readiness worker lease was lost")
        if action == "github":
            raise WorkerError("Readiness reviewers cannot obtain publication credentials", 403)
        if action == "checkout":
            return {"token": self.github.installation_token(route.repository, permissions={"contents": "read"})}
        if action in {"accounts", "claim", "bind_account"}:
            current, version = self.table.get(row["RowKey"])
            if current != row:
                raise WorkerError("Readiness account selection was superseded")
            was_assigned = bool(row.get("assigned_account_id"))
            result = account_request(self.accounts, action, data, row, self.account_owner(row, owner),
                                     selector=selector, run_owner=owner)
            if action == "bind_account":
                if not was_assigned:
                    row["lease_until"] = int(self.clock()) + ACCOUNT_HANDOFF_SECONDS
                    row["deadline"] = row["lease_until"] + REVIEW_TIMEOUT_SECONDS + 120
                self.table.replace(row, version)
            return result
        if action == "heartbeat":
            current, version = self.table.get(row["RowKey"])
            if current != row:
                raise WorkerError("Readiness worker changed concurrently")
            row["lease_until"] = max(row["lease_until"], int(self.clock()) + LEASE_SECONDS)
            self.table.replace(row, version)
            return {"running": True}
        if action == "review":
            try:
                if data.get("binding") != json.loads(row["binding"]):
                    raise ValueError("Review binding mismatch")
                output_error = data.get("result_error")
                if output_error:
                    errors = {"invalid_json": "Readiness output is not valid JSON",
                              "missing_output": "Readiness output file is missing or unreadable",
                              "output_too_large": "Readiness output exceeds the 28,000-byte output limit",
                              "invalid_encoding": "Readiness output is not valid UTF-8"}
                    reason = errors.get(output_error, "Unrecognized readiness output error")
                    location = data.get("result_location")
                    if (output_error == "invalid_json" and isinstance(location, dict)
                            and all(type(location.get(k)) is int and 0 < location[k] <= 28_001
                                    for k in ("line", "column"))):
                        reason += f" at line {location['line']}, column {location['column']}"
                    raise ValueError(reason)
                result = validate_result(data.get("result"))
            except (ValueError, TypeError) as exc:
                # Only our validator's diagnostics are public; never forward raw exceptions.
                reason = str(exc) if isinstance(exc, ValueError) else "result: expected JSON-compatible review data"
                row = self.update(row["RowKey"], expected=row, status="rejected", reason=reason,
                                  rejected_result=result_snapshot(data.get("result")), lease_until=0,
                                  retry_after=0, reported=False)
                self.sync(row, issue, route)
                return {"status": "rejected"}
            if result["verdict"] == "retry":
                row = self.update(row["RowKey"], expected=row,
                                  result=json.dumps(redact_review_value(result), ensure_ascii=False))
                row = self.retry(row, "Reviewer could not complete the assessment. " + result["summary"])
                self.report_hold(issue, row)
                return {"status": "readiness_retry"}
            # The ETag fences result acceptance against timeout and manual reruns.
            current, version = self.table.get(row["RowKey"])
            if current != row:
                raise WorkerError("Readiness result was superseded")
            row.update(status="passed" if result["verdict"] == "ready" else "needs_clarification",
                       result=json.dumps(redact_review_value(result), ensure_ascii=False), lease_until=0)
            self.table.replace(row, version)
            self.sync(row, issue, route)
            return {"status": row["status"]}
        raise WorkerError("Action is not permitted for a readiness reviewer", 403)

    @staticmethod
    def account_owner(row, owner):
        return f"readiness:{row['RowKey']}:{owner}"

    def reconcile(self):
        for pointer in self.table.list():
            if not pointer["RowKey"].startswith("issue-"):
                continue
            try:
                row, _ = self.table.get(pointer["review_key"])
                if ((row["status"] == "passed" and row.get("reported"))
                        or (row["status"] in REJECTIONS and row.get("applied"))):
                    continue  # Issue events/project scans discover Todo reentry.
                issue = self.linear.issue(pointer["issue_id"])
                route, _ = eligibility(issue or {}, self.registry)
                if (issue and row.get("rejection_target") == issue["state"]["id"]
                        and row["status"] in REJECTIONS and not row.get("applied")):
                    self.update(row["RowKey"], applied=True, reported=True)
                elif route and (row["status"] != "passed" or not row.get("reported")):
                    # A reported pass is rechecked by queue admission/dispatch,
                    # not restarted here: its coding receipt may already be complete.
                    self.ensure(issue, route)
            except Exception:
                logging.error("Readiness reconciliation deferred for %s", pointer["issue_id"])
