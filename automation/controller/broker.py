"""Worker operations use the controller's grant and existing credential resources."""

import json
import re
import time
from uuid import UUID
from types import SimpleNamespace

from .authorization import SELECTOR_ACTIONS,authorize_job, verify_review_job
from .images import Images
from .context import Context
from .model import (Authorize, AccountRequest, EvidenceChanged, Finish, Progress, Rejected, Review, Role,
                    SessionUpdated, Start, dependency_admission_applies)
from .workflows import ENGINE, WORKFLOW
from .telemetry import correlate
from common.diagnostics import validate_failure
from common.clients import RequestError
from common.images import image_digest
from common.policy import PROTOCOL, REVIEW_TIMEOUT_SECONDS, issue_revision, policy_version
from common.accounts import account_provider, execution_profiles
from common.reporting import validate_quota_wait, validate_usage, quota_wait_text
from common.reporting import worker_branch
from common.repair import repair_instructions
from common.context import CONTEXT_PROTOCOL, ContextError


class Broker:
    def __init__(self, controller, registry, linear, github, *, clock=time.time,
                 refresh=None, steering=None):
        self.controller, self.registry, self.accounts = controller, registry, controller.accounts
        self.linear, self.github, self.clock = linear, github, clock
        self.refresh, self.steering = refresh, steering
        self.images=Images(controller.database,linear.download_image,clock=clock)
        self.context = Context(controller.database, linear, github)

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
        identity_route=(SimpleNamespace(project_id=view.project_id,repository=view.repository) if action=='save' else route)
        if identity_route is None:
            raise Rejected("Target project is no longer mapped")
        principal = authorize_job(claims, view, identity_route, action)
        current_session=view.session(principal.phase)
        selecting=not current_session or (not current_session['agent_started'] and (
            not current_session['account_id'] or action=='heartbeat'))
        role=(Role.REVIEW_SELECT if principal==Role.REVIEW else Role.CODING_SELECT) if selecting else principal
        if role.selector and action not in SELECTOR_ACTIONS:
            raise Rejected('Initial account selection must finish before execution access')
        run_id, run_attempt = str(claims["run_id"]), int(claims["run_attempt"])
        if view.attempt["run_id"] and (view.attempt["run_id"], view.attempt["run_attempt"]) != (run_id, run_attempt):
            raise Rejected("Request belongs to another GitHub attempt")
        phase, selector = role.phase, role.selector
        try:
            correlate(phase)
        except Exception:
            pass  # Optional correlation cannot reject an authorized worker operation.

        # Fenced cleanup remains possible after task edits and cancellation.
        if action == "save":
            return self.apply(AccountRequest(view.task_id,key,role,action,data))

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
            images = self.images.snapshot(issue,revalidate=action in {'start','review','github'} or (
                action=='progress' and data.get('stage')=='publishing'))
            if image_digest(images) != view.spec.images_sha:
                raise Rejected("Task image context changed")
        else:
            issue, images = None, []
        if phase == "coding" and (action in {"start", "checkout", "github"} or
                action == "progress" and data.get("stage") in {"coding", "publishing"}):
            self.validate_context(view, role)
        if action == "start":
            passed = verify_review_job(self.github, run_id, run_attempt) if phase == "coding" else False
            view = self.apply(Start(view.task_id, key, run_id, run_attempt, role, passed))
            fields = {"heartbeat_until": self.clock()+120}
            if phase=="coding" and not role.selector and not view.session(phase)["data"].get("steering_started_at"):
                fields["steering_started_at"] = self.clock()
                profile=execution_profiles(self.registry,{'assigned_account_id':view.session(phase)['account_id']})
                fields.update({key:profile[key] for key in ('provider','model','effort')})
            self.metadata(view, phase, **fields)
            return self.config(view, route, role, issue, images)

        if action != 'finish':
            view = self.apply(Authorize(view.task_id, key, role))
        if action == "heartbeat":
            return self.apply(AccountRequest(view.task_id,key,role,action,data))
        if action in {"accounts", "claim", "bind_account", "exhaust_account"}:
            return self.apply(AccountRequest(view.task_id,key,role,action,data))
        if action == "context":
            try:
                return self.context.read(view, data.get("request"), self.context_repositories(),
                                         lambda: self.context_authority(view, role, require_lease=True))
            except ContextError as exc:
                return {"status": exc.status, "complete": False}
        if action == "checkout":
            permissions={"contents":"read"}
            if phase=="coding":
                permissions["pull_requests"]="read"
                if view.attempt["attempt_kind"]=="repair":
                    permissions["actions"]="read"
            return {"token": self.github.installation_token(route.repository, permissions=permissions)}
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
            with self.context.lock_for(view):
                evidence = self.validate_context(view, role) if isinstance(result, dict) and result.get("verdict") == "ready" else ""
                updated = self.apply(Review(view.task_id, key, result, view.spec, evidence))
            verdict = json.loads(updated.attempt["review_result"])["verdict"]
            return {"status": 'passed' if verdict == 'ready' else verdict}
        if action == "progress":
            return self.progress(view, data)
        if action == "finish":
            status = data.get("status")
            if selector and status not in {"waiting_for_quota", "failed"}:
                raise Rejected("Preflight cannot report an agent result")
            fields = {}
            if status == "failed" and "failure" in data:
                fields["failure"] = {**validate_failure(data["failure"]),'dispatch_key':key,
                                     'run_id':run_id,'run_attempt':str(run_attempt)}
            if status == "waiting_for_quota" and "quota_wait" in data:
                fields["quota_wait"] = validate_quota_wait(data["quota_wait"])
            if "wip_branch" in data or "wip_commit_sha" in data:
                branch=worker_branch(view.identifier,view.task_id)
                if (status!="failed" or phase!="coding" or data.get("wip_branch") not in {branch,branch.replace("clusterfork/","clusterfork-wip/",1)}
                        or not isinstance(data.get("wip_commit_sha"),str) or not re.fullmatch(r"[0-9a-f]{40}",data["wip_commit_sha"])):
                    raise Rejected("Invalid preserved worker change")
                fields.update(wip_branch=data["wip_branch"],wip_commit_sha=data["wip_commit_sha"])
            if fields:
                self.metadata(view, phase, **fields)
            reason="Waiting for account capacity. "+quota_wait_text(fields.get("quota_wait",{})) if status=="waiting_for_quota" else ""
            self.apply(Finish(view.task_id, key, phase, status, data.get("pull_request", ""), reason, fields.get('failure', {})))
            return {"status": status}
        if action in {"steering_open", "steering_close", "instructions", "instruction_result"}:
            return self.instruction(view, action, data)
        raise Rejected("Unsupported worker operation")

    def config(self, view, route, role, issue, images):
        session = view.session(role.phase)
        identity = session["account_id"]
        result = {"protocol": PROTOCOL, "serial_admission": True, "task_controller": True,
                  "runtime_sha":view.spec.runtime_sha,
                  "phase": "readiness" if role.phase == "review" else "development",
                  "account_selection": role.selector, "account_id": identity or None,
                  **execution_profiles(self.registry, {"assigned_account_id": identity}),
                  "binding": self.binding(view), "readiness": {"review_key": view.attempt["attempt_id"], **self.binding(view)},
                  "repository": route.repository, "ref": route.ref, "images": images,
                  "context_capability": CONTEXT_PROTOCOL,
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
        elif view.attempt["attempt_kind"]=="repair":
            previous=self.controller.repair_parent(view.task_id)
            if previous is None:
                raise Rejected("Repair parent evidence is unavailable")
            result["repair"]={"target_branch":worker_branch(view.identifier,view.task_id),
                              "target_dispatch_key":view.task_id,"target_identifier":view.identifier,
                              "target_pull_request":previous["pull_request"],"instructions":repair_instructions(route.ref),
                              "failure_output":previous["data"].get("failure",{}).get("failure_output","")}
            result["repair"].update({k:previous["data"][k] for k in ("wip_branch","wip_commit_sha") if k in previous["data"]})
        return result

    def context_repositories(self):
        return {route.repository.lower() for route in self.registry.routes.values()}

    def context_authority(self, view, role, *, require_lease=False):
        current = self.controller.inspect(view.task_id)
        session = current.session(role.phase)
        route = self.registry.routes.get(current.project_id)
        if (not current.active or current.canceled or not current.eligible
                or (not current.dependencies_ready and dependency_admission_applies(current.attempt))
                or route is None or route.repository.lower() != current.repository
                or current.phase in {"held", "completed", "canceled", "skipped"}
                or current.attempt["attempt_id"] != view.attempt["attempt_id"] or current.attempt["stopped"]
                or session and (session["finished"] or session["deadline"] <= self.clock())):
            raise Rejected("Related context access is no longer authorized")
        # Recheck mapped source scopes after network activity as well as before
        # it, including a configuration revocation while a fetch was in flight.
        from common.context import pull_ref
        with self.controller.database.transaction() as db:
            requests = [json.loads(row[0]) for row in db.execute(
                "SELECT request FROM context_sources WHERE attempt_id=? AND status='ok'", (view.attempt["attempt_id"],))]
        repositories = self.context_repositories()
        if any(request["operation"] == "pull" and pull_ref(request["target"])[0] not in repositories for request in requests):
            raise Rejected("Related repository context authorization was revoked")
        if require_lease or (role.phase == "coding" and not dependency_admission_applies(current.attempt)):
            if (not session or not session["account_id"]
                    or (require_lease and session["data"].get("account_reserved"))):
                raise Rejected("Related context requires an active execution account lease")
            # Fenced release still permits trusted WIP preservation after the
            # agent stops. Context-tool use always needs the live execution lease.
            if require_lease or session["data"].get("released_account_id") != session["account_id"]:
                account, _ = self.accounts.get(session["account_id"])
                owner = f"{view.attempt['attempt_id']}:{view.attempt['run_id']}:{view.attempt['run_attempt']}:{role.phase}"
                if account.get("owner") != owner or account.get("lease_until", 0) <= self.clock():
                    raise Rejected("Related context requires an active execution account lease")

    def validate_context(self, view, role):
        try:
            return self.context.revalidate(view, self.context_repositories(),
                                           lambda: self.context_authority(view, role), approved=role.phase == "coding")
        except (ValueError, RequestError, KeyError, TypeError, AttributeError):
            self.apply(EvidenceChanged(view.task_id, view.attempt["attempt_id"]))
            raise Rejected("Related context requires a fresh readiness assessment") from None


    def progress(self, view, data):
        stage = data.get("stage", view.attempt["stage"])
        fields = {}
        session = view.session("coding")
        if "account_id" in data:
            if session['data'].get('account_reserved'):
                raise Rejected('Selected coding account lease is not active for execution until claimed')
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
            if not session["data"].get("usage_before"):
                fields.update(usage_before=before, original_account_id=session["account_id"])
            fields["account_failover"] = False
            for key in ("account_email", "account_plan"):
                value = data.get(key) or ""
                if not isinstance(value, str) or len(value) > 200:
                    raise Rejected("Invalid account label")
                fields[key] = value
                segments[-1].setdefault(key,value)
            fields['usage_segments']=segments
        if "usage_after" in data:
            if data.get("usage_account_id", session["account_id"]) != session["account_id"]:
                raise Rejected("Usage measurement belongs to another account")
            fields["usage_after"] = validate_usage(data["usage_after"])
            segments=list(fields.get('usage_segments',session['data'].get('usage_segments',[])))
            if not segments or segments[-1]['account_id']!=session['account_id']:
                raise Rejected('Account selection must be recorded before a final usage reading')
            segments[-1]={**segments[-1],'usage_after':fields['usage_after']}
            fields['usage_segments']=segments
        if stage == "publishing":
            fields["steering_closed"] = True
            if data.get("repair_validation_passed") is True:
                fields["repair_validation_passed"]=True
            if "repair_result" in data:
                result=data["repair_result"]
                if (not isinstance(result,dict) or set(result)!={"commit_sha","changed_files"}
                        or not isinstance(result.get("commit_sha"),str) or not re.fullmatch(r"[0-9a-f]{40}",result["commit_sha"])
                        or type(result.get("changed_files")) is not int or not 0<=result["changed_files"]<=100000):
                    raise Rejected("Invalid repair validation result")
                fields["repair_result"]=result
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
