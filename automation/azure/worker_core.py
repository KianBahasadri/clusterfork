"""Authorization, credential leases, and publication for trusted Codex jobs."""

from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4

from dispatch_core import eligibility
from worker_reporting import STAGES, is_auto_repair, validate_usage
from readiness_policy import PROTOCOL
from diagnostics import failure, operation, record, validate_failure

AUDIENCE = "clusterfork-worker"
ISSUER = "https://token.actions.githubusercontent.com"
LEASE_SECONDS = 120
ACCOUNT_HANDOFF_SECONDS = 900
SELECTOR_WORKFLOW = "clusterfork-select-account.yml"
SELECTOR_ACTIONS = {"start", "heartbeat", "accounts", "claim", "save", "bind_account", "finish"}


class WorkerError(RuntimeError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def account_identity(auth):
    if not isinstance(auth, dict) or auth.get("auth_mode") != "chatgpt":
        raise WorkerError("Only saved ChatGPT Codex accounts are supported", 400)
    tokens = auth.get("tokens", {})
    if not all(isinstance(tokens.get(k), str) and tokens[k] for k in
               ("account_id", "access_token", "refresh_token")):
        raise WorkerError("Incomplete Codex account credentials", 400)
    if len(json.dumps(auth)) > 30_000:
        raise WorkerError("Account credentials are too large", 400)
    return hashlib.sha256(tokens["account_id"].encode()).hexdigest()


def authorize_claims(claims, registry, receipt):
    """Only call after cryptographic verification; bind claims to a durable dispatch."""
    route = registry.routes.get(receipt.get("project_id"))
    if not route or receipt.get("repository") != route.repository:
        raise WorkerError("Dispatch is not mapped to this repository", 403)
    expected = {
        "repository": route.repository, "ref": f"refs/heads/{route.ref}",
        "event_name": "workflow_dispatch", "runner_environment": "github-hosted",
        "workflow_ref": f"{route.repository}/.github/workflows/{route.workflow}@refs/heads/{route.ref}",
    }
    if route.workflow != "clusterfork-worker.yml" or any(claims.get(k) != v for k, v in expected.items()):
        raise WorkerError("Workflow identity is not authorized", 403)
    if claims.get("job_workflow_ref") and claims["job_workflow_ref"] not in {
            expected["workflow_ref"], selector_workflow_ref(claims)}:
        raise WorkerError("Reusable workflow identity is not authorized", 403)
    if is_account_selector(claims) and claims.get("job_workflow_sha") != claims.get("sha"):
        raise WorkerError("Account selector revision does not match the dispatched workflow", 403)
    for key in ("run_id", "run_attempt", "repository_id"):
        if not re.fullmatch(r"[1-9][0-9]*", str(claims.get(key, ""))):
            raise WorkerError("Invalid workflow identity", 403)
    if not re.fullmatch(r"[0-9a-f]{40}", claims.get("sha", "")):
        raise WorkerError("Invalid workflow revision", 403)
    return route


def selector_workflow_ref(claims):
    return f"{claims.get('repository')}/.github/workflows/{SELECTOR_WORKFLOW}@{claims.get('ref')}"


def is_account_selector(claims):
    # This role comes only from the verified JWT, never from request data.
    return claims.get("job_workflow_ref") == selector_workflow_ref(claims)


def account_request(accounts, action, data, row, owner, *, selector, run_owner=None):
    """Pool access is exclusive to preflight; bindings survive account release."""
    if row.get("owner") != (run_owner or owner):
        raise WorkerError("Account assignment belongs to another worker attempt", 403)
    assigned = row.get("assigned_account_id")
    if action == "accounts":
        if assigned:
            return {"accounts": [assigned]}
        if selector:
            return {"accounts": accounts.list()}
        raise WorkerError("This run has no assigned Codex account", 403)
    identity = data.get("account_id", "")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise WorkerError("Invalid account identity", 400)
    if (assigned or not selector) and identity != assigned:
        raise WorkerError("This run cannot access another Codex account", 403)
    if action == "claim":
        return accounts.claim(identity, owner)
    if action == "save":
        accounts.save(identity, owner, data.get("lease"), data.get("auth"),
                      release=data.get("release") is True)
        return {"saved": True}
    if not selector:
        raise WorkerError("Only account preflight can bind a Codex account", 403)
    accounts.table.get(identity)  # Do not bind an unknown/reclaimed profile.
    row["assigned_account_id"] = identity
    return {"account_id": identity}


class Accounts:
    """Each account has one fenced lease, including while its tokens refresh."""
    def __init__(self, table, clock=time.time):
        self.table, self.clock = table, clock

    def enroll(self, auth):
        identity = account_identity(auth)
        if not self.table.create({"RowKey": identity, "auth": json.dumps(auth),
                                  "owner": "", "fence": "", "lease_until": 0}):
            raise WorkerError("Account is already enrolled; do not overwrite refreshed cloud credentials")
        return identity

    def list(self):
        return [row["RowKey"] for row in self.table.list()]

    def claim(self, identity, owner):
        row, etag = self.table.get(identity)
        if row.get("lease_until", 0) > self.clock():
            raise WorkerError("Account is busy")
        fence = str(uuid4())
        row.update(owner=owner, fence=fence, lease_until=int(self.clock()) + LEASE_SECONDS)
        self.table.replace(row, etag)
        return {"auth": json.loads(row["auth"]), "lease": fence}

    def save(self, identity, owner, fence, auth=None, release=False):
        row, etag = self.table.get(identity)
        if (row.get("owner") != owner or row.get("fence") != fence
                or row.get("lease_until", 0) <= self.clock()):
            raise WorkerError("Account lease was lost")
        if auth is not None:
            if account_identity(auth) != identity:
                raise WorkerError("Refreshed credentials belong to another account", 400)
            row["auth"] = json.dumps(auth)
        row["lease_until"] = 0 if release else int(self.clock()) + LEASE_SECONDS
        if release:
            row.update(owner="", fence="")
        self.table.replace(row, etag)


class WorkerService:
    def __init__(self, registry, receipts, runs, accounts, linear, github, queue, clock=time.time, sync=None,
                 steering=None):
        self.registry, self.receipts, self.runs = registry, receipts, runs
        self.accounts, self.linear, self.github, self.clock = accounts, linear, github, clock
        self.sync = sync
        self.queue = queue
        if sync:
            self.queue.repair.lifecycle_notes = sync.notes
        self.steering = steering

    def sync_steering(self, issue_id):
        if self.steering:
            import logging
            try:
                self.steering.repair(issue_id)
            except Exception as exc:
                record("steering_sync_deferred", level=logging.ERROR, **failure(exc, "worker/steering_sync"))

    def sync_linear(self, key):
        if self.sync:
            import logging
            try:
                with operation("worker/linear_sync"):
                    self.sync.sync(key)
            except Exception as exc:
                # Tracker outages must not lose execution/publication outcomes.
                record("linear_sync_deferred", level=logging.ERROR, **failure(exc, "worker/linear_sync"))

    def handle(self, action, claims, data):
        key = data.get("dispatch_key", "")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise WorkerError("Invalid dispatch key", 400)
        if action == "start" and data.get("protocol") != PROTOCOL:
            raise WorkerError("Deploy a compatible readiness worker and broker", 403)
        try:
            receipt, _ = self.receipts.get(key)
        except WorkerError as exc:
            if exc.status != 404:
                raise
            review, _ = self.queue.readiness.table.get(key)
            route = authorize_claims(claims, self.registry, review)
            if is_account_selector(claims) and action not in SELECTOR_ACTIONS:
                raise WorkerError("Account preflight cannot execute or publish agent work", 403)
            owner = f"{claims['run_id']}:{claims['run_attempt']}"
            parent = hashlib.sha256(
                f"{self.registry.generation}:{route.project_id}:{review['issue_id']}".encode()
            ).hexdigest()
            if action == "start":
                self.github.verify_worker_run(route.repository, claims, key)
                self.queue.start_worker(route.repository, parent, owner)
                workflow_changes_allowed = self.github.workflow_changes_allowed(route.repository) is True
            elif action != "save":
                self.queue.assert_worker(route.repository, parent, owner)
            result = self.queue.readiness.handle(action, claims, data, review)
            if action == "start":
                result["serial_admission"] = True
                result["workflow_changes_allowed"] = workflow_changes_allowed
            if action == "review" and result.get("status") == "passed":
                issue = self.linear.issue(review["issue_id"])
                current, _ = eligibility(issue or {}, self.registry)
                if current:
                    self.queue.enqueue(issue, current)
                    self.queue.advance(current.repository)
            return result
        route = authorize_claims(claims, self.registry, receipt)
        selector = is_account_selector(claims)
        if selector and action not in SELECTOR_ACTIONS:
            raise WorkerError("Account preflight cannot execute or publish agent work", 403)
        owner = f"{claims['run_id']}:{claims['run_attempt']}"
        if action == "start":
            return self.start(key, owner, claims, receipt, route)
        self.queue.assert_worker(route.repository, key, owner)
        row, etag = self.runs.get(key)
        if (row.get("owner") != owner or row.get("status") != "running"
                or row.get("lease_until", 0) <= self.clock()):
            raise WorkerError("Worker lease was lost")
        row["lease_until"] = max(row["lease_until"], int(self.clock()) + LEASE_SECONDS)
        # The runner serializes broker requests; the ETag fences duplicate jobs.
        self.runs.replace(row, etag)
        if action in {"accounts", "claim", "bind_account", "github", "checkout"} or (
                action == "progress" and data.get("stage") in {"coding", "publishing"}):
            issue = self.linear.issue(receipt["issue_id"])
            extra = (route.linear_states.in_progress, route.linear_states.blocked) if route.linear_states else ()
            current, _ = eligibility(issue or {}, self.registry, additional_state_ids=extra)
            if current != route:
                raise WorkerError("Issue is no longer eligible", 403)
            approval = self.queue.approval(issue, route, receipt)
            if approval.get("review_key", "") != row.get("readiness_review_key", ""):
                raise WorkerError("Worker readiness approval was superseded", 403)
            if (action == "progress" and data.get("stage") == "coding" and approval.get("context_sha")
                    and data.get("context_sha") != approval["context_sha"]):
                raise WorkerError("Coding checkout does not match the reviewed repository context", 403)
            if not self.queue.dependencies_ready(
                    issue, route, key=key, owner=owner):
                raise WorkerError("Coding start held for unresolved or unverifiable issue prerequisites", 403)
        if action == "heartbeat":
            return {"running": True}
        if action in {"steering_open", "instructions", "instruction_result", "steering_close"}:
            if not self.steering:
                raise WorkerError("Instruction steering is unavailable", 503)
            if row.get("stage") != "coding" or row.get("steering_closed"):
                raise WorkerError("The agent is no longer accepting instructions")
            for field in ("thread_id", "turn_id"):
                value = data.get(field)
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
                    raise WorkerError("Invalid agent session identity", 400)
                if row.get(field, value) != value:
                    raise WorkerError("Agent session cannot be replaced within an attempt", 403)
            if action == "steering_open":
                row, etag = self.runs.get(key)
                if (row.get("owner") != owner or row.get("status") != "running" or row.get("steering_closed")
                        or any(row.get(field, data[field]) != data[field] for field in ("thread_id", "turn_id"))):
                    raise WorkerError("Agent session was superseded", 403)
                row.update(thread_id=data["thread_id"], turn_id=data["turn_id"])
                self.runs.replace(row, etag)
                return {"accepting": True}
            if not row.get("thread_id"):
                raise WorkerError("Agent session has not registered")
            if action == "steering_close":
                row, etag = self.runs.get(key)
                row["steering_closed"] = True
                self.runs.replace(row, etag)
                self.sync_steering(receipt["issue_id"])
                return {"accepting": False}
            if action == "instructions":
                return self.steering.poll(receipt, row)
            return self.steering.result(receipt, row, data)
        if action == "progress":
            stage = data.get("stage", row.get("stage", "initializing"))
            if stage not in STAGES or stage in {"review", "waiting_for_quota"}:
                raise WorkerError("Invalid worker stage", 400)
            row, etag = self.runs.get(key)
            if list(STAGES).index(stage) < list(STAGES).index(row.get("stage", "initializing")):
                raise WorkerError("Worker stage cannot move backwards", 400)
            if "account_id" in data:
                identity = data["account_id"]
                if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
                    raise WorkerError("Invalid account identity", 400)
                account, _ = self.accounts.table.get(identity)
                if account.get("owner") != owner or account.get("lease_until", 0) <= self.clock():
                    raise WorkerError("Selected account lease was lost")
                if (stage != "coding" or row.get("assigned_account_id") != identity
                        or row.get("account_id", identity) != identity):
                    raise WorkerError("Invalid account selection", 400)
                if not row.get("usage_before"):
                    row["usage_before"] = json.dumps(validate_usage(data.get("usage_before")))
                    row["account_id"] = identity
                    for field in ("account_email", "account_plan"):
                        value = data.get(field)
                        if value is not None and (not isinstance(value, str) or len(value) > 200):
                            raise WorkerError("Invalid account description", 400)
                        row[field] = value or ""
                    profile = self.registry.agent_profiles["codex"]
                    row.update(model=profile.model, effort=profile.effort)
            if "usage_after" in data:
                if not row.get("usage_before"):
                    raise WorkerError("Account selection was not recorded", 400)
                row["usage_after"] = json.dumps(validate_usage(data["usage_after"]))
            row.update(stage=stage, report_version=row.get("report_version", 0) + 1)
            if receipt.get("is_auto_repair") and data.get("repair_validation_passed") is True:
                if stage != "publishing":
                    raise WorkerError("Repair validation must finish before publication", 400)
                row["repair_validation_passed"] = True
            if receipt.get("is_auto_repair") and "repair_result" in data:
                result = data["repair_result"]
                if (stage != "publishing" or not isinstance(result, dict)
                        or not isinstance(result.get("commit_sha"), str)
                        or not re.fullmatch(r"[0-9a-f]{40}", result["commit_sha"])
                        or type(result.get("changed_files")) is not int or not 0 <= result["changed_files"] <= 100_000
                        or set(result) != {"commit_sha", "changed_files"}):
                    raise WorkerError("Invalid structured repair result", 400)
                row["repair_result"] = json.dumps(result)
            if stage in {"testing", "publishing"}:
                row["steering_closed"] = True
            if data.get("stage") == "coding" and not self.queue.dependencies_ready(
                    issue, route, key=key, owner=owner, coding=True):
                raise WorkerError("Coding start held for unresolved or unverifiable issue prerequisites", 403)
            self.runs.replace(row, etag)
            self.sync_linear(key)
            self.sync_steering(receipt["issue_id"])
            return {"stage": stage}
        if action in {"accounts", "claim", "save", "bind_account"}:
            row, etag = self.runs.get(key)
            was_assigned = bool(row.get("assigned_account_id"))
            result = account_request(self.accounts, action, data, row, owner, selector=selector)
            if action == "bind_account":
                if not was_assigned:
                    row["lease_until"] = int(self.clock()) + ACCOUNT_HANDOFF_SECONDS
                self.runs.replace(row, etag)
            return result
        if action == "checkout":
            return {"token": self.github.installation_token(
                route.repository, permissions={"contents": "read", "pull_requests": "read"})}
        if action == "github":
            issue = self.linear.issue(receipt["issue_id"])
            extra = (route.linear_states.in_progress,) if route.linear_states else ()
            current_route, _ = eligibility(issue or {}, self.registry, additional_state_ids=extra)
            if current_route != route:
                raise WorkerError("Issue is no longer eligible for publication", 403)
            permissions = {"contents": "write", "pull_requests": "write"}
            if self.github.workflow_changes_allowed(route.repository) is True:
                permissions["workflows"] = "write"
            return {"token": self.github.installation_token(
                route.repository, permissions=permissions)}
        if action == "finish":
            status = data.get("status")
            if status not in {"completed", "failed", "waiting_for_quota"}:
                raise WorkerError("Invalid worker outcome", 400)
            diagnostic = validate_failure(data["failure"]) if status == "failed" and "failure" in data else None
            if row.get("dependency_wait"):
                # The runner reports a denied pre-coding request as failure.
                # Keep that outcome retryable without treating it as coding failure.
                status, diagnostic = "waiting_for_dependencies", None
            row, etag = self.runs.get(key)
            row.update(status=status, lease_until=0, finished_at=int(self.clock()),
                       steering_closed=True,
                       report_version=row.get("report_version", 0) + 1)
            url = data.get("pull_request", "")
            if url and not re.fullmatch(re.escape(f"https://github.com/{route.repository}/pull/") + r"[1-9][0-9]*", url):
                raise WorkerError("Invalid pull request URL", 400)
            row["pull_request"] = url
            if diagnostic:
                # Run identity is authoritative; a callback cannot attribute a failure to another run.
                diagnostic.update(dispatch_key=key, run_id=claims["run_id"], run_attempt=claims["run_attempt"])
                row["failure"] = json.dumps(diagnostic)
            row["auto_merge_requested"] = route.auto_merge and status == "completed" and bool(url)
            self.runs.replace(row, etag)
            if status == "waiting_for_quota":
                for _ in range(32):
                    # A very fast quota wait can finish while the dispatcher is
                    # still persisting GitHub's acceptance. Merge that write;
                    # contention must not turn a capacity wait into a failure.
                    receipt, receipt_etag = self.receipts.get(key)
                    receipt["retry_after"] = int(self.clock()) + 900
                    try:
                        self.receipts.replace(receipt, receipt_etag)
                        break
                    except WorkerError as exc:
                        if exc.status != 409:
                            raise
                else:
                    raise WorkerError("Dispatch receipt changed concurrently; retry quota scheduling")
            self.sync_linear(key)
            self.sync_steering(receipt["issue_id"])
            return {"status": status}
        raise WorkerError("Unknown worker action", 404)

    def start(self, key, owner, claims, receipt, route):
        # Check the GitHub API as well as JWT claims, including who dispatched it.
        self.github.verify_worker_run(route.repository, claims, key)
        issue = self.linear.issue(receipt["issue_id"])
        extra = (route.linear_states.in_progress, route.linear_states.blocked) if route.linear_states else ()
        current, _ = eligibility(issue or {}, self.registry, additional_state_ids=extra)
        if current != route:
            raise WorkerError("Issue is no longer eligible", 403)
        if not route.validation_commands:
            raise WorkerError("Repository validation commands must be configured", 400)
        # Fence the admitted task before requesting any fresh review. A stale
        # manual rerun must not review a completed or no-longer-admitted task.
        self.queue.start_worker(route.repository, key, owner)
        try:
            previous, _ = self.runs.get(key)
        except WorkerError as exc:
            if exc.status != 404:
                raise
        else:
            if previous.get("status") == "completed":
                raise WorkerError("Issue already completed")
            if previous.get("owner") == owner and previous.get("status") != "running":
                raise WorkerError("This worker attempt already finished")
            if is_auto_repair(issue, receipt) and not (
                    previous.get("status") == "waiting_for_quota"
                    or (previous.get("status") == "running" and previous.get("owner") == owner)):
                raise WorkerError("The single auto-repair worker cannot be retried", 403)
        if not self.queue.dependencies_ready(issue, route, key=key, owner=owner):
            raise WorkerError("Coding start held for unresolved or unverifiable issue prerequisites", 403)
        try:
            approval = self.queue.approval(issue, route, receipt)
        except WorkerError:
            if receipt.get("is_auto_repair") and receipt.get("repair_parent_key"):
                raise
            self.queue.readiness.ensure(issue, route)
            # A dispatch can become stale while GitHub is scheduling it. Record
            # a retryable admission hold without starting coding or consuming an account.
            for _ in range(32):
                updated, etag = self.receipts.get(key)
                updated.update(readiness_wait_owner=owner, retry_after=int(self.clock()) + 900)
                try:
                    self.receipts.replace(updated, etag)
                    break
                except WorkerError as exc:
                    if exc.status != 409:
                        raise
            raise WorkerError("Coding start held for a current passing readiness review", 403) from None
        workflow_changes_allowed = self.github.workflow_changes_allowed(route.repository) is True
        row = {"RowKey": key, "owner": owner, "status": "running", "stage": "initializing",
               "attempt_id": str(uuid4()), "steering_started_at": self.clock(),
               "readiness_review_key": approval.get("review_key", ""),
               "started_at": int(self.clock()), "report_version": 0,
               "lease_until": int(self.clock()) + LEASE_SECONDS}
        if not self.runs.create(row):
            existing, etag = self.runs.get(key)
            if existing.get("status") == "completed":
                raise WorkerError("Issue already completed")
            # Repository admission checked the previous GitHub attempt, even
            # when its heartbeat expired or its finish callback already ran.
            if existing.get("owner") == owner:
                if existing.get("status") != "running":
                    raise WorkerError("This worker attempt already finished")
                row = {**existing, "lease_until": max(existing["lease_until"], row["lease_until"])}
            if not is_account_selector(claims) and row.get("assigned_account_id"):
                row.update(execution_started=True, lease_until=int(self.clock()) + LEASE_SECONDS)
            self.runs.replace(row, etag)
        self.sync_linear(key)
        self.sync_steering(receipt["issue_id"])
        profile = self.registry.agent_profiles["codex"]
        repair = ({name: receipt[name] for name in
                   ("target_branch", "target_dispatch_key", "target_identifier", "target_pull_request")}
                  if receipt.get("is_auto_repair") and receipt.get("repair_parent_key") else None)
        return {"protocol": PROTOCOL, "phase": "development", "readiness": approval,
                "account_selection": is_account_selector(claims),
                "account_id": row.get("assigned_account_id"),
                "model": profile.model, "effort": profile.effort,
                "serial_admission": True,
                "workflow_changes_allowed": workflow_changes_allowed,
                "attempt_id": row["attempt_id"], "owner": owner,
                "selection": {"min_remaining_percent": self.registry.agent_selection.min_remaining_percent,
                              "max_snapshot_age_seconds": self.registry.agent_selection.max_snapshot_age_seconds},
                "repository": route.repository, "ref": route.ref,
                "auto_merge": route.auto_merge,
                "repair": repair,
                "agent_timeout_minutes": route.agent_timeout_minutes,
                "validation_commands": route.validation_commands,
                "issue": {key: issue.get(key) for key in ("id", "identifier", "title", "description", "url")}}
