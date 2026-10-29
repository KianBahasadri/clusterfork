"""Authorization, credential leases, and publication for trusted Codex jobs."""

from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4

from dispatch_core import eligibility
from worker_reporting import STAGES, validate_usage
from readiness_policy import PROTOCOL

AUDIENCE = "clusterfork-worker"
ISSUER = "https://token.actions.githubusercontent.com"
LEASE_SECONDS = 120


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
    if claims.get("job_workflow_ref") and claims["job_workflow_ref"] != expected["workflow_ref"]:
        raise WorkerError("Reusable workflow identity is not authorized", 403)
    for key in ("run_id", "run_attempt", "repository_id"):
        if not re.fullmatch(r"[1-9][0-9]*", str(claims.get(key, ""))):
            raise WorkerError("Invalid workflow identity", 403)
    if not re.fullmatch(r"[0-9a-f]{40}", claims.get("sha", "")):
        raise WorkerError("Invalid workflow revision", 403)
    return route


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
    def __init__(self, registry, receipts, runs, accounts, linear, github, queue, clock=time.time, sync=None):
        self.registry, self.receipts, self.runs = registry, receipts, runs
        self.accounts, self.linear, self.github, self.clock = accounts, linear, github, clock
        self.sync = sync
        self.queue = queue

    def sync_linear(self, key):
        if self.sync:
            import logging
            try:
                self.sync.sync(key)
            except Exception:
                # Tracker outages must not lose execution/publication outcomes.
                logging.error("Linear lifecycle update deferred for %s", key)

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
            owner = f"{claims['run_id']}:{claims['run_attempt']}"
            parent = hashlib.sha256(
                f"{self.registry.generation}:{route.project_id}:{review['issue_id']}".encode()
            ).hexdigest()
            if action == "start":
                self.github.verify_worker_run(route.repository, claims, key)
                self.queue.start_worker(route.repository, parent, owner)
            elif action != "save":
                self.queue.assert_worker(route.repository, parent, owner)
            result = self.queue.readiness.handle(action, claims, data, review)
            if action == "start":
                result["serial_admission"] = True
            if action == "review" and result.get("status") == "passed":
                issue = self.linear.issue(review["issue_id"])
                current, _ = eligibility(issue or {}, self.registry)
                if current:
                    self.queue.enqueue(issue, current)
                    self.queue.advance(current.repository)
            return result
        route = authorize_claims(claims, self.registry, receipt)
        owner = f"{claims['run_id']}:{claims['run_attempt']}"
        if action == "start":
            return self.start(key, owner, claims, receipt, route)
        self.queue.assert_worker(route.repository, key, owner)
        row, etag = self.runs.get(key)
        if (row.get("owner") != owner or row.get("status") != "running"
                or row.get("lease_until", 0) <= self.clock()):
            raise WorkerError("Worker lease was lost")
        row["lease_until"] = int(self.clock()) + LEASE_SECONDS
        # The runner serializes broker requests; the ETag fences duplicate jobs.
        self.runs.replace(row, etag)
        if action in {"accounts", "claim", "github", "checkout"} or (
                action == "progress" and data.get("stage") in {"coding", "publishing"}):
            issue = self.linear.issue(receipt["issue_id"])
            extra = (route.linear_states.in_progress, route.linear_states.blocked) if route.linear_states else ()
            current, _ = eligibility(issue or {}, self.registry, additional_state_ids=extra)
            if current != route:
                raise WorkerError("Issue is no longer eligible", 403)
            approval = self.queue.readiness.require(issue, route)
            if approval.get("review_key", "") != row.get("readiness_review_key", ""):
                raise WorkerError("Worker readiness approval was superseded", 403)
            if (action == "progress" and data.get("stage") == "coding" and approval.get("context_sha")
                    and data.get("context_sha") != approval["context_sha"]):
                raise WorkerError("Coding checkout does not match the reviewed repository context", 403)
        if action == "heartbeat":
            return {"running": True}
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
                if stage != "coding" or row.get("account_id", identity) != identity:
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
            self.runs.replace(row, etag)
            self.sync_linear(key)
            return {"stage": stage}
        if action == "accounts":
            return {"accounts": self.accounts.list()}
        if action in {"claim", "save"}:
            identity = data.get("account_id", "")
            if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
                raise WorkerError("Invalid account identity", 400)
            if action == "claim":
                return self.accounts.claim(identity, owner)
            self.accounts.save(identity, owner, data.get("lease"), data.get("auth"),
                               release=data.get("release") is True)
            return {"saved": True}
        if action == "checkout":
            return {"token": self.github.installation_token(
                route.repository, permissions={"contents": "read", "pull_requests": "read"})}
        if action == "github":
            issue = self.linear.issue(receipt["issue_id"])
            extra = (route.linear_states.in_progress,) if route.linear_states else ()
            current_route, _ = eligibility(issue or {}, self.registry, additional_state_ids=extra)
            if current_route != route:
                raise WorkerError("Issue is no longer eligible for publication", 403)
            return {"token": self.github.installation_token(
                route.repository, permissions={"contents": "write", "pull_requests": "write"})}
        if action == "finish":
            status = data.get("status")
            if status not in {"completed", "failed", "waiting_for_quota"}:
                raise WorkerError("Invalid worker outcome", 400)
            row, etag = self.runs.get(key)
            row.update(status=status, lease_until=0, finished_at=int(self.clock()),
                       report_version=row.get("report_version", 0) + 1)
            url = data.get("pull_request", "")
            if url and not re.fullmatch(re.escape(f"https://github.com/{route.repository}/pull/") + r"[1-9][0-9]*", url):
                raise WorkerError("Invalid pull request URL", 400)
            row["pull_request"] = url
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
        try:
            approval = self.queue.readiness.require(issue, route)
        except WorkerError:
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
        row = {"RowKey": key, "owner": owner, "status": "running", "stage": "initializing",
               "readiness_review_key": approval.get("review_key", ""),
               "started_at": int(self.clock()), "report_version": 0,
               "lease_until": int(self.clock()) + LEASE_SECONDS}
        if not self.runs.create(row):
            existing, etag = self.runs.get(key)
            if existing.get("status") == "completed":
                raise WorkerError("Issue already completed")
            # Repository admission checked the previous GitHub attempt, even
            # when its heartbeat expired or its finish callback already ran.
            if existing.get("owner") == owner and existing.get("status") == "running":
                row = {**existing, "lease_until": row["lease_until"]}
            self.runs.replace(row, etag)
        self.sync_linear(key)
        profile = self.registry.agent_profiles["codex"]
        return {"protocol": PROTOCOL, "phase": "development", "readiness": approval,
                "model": profile.model, "effort": profile.effort,
                "serial_admission": True,
                "selection": {"min_remaining_percent": self.registry.agent_selection.min_remaining_percent,
                              "max_snapshot_age_seconds": self.registry.agent_selection.max_snapshot_age_seconds},
                "repository": route.repository, "ref": route.ref,
                "auto_merge": route.auto_merge,
                "agent_timeout_minutes": route.agent_timeout_minutes,
                "validation_commands": route.validation_commands,
                "issue": {key: issue.get(key) for key in ("id", "identifier", "title", "description", "url")}}
