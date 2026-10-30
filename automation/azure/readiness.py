"""Durable, broker-enforced readiness reviews, independent of coding receipts."""

import json
import logging
import time
from uuid import uuid4

from dispatch_core import DispatchError, eligibility
from deployment_control import build_metadata
from linear_sync import comment_id
from issue_images import ImageContextError, capture_images, image_digest
from readiness_policy import (PROTOCOL, RETRY_SECONDS, REVIEW_TIMEOUT_SECONDS, digest,
                              issue_revision, policy_version, redact_review_text, redact_review_value,
                              result_snapshot, validate_result)
from worker_core import (ACCOUNT_HANDOFF_SECONDS, LEASE_SECONDS, SELECTOR_ACTIONS, WorkerError,
                         WORKER_REPOSITORY, account_request, authorize_claims, is_account_selector, execution_profiles)
from diagnostics import failure, record

REJECTIONS = {"needs_clarification", "rejected"}
DECISIONS = {"passed", *REJECTIONS}
DEPLOYMENT_WAIT = ("Azure deployment paused new workers before readiness assessment. Waiting for successful "
                   "deployment and explicit resume, then confirmation that all previous GitHub attempts stopped.")


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
        self.admission_reason = lambda issue, route, **kwargs: "repository_admission_lost"
        self.deployment_paused = lambda: False
        self.review_selection = lambda issue, route, rejected: None

    def pointer(self, issue):
        return "issue-" + digest(issue["id"])

    def images(self, issue):
        try:
            return capture_images(issue.get("description"),
                                  lambda url, **kwargs: self.linear.download_image(url, **kwargs))
        except ImageContextError as exc:
            reason = (str(exc) + "\n\nThe description images could not all be supplied as visual inputs; "
                      "no visual review is claimed. Coding and publication are held. "
                      "Upload accessible PNG, JPEG, or still WebP images within the documented limits. "
                      "Temporary retrieval failures retry on the next event or timer.")
            self.comment(issue, reason)
            raise WorkerError("Issue description images are unavailable; see the Linear readiness comment", 503) from None

    def binding(self, issue, route, *, images=None):
        return {"issue_revision": issue_revision(issue),
                "policy_version": policy_version(self.registry, route),
                "images_sha": image_digest(self.images(issue) if images is None else images),
                # Conservatively version all repository rules and relevant code.
                "context_sha": self.github.branch_sha(route)}

    def current(self, row, issue, route, *, images=None, verify_images=True):
        pointer, _ = optional(self.table, self.pointer(issue))
        if not pointer or pointer["review_key"] != row["RowKey"]:
            return False
        binding = json.loads(row["binding"])
        observed = self.binding(issue, route, images=images if verify_images else [])
        if not verify_images:
            # Heartbeats cannot admit work or accept a result. Recheck bytes at
            # admission, account access, review acceptance, coding and publication.
            observed["images_sha"] = binding.get("images_sha")
        return (pointer and pointer["review_key"] == row["RowKey"]
                and binding == observed)

    def require(self, issue, route, *, images=None):
        if route.workflow != "clusterfork-worker.yml":
            return {}
        pointer, _ = optional(self.table, self.pointer(issue))
        row, _ = optional(self.table, pointer["review_key"]) if pointer else (None, None)
        if not row or row["status"] != "passed" or not self.current(row, issue, route, images=images):
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
        if row and row["status"] in REJECTIONS and self.registry.notify_user_url:
            body += "\n\n" + self.registry.notify_user_url
        self.linear.status_comment(issue["id"], comment_id(key, "readiness"),
                                   "Clusterfork readiness review: " + redact_review_text(body))

    def retry(self, row, reason):
        return self.update(row["RowKey"], expected=row, status="retry", reason=reason, lease_until=0,
                           retry_after=int(self.clock()) + RETRY_SECONDS, reported=False)

    def defer_deployment(self, row, *, owner=None):
        fields = {"status": "deployment_deferred", "reason_code": "deployment_paused",
                  "reason": DEPLOYMENT_WAIT, "lease_until": 0, "retry_after": 0}
        if owner is not None:
            fields["owner"] = owner
        if all(row.get(key) == value for key, value in fields.items()):
            return row
        row = self.update(row["RowKey"], expected=row, **fields)
        record("readiness_deferred", dispatch_key=row["RowKey"], reason_code="deployment_paused")
        return row

    @staticmethod
    def deployment_result():
        return {"status": "deployment_deferred", "reason_code": "deployment_paused"}

    @staticmethod
    def reporting_failure(exc):
        record("readiness_reporting_deferred", level=logging.ERROR, **failure(exc, "worker/linear_sync"))

    def report_decision(self, row, issue, route):
        try:
            self.sync(row, issue, route)
        except Exception as exc:
            # The verdict/dispatch receipt survives a tracker reporting outage.
            self.reporting_failure(exc)

    def report_hold(self, issue, row):
        current, _ = self.table.get(row["RowKey"])
        pointer, _ = self.table.get(self.pointer(issue))
        if current["status"] in DECISIONS or pointer["review_key"] != row["RowKey"]:
            return
        row = current
        recovery = ("\n\nDeployment resume triggers reconciliation without the readiness failure cooldown. "
                    "The runner exits so deployment can drain."
                    if row["status"] == "deployment_deferred" else
                    "\n\nCoding and publication are held. The timer retries temporary failures "
                    "after 15 minutes, once the previous GitHub attempt has stopped.")
        try:
            self.comment(issue, (row.get("reason") or "Waiting for the read-only reviewer.") + recovery
                         + " An unconfirmed dispatch is held for run discovery; it is never blindly resent."
                         + self.run_link(row), row)
        except Exception as exc:
            self.reporting_failure(exc)
        # A callback can finish while the Linear request is in flight. Repair a
        # late hold message without reapplying labels or a state transition.
        latest, _ = self.table.get(row["RowKey"])
        if latest["status"] in DECISIONS:
            try:
                self.comment(issue, self.review_body(latest), latest)
            except Exception as exc:
                self.reporting_failure(exc)
        elif latest["status"] == "deployment_deferred" and row["status"] != latest["status"]:
            self.report_hold(issue, latest)

    @staticmethod
    def run_link(row):
        if not row.get("owner"):
            return ""
        run, attempt = row["owner"].split(":")
        return f"\n\n[Readiness run](https://github.com/{row['repository']}/actions/runs/{run}/attempts/{attempt})"

    @classmethod
    def review_body(cls, row):
        # Detailed evidence stays in the durable row. Model prose can include
        # audit inventories or claims about tests a read-only review cannot run.
        if row["status"] == "passed":
            body = ("Ready for development. The requirements are clear and the repository provides "
                    "the needed context. Tests will run during development.")
        elif row["status"] == "rejected":
            # Do not parse rejected output or assume it matches the result schema.
            body = "Rejected: invalid reviewer result. Reviewer output error: " + row["reason"]
        else:
            result = json.loads(row["result"])
            body = "Needs clarification. " + result["summary"]
            for number, blocker in enumerate(result["blockers"], 1):
                body += (f"\n\n{number}. {blocker['decision']}\n"
                         f"Consequence: {blocker['consequence']}\nQuestion: {blocker['question']}")
        if row["status"] in REJECTIONS:
            body += ("\n\nNo tests were run during this read-only review. "
                     "Blocked; this review will not retry automatically. Address the feedback in the "
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
        if row.get("owner"):
            # A manual rerun can be newer than the recorded owner and still be
            # missing from listings. Check the run's current attempt on resume.
            result = (self.github.worker_result(route.repository, row["owner"], latest=True)
                      if row["status"] == "deployment_deferred"
                      else self.github.worker_result(route.repository, row["owner"]))
            if result["status"] != "completed":
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
                if exc.status == 503:
                    return {"status": "readiness_retry"}
                raise
            except Exception as exc:
                # Infrastructure cannot become a pass or a clarification verdict.
                record("readiness_context_deferred", level=logging.ERROR, **failure(exc, "worker/readiness"))
                try:
                    self.comment(issue, "Infrastructure/context lookup failed. Held for a visible retry "
                                 "on the next event or timer; coding and publication have not been admitted.")
                except Exception as report_error:
                    self.reporting_failure(report_error)
                return {"status": "readiness_retry"}
        raise WorkerError("Readiness review changed concurrently; retry")

    def _ensure(self, issue, route):
        if eligibility(issue, self.registry)[0] != route:
            return {"status": "not_ready"}
        admission = self.admission_reason(issue, route)
        if not admission:
            binding = self.binding(issue, route)
            revision = build_metadata().get("revision")
            if (revision and route.repository.lower() == WORKER_REPOSITORY.lower() and route.ref == "main"
                    and binding["context_sha"] != revision):
                admission = "deployment_paused"
        if admission == "deployment_paused":
            pointer, _ = optional(self.table, self.pointer(issue))
            row, _ = optional(self.table, pointer["review_key"]) if pointer else (None, None)
            if row and (row["status"] in {"dispatching", "dispatched", "uncertain", "deployment_deferred"}
                        or (row["status"] == "running" and not row.get("execution_started"))):
                self.report_hold(issue, self.defer_deployment(row))
            return self.deployment_result()
        if admission:
            return {"status": "queued"}
        pointer, etag = optional(self.table, self.pointer(issue))
        row, _ = optional(self.table, pointer["review_key"]) if pointer else (None, None)
        if row and row["status"] == "deployment_deferred":
            stopped, runs = self.stopped(row, route)
            if not stopped or not (runs or row.get("owner")):
                self.report_hold(issue, row)
                return self.deployment_result()
            # A replacement always binds to current issue/policy/repository data.
            row = self.new(issue, route, binding, etag)
        if row and row["status"] in REJECTIONS and row.get("applied"):
            # Another delivery may have fetched Todo before our rejection completed.
            # Only a fresh Todo observation is a resubmission, never that stale snapshot.
            latest = self.linear.issue(issue["id"])
            if not latest or eligibility(latest, self.registry)[0] != route:
                return {"status": "not_ready"}
            if issue_revision(latest) != issue_revision(issue):
                return self.ensure(latest, route)
        if (not row or json.loads(row["binding"]) != binding
                or (row["status"] in REJECTIONS and row.get("applied"))):
            if row and row["status"] != "pending":
                stopped, runs = self.stopped(row, route)
                if not stopped or not (runs or row.get("owner") or row["status"] == "retry"):
                    # Editing a binding cannot bypass uncertain-dispatch/run
                    # discovery, or overlap an older matching GitHub attempt.
                    self.report_hold(issue, row)
                    return {"status": "readiness_pending"}
            row = self.new(issue, route, binding, etag)
        if row["status"] in DECISIONS:
            self.report_decision(row, issue, route)
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
            self.report_decision(row, issue, route)
            return {"status": "ready" if row["status"] == "passed" else row["status"]}
        self.report_hold(issue, row)
        if row["status"] == "deployment_deferred":
            return self.deployment_result()
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
        if not issue or eligibility(issue, self.registry)[0] != route:
            raise WorkerError("Readiness request was superseded or canceled", 403)
        if action == "start" and row["status"] in DECISIONS:
            raise WorkerError("This review already has a result")
        if action == "start" and row["status"] == "retry" and row.get("owner") == owner:
            raise WorkerError("This readiness attempt already failed; await a retry")
        if action in {"start", "checkout", "accounts", "claim", "bind_account"}:
            admission = self.admission_reason(issue, route, allow_paused=bool(
                row["status"] == "running" and row.get("execution_started") and row.get("owner") == owner))
            if admission == "deployment_paused":
                if row["status"] not in DECISIONS | {"retry"}:
                    self.report_hold(issue, self.defer_deployment(row, owner=owner))
                return self.deployment_result()
            if admission:
                message = ("Readiness start held for unresolved or unverifiable issue prerequisites"
                           if admission == "prerequisites_unresolved" else "Readiness repository admission was lost")
                raise WorkerError(message, 403, reason_code=admission)
        images = self.images(issue) if action == "start" else None
        if not self.current(row, issue, route, images=images, verify_images=action != "heartbeat"):
            raise WorkerError("Readiness request was superseded or canceled", 403)
        if row["status"] == "deployment_deferred":
            return self.deployment_result()
        if action == "start":
            self.github.verify_worker_run(route.repository, claims, row["RowKey"])
            if row.get("owner") and row["owner"] != owner:
                if self.github.worker_result(route.repository, row["owner"])["status"] != "completed":
                    raise WorkerError("Previous readiness worker has not stopped")
            # A complete, paginated discussion snapshot is required for assessment.
            # Failure leaves the attempt retryable, never silently description-only.
            comments = sorted(self.linear.issue_comments(issue["id"]),
                              key=lambda comment: (comment["createdAt"], comment["id"]))
            if self.deployment_paused() and not row.get("execution_started"):
                self.report_hold(issue, self.defer_deployment(row, owner=owner))
                return self.deployment_result()
            if data.get("protocol") != PROTOCOL:
                raise WorkerError("Deploy a compatible readiness worker and broker", 403)
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
            profiles = execution_profiles(self.registry, row)
            return {"protocol": PROTOCOL, "phase": "readiness", **profiles,
                    "images": images,
                    "account_selection": selector, "account_id": row.get("assigned_account_id"),
                    "repository": route.repository, "ref": route.ref,
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
            if data.get("status") == "waiting_for_quota" and data.get("quota_wait"):
                from worker_reporting import quota_wait_text
                reason += " " + quota_wait_text(data["quota_wait"])
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

    def reconcile(self, *, deployment_only=False):
        pending = False
        for pointer in self.table.list():
            if not pointer["RowKey"].startswith("issue-"):
                continue
            try:
                row, _ = self.table.get(pointer["review_key"])
                if deployment_only and row["status"] != "deployment_deferred":
                    continue
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
                    result = self.ensure(issue, route)
                    pending |= result["status"] in {"deployment_deferred", "readiness_retry"}
            except Exception:
                logging.error("Readiness reconciliation deferred for %s", pointer["issue_id"])
                pending = True
        return pending
