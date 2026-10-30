"""Worker operations use the controller's grant and existing credential resources."""

from dataclasses import asdict
import json
import re
import time
from uuid import UUID

from .authorization import authorize_job, verify_review_job
from .model import Authorize, Assign, ExhaustAccount, Finish, Progress, Rejected, Review, Role, SessionUpdated, Start
from .workflows import ENGINE, WORKFLOW
from diagnostics import validate_failure
from issue_images import capture_images, image_digest
from readiness_policy import PROTOCOL, REVIEW_TIMEOUT_SECONDS, issue_revision, policy_version
from worker_core import account_provider, execution_profiles, valid_account_id
from worker_reporting import validate_quota_wait, validate_usage


class Broker:
    def __init__(self, controller, registry, accounts, linear, github, *, clock=time.time,
                 refresh=None, steering=None):
        self.controller, self.registry, self.accounts = controller, registry, accounts
        self.linear, self.github, self.clock = linear, github, clock
        self.refresh, self.steering = refresh, steering

    def apply(self, command):
        return self.controller.apply(command, now=self.clock())

    @staticmethod
    def binding(view):
        return {"issue_revision": view.spec.content_revision, "context_sha": view.spec.context_sha,
                "policy_version": view.spec.policy_revision, "images_sha": view.spec.images_sha}

    def metadata(self, view, phase, **fields):
        return self.apply(SessionUpdated(view.task_id, view.attempt["attempt_id"], phase, fields))

    def handle(self, action, claims, data):
        key = data.get("dispatch_key")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise Rejected("Invalid dispatch identity")
        view = self.controller.for_attempt(key)
        if view.attempt["attempt_id"] != key:
            raise Rejected("This dispatch was superseded")
        route = self.registry.routes.get(view.project_id)
        if route is None:
            raise Rejected("Target project is no longer mapped")
        role = authorize_job(claims, view, route, action)
        run_id, run_attempt = str(claims["run_id"]), int(claims["run_attempt"])
        if view.attempt["run_id"] and (view.attempt["run_id"], view.attempt["run_attempt"]) != (run_id, run_attempt):
            raise Rejected("Request belongs to another GitHub attempt")
        phase, selector = role.phase, role.selector
        owner = f"{key}:{run_id}:{run_attempt}:{phase}"

        # Fenced cleanup remains possible after task edits and cancellation.
        if action == "save":
            session = view.session(phase)
            metadata = session["data"] if session else {}
            allowed = {session["account_id"] if session else "", metadata.get("probe_account_id", "")}
            identity = data.get("account_id")
            if not valid_account_id(identity) or identity not in allowed:
                raise Rejected("This phase cannot save another account")
            self.accounts.save(identity, owner, data.get("lease"), data.get("auth"), release=data.get("release") is True)
            if data.get("release") is True:
                self.metadata(view, phase, released_account_id=identity, probe_account_id="")
            return {"saved": True}

        if action == "start":
            if data.get("protocol") != PROTOCOL:
                raise Rejected("Worker and controller protocol do not match")
            self.github.verify_worker_run(ENGINE, claims, key, workflow=WORKFLOW)
        if action in {"start", "accounts", "claim", "bind_account", "checkout", "github", "review"} or (
                action == "progress" and data.get("stage") in {"coding", "publishing"}):
            if self.refresh:
                self.refresh(view)
                view = self.controller.inspect(view.task_id)
            issue = self.linear.issue(view.issue_id)
            if not issue or issue_revision(issue) != view.spec.content_revision:
                raise Rejected("Task content changed since its review specification")
            if self.github.branch_sha(route) != view.spec.context_sha:
                raise Rejected("Target context changed; a new readiness review is required")
            if policy_version(self.registry, route) != view.spec.policy_revision:
                raise Rejected("Task policy does not match its admitted release")
            images = capture_images(issue.get("description"), self.linear.download_image)
            if image_digest(images) != view.spec.images_sha:
                raise Rejected("Task image context changed")
        else:
            issue, images = None, []
        if action == "start":
            passed = verify_review_job(self.github, run_id, run_attempt) if phase == "coding" else False
            view = self.apply(Start(view.task_id, key, run_id, run_attempt, role, passed))
            self.metadata(view, phase, heartbeat_until=self.clock() + 120)
            return self.config(view, route, role, issue, images)

        view = self.apply(Authorize(view.task_id, key, role))
        session = view.session(phase)
        if action == "heartbeat":
            self.metadata(view, phase, heartbeat_until=self.clock() + 120)
            return {"running": True}
        if action in {"accounts", "claim", "bind_account", "exhaust_account"}:
            return self.account(view, role, owner, action, data)
        if action == "checkout":
            return {"token": self.github.installation_token(route.repository, permissions={"contents": "read", "pull_requests": "read"})}
        if action == "github":
            if view.phase not in {"executing", "published"}:
                raise Rejected("Publication is not authorized in this task phase")
            permissions = {"contents": "write", "pull_requests": "write"}
            if self.github.workflow_changes_allowed(route.repository) is True:
                permissions["workflows"] = "write"
            return {"token": self.github.installation_token(route.repository, permissions=permissions)}
        if action == "review":
            if data.get("binding") != self.binding(view):
                raise Rejected("Review result belongs to a different specification")
            result = None if data.get("result_error") else data.get("result")
            updated = self.apply(Review(view.task_id, key, result, view.spec))
            verdict = json.loads(updated.attempt["review_result"])["verdict"]
            return {"status": {"ready": "passed", "retry": "readiness_retry"}.get(verdict, verdict)}
        if action == "progress":
            return self.progress(view, data)
        if action == "finish":
            status = data.get("status")
            if selector and status != "waiting_for_quota":
                raise Rejected("Preflight cannot report an agent result")
            fields = {}
            if status == "failed" and "failure" in data:
                fields["failure"] = validate_failure(data["failure"])
            if status == "waiting_for_quota" and "quota_wait" in data:
                fields["quota_wait"] = validate_quota_wait(data["quota_wait"])
            if fields:
                self.metadata(view, phase, **fields)
            self.apply(Finish(view.task_id, key, phase, status, data.get("pull_request", "")))
            return {"status": status}
        if action in {"steering_open", "steering_close", "instructions", "instruction_result"}:
            return self.instruction(view, action, data)
        raise Rejected("Unsupported worker operation")

    def config(self, view, route, role, issue, images):
        session = view.session(role.phase)
        identity = session["account_id"]
        result = {"protocol": PROTOCOL, "serial_admission": True, "task_controller": True,
                  "phase": "readiness" if role.phase == "review" else "development",
                  "account_selection": role.selector, "account_id": identity or None,
                  **execution_profiles(self.registry, {"assigned_account_id": identity}),
                  "binding": self.binding(view), "readiness": {"review_key": view.attempt["attempt_id"], **self.binding(view)},
                  "repository": route.repository, "ref": route.ref, "images": images,
                  "workflow_changes_allowed": self.github.workflow_changes_allowed(route.repository) is True,
                  "review_timeout_seconds": REVIEW_TIMEOUT_SECONDS,
                  "selection": {"min_remaining_percent": self.registry.agent_selection.min_remaining_percent,
                                "max_snapshot_age_seconds": self.registry.agent_selection.max_snapshot_age_seconds},
                  "agent_timeout_minutes": route.agent_timeout_minutes, "auto_merge": route.auto_merge,
                  "required_checks": route.required_checks, "validation_commands": route.validation_commands,
                  "attempt_id": str(UUID(hex=view.attempt["attempt_id"][:32], version=4)),
                  "owner": f"{view.attempt['run_id']}:{view.attempt['run_attempt']}",
                  "target_dispatch_key": view.task_id,
                  "issue": {k: issue.get(k) for k in ("id", "identifier", "title", "description", "url")}}
        if role.phase == "review":
            result["issue"]["comments"] = sorted(self.linear.issue_comments(view.issue_id), key=lambda c:(c["createdAt"],c["id"]))
        return result

    def account(self, view, role, owner, action, data):
        session = view.session(role.phase)
        assigned, metadata = session["account_id"], session["data"]
        if action == "accounts":
            if assigned:
                return {"accounts": [assigned]}
            if role.selector:
                return {"accounts": self.accounts.list()}
            if role == Role.CODING and metadata.get("account_failover"):
                return {"accounts": [i for i in self.accounts.list() if account_provider(i) == "codex"
                                      and i not in metadata.get("exhausted_account_ids", [])]}
            raise Rejected("Agent job has no assigned account")
        identity = data.get("account_id")
        if not valid_account_id(identity):
            raise Rejected("Invalid provider account identity")
        if action == "exhaust_account":
            if role != Role.CODING:
                raise Rejected("Only the coding agent may exhaust its account")
            account, _ = self.accounts.get(identity)
            if account.get("owner") == owner and account.get("lease_until", 0) > self.clock():
                raise Rejected("Release the exhausted account before failover")
            updated = self.apply(ExhaustAccount(view.task_id, view.attempt["attempt_id"], identity, data.get("thread_id")))
            return {"exhausted_account_ids": updated.session("coding")["data"]["exhausted_account_ids"]}
        replacement = role == Role.CODING and metadata.get("account_failover")
        if replacement and (account_provider(identity) != "codex" or identity in metadata.get("exhausted_account_ids", [])):
            raise Rejected("Cross-provider or exhausted account replacement is forbidden")
        if (assigned or not role.selector and not replacement) and identity != assigned:
            raise Rejected("This phase cannot access another account")
        if action == "bind_account":
            # Binding is only permitted after a probe released its fenced lease.
            account, _ = self.accounts.get(identity)
            if account.get("owner") == owner and account.get("lease_until", 0) > self.clock():
                raise Rejected("Release the quota probe before binding")
            self.apply(Assign(view.task_id, view.attempt["attempt_id"], role, identity))
            return {"account_id": identity}
        if action == "claim":
            if metadata.get("probe_account_id"):
                raise Rejected("Release the previous quota probe before claiming another")
            probing = not assigned
            if probing:
                self.metadata(view, role.phase, probe_account_id=identity)
            try:
                result = self.accounts.claim(identity, owner)
            except Exception:
                # An ambiguous accepted claim keeps its probe fence. Only a
                # positively absent/foreign lease can unlock another identity.
                current, _ = self.accounts.get(identity)
                if probing and (current.get("owner") != owner or current.get("lease_until", 0) <= self.clock()):
                    self.metadata(view, role.phase, probe_account_id="")
                raise
            try:
                self.apply(Authorize(view.task_id, view.attempt["attempt_id"], role))
            except BaseException:
                self.accounts.save(identity, owner, result["lease"], release=True)
                raise
            return result
        raise Rejected("Invalid account operation")

    def progress(self, view, data):
        stage = data.get("stage", view.attempt["stage"])
        fields = {}
        session = view.session("coding")
        if "account_id" in data:
            if data["account_id"] != session["account_id"] or stage != "coding":
                raise Rejected("Coding usage belongs to another assigned account")
            owner = f"{view.attempt['attempt_id']}:{view.attempt['run_id']}:{view.attempt['run_attempt']}:coding"
            account, _ = self.accounts.get(session["account_id"])
            if account.get("owner") != owner or account.get("lease_until", 0) <= self.clock():
                raise Rejected("Selected coding account lease is not active")
            before = validate_usage(data.get("usage_before"))
            segments = list(session["data"].get("usage_segments", []))
            if not segments or segments[-1]["account_id"] != session["account_id"]:
                segments.append({"account_id": session["account_id"], "provider": account_provider(session["account_id"]), "usage_before": before})
                fields["usage_segments"] = segments
            if not session["data"].get("usage_before"):
                fields.update(usage_before=before, original_account_id=session["account_id"])
            fields["account_failover"] = False
            for key in ("account_email", "account_plan"):
                value = data.get(key) or ""
                if not isinstance(value, str) or len(value) > 200:
                    raise Rejected("Invalid account label")
                fields[key] = value
        if "usage_after" in data:
            if data.get("usage_account_id", session["account_id"]) != session["account_id"]:
                raise Rejected("Usage measurement belongs to another account")
            fields["usage_after"] = validate_usage(data["usage_after"])
        if stage == "publishing":
            fields["steering_closed"] = True
        updated = self.apply(Progress(view.task_id, view.attempt["attempt_id"], stage,
                                      data.get("context_sha", view.spec.context_sha if view.attempt["coding_started"] else ""), fields))
        return {"stage": updated.attempt["stage"]}

    def instruction(self, view, action, data):
        session = view.session("coding")
        metadata = session["data"]
        stages = {"coding", "testing"} if action == "steering_close" else {"coding"}
        if view.attempt["stage"] not in stages or metadata.get("steering_closed"):
            raise Rejected("The agent is no longer accepting instructions")
        for field in ("thread_id", "turn_id"):
            value = data.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
                raise Rejected("Invalid agent session identity")
            if metadata.get(field, value) != value and not (action == "steering_open" and field == "turn_id"):
                raise Rejected("Agent session cannot be replaced within an attempt")
        if action == "steering_open":
            self.metadata(view, "coding", thread_id=data["thread_id"], turn_id=data["turn_id"])
            return {"accepting": True}
        if not metadata.get("thread_id"):
            raise Rejected("Agent session has not registered")
        if action == "steering_close":
            self.metadata(view, "coding", steering_closed=True)
            return {"accepting": False}
        if self.steering is None:
            raise Rejected("Instruction transport is unavailable")
        return self.steering.handle(view, action, data)
