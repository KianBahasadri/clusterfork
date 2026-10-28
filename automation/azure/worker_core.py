"""Authorization, credential leases, and publication for trusted Codex jobs."""

from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4

from dispatch_core import eligibility

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
    def __init__(self, registry, receipts, runs, accounts, linear, github, clock=time.time, sync=None):
        self.registry, self.receipts, self.runs = registry, receipts, runs
        self.accounts, self.linear, self.github, self.clock = accounts, linear, github, clock
        self.sync = sync

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
        receipt, _ = self.receipts.get(key)
        route = authorize_claims(claims, self.registry, receipt)
        owner = f"{claims['run_id']}:{claims['run_attempt']}"
        if action == "start":
            return self.start(key, owner, claims, receipt, route)
        row, etag = self.runs.get(key)
        if (row.get("owner") != owner or row.get("status") != "running"
                or row.get("lease_until", 0) <= self.clock()):
            raise WorkerError("Worker lease was lost")
        row["lease_until"] = int(self.clock()) + LEASE_SECONDS
        # The runner serializes broker requests; the ETag fences duplicate jobs.
        self.runs.replace(row, etag)
        if action == "heartbeat":
            return {"running": True}
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
            row.update(status=status, lease_until=0)
            url = data.get("pull_request", "")
            if url and not re.fullmatch(re.escape(f"https://github.com/{route.repository}/pull/") + r"[1-9][0-9]*", url):
                raise WorkerError("Invalid pull request URL", 400)
            row["pull_request"] = url
            row["auto_merge_requested"] = route.auto_merge and status == "completed" and bool(url)
            self.runs.replace(row, etag)
            if status == "waiting_for_quota":
                receipt, receipt_etag = self.receipts.get(key)
                receipt["retry_after"] = int(self.clock()) + 900
                self.receipts.replace(receipt, receipt_etag)
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
        row = {"RowKey": key, "owner": owner, "status": "running",
               "lease_until": int(self.clock()) + LEASE_SECONDS}
        if not self.runs.create(row):
            existing, etag = self.runs.get(key)
            if existing.get("status") == "completed":
                raise WorkerError("Issue already completed")
            if (existing.get("owner") != owner and existing.get("status") == "running"
                    and existing.get("lease_until", 0) > self.clock()):
                raise WorkerError("Another worker is active")
            self.runs.replace(row, etag)
        self.sync_linear(key)
        profile = self.registry.agent_profiles["codex"]
        return {"model": profile.model, "effort": profile.effort,
                "selection": {"min_remaining_percent": self.registry.agent_selection.min_remaining_percent,
                              "max_snapshot_age_seconds": self.registry.agent_selection.max_snapshot_age_seconds},
                "repository": route.repository, "ref": route.ref,
                "auto_merge": route.auto_merge,
                "validation_commands": route.validation_commands,
                "issue": {key: issue.get(key) for key in ("id", "identifier", "title", "description", "url")}}
