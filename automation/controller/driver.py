"""External adapters observe and execute; the controller alone changes task policy."""

from dataclasses import replace
import hashlib
import json
import logging
import time

from .authorization import release_ref
from .model import (Admit, ContextObserved, DispatchObserved, EffectUpdated, ExternalObserved,
                    Hold, InboxDone, IssueObserved, Rejected, Retry, TaskSpec)
from .workflows import ENGINE, WORKFLOW
from dispatch_core import DispatchError, dependency_status, eligibility
from issue_images import capture_images, image_digest
from linear_sync import comment_id, failed_required_checks, pull_outcome
from readiness_policy import issue_revision, policy_version, redact_review_text
from worker_reporting import worker_branch


class Driver:
    def __init__(self, controller, registry, linear, github, runtime_sha, *, runtime_ready, clock=time.time, steering=None):
        self.controller, self.registry = controller, registry
        self.linear, self.github, self.runtime_sha = linear, github, runtime_sha
        self.runtime_ready, self.clock = runtime_ready, clock
        self.steering = steering
        self._reported = {}

    def apply(self, command):
        return self.controller.apply(command, now=self.clock())

    def route(self, view):
        route = self.registry.routes.get(view.project_id)
        if not route or route.repository.lower() != view.repository:
            raise Rejected("Task project mapping changed")
        return route

    def runtime_route(self, route, revision):
        return replace(route, repository=ENGINE, workflow=WORKFLOW,
                       ref=release_ref(revision).removeprefix("refs/tags/"), worker_revision="")

    def observe_issue(self, issue, *, known=None):
        if not issue:
            if known:
                return self.apply(IssueObserved(known.issue_id, known.project_id, known.repository,
                                               self.registry.generation, known.identifier, False, False,
                                               content_revision=known.spec.content_revision if known.spec else "",
                                               reason="Issue is unavailable or removed.", canceled=known.canceled))
            return None
        mapped = self.registry.routes.get((issue.get("project") or {}).get("id"))
        if not mapped:
            if known:
                return self.observe_issue(None, known=known)
            return None
        extra = (mapped.linear_states.in_progress, mapped.linear_states.blocked) if known and known.active and mapped.linear_states else ()
        route, reason = eligibility(issue, self.registry, additional_state_ids=extra)
        try:
            _, dependency_reason = dependency_status(self.linear.issue_dependencies(issue["id"]))
        except Exception:
            dependency_reason = "Issue prerequisites could not be verified."
        return self.apply(IssueObserved(issue["id"], mapped.project_id, mapped.repository,
                                       self.registry.generation, issue["identifier"], route == mapped,
                                       not dependency_reason, issue.get("priority", 0) or 0,
                                       float(issue.get("sortOrder", 0) or 0), issue_revision(issue),
                                       dependency_reason or ("" if route else reason), issue.get("state",{}).get("type")=="canceled"))

    def refresh(self, view):
        issue = self.linear.issue(view.issue_id)
        updated = self.observe_issue(issue, known=view)
        if updated and updated.active and updated.eligible and updated.spec:
            spec = self.spec(issue, self.route(updated), runtime_sha=updated.spec.runtime_sha)
            self.apply(ContextObserved(updated.task_id, updated.attempt["attempt_id"], spec))
        return issue

    def spec(self, issue, route, *, runtime_sha=None):
        images = capture_images(issue.get("description"), self.linear.download_image)
        return TaskSpec(issue_revision(issue), self.github.branch_sha(route), runtime_sha or self.runtime_sha,
                        policy_version(self.registry, route), image_digest(images))

    def scan(self):
        for project in self.registry.routes:
            issues = sorted(self.linear.project_issues(project), key=lambda i:(i.get("createdAt", ""),i["id"]))
            known = {v.issue_id: v for v in self.controller.tasks() if v.project_id == project}
            for issue in issues:
                self.observe_issue(issue, known=known.get(issue["id"]))

    def events(self):
        scanned = False
        for event_id, event in self.controller.inbox()[:100]:
            try:
                if event["type"] == "Project":
                    if event["id"] in self.registry.routes and not scanned:
                        self.scan()
                        scanned = True
                elif event["type"] == "Issue":
                    known = next((v for v in self.controller.tasks() if v.issue_id==event["id"]), None)
                    self.observe_issue(self.linear.issue(event["id"]),known=known)
                elif event["type"] == "Comment":
                    if self.steering is None:
                        raise Rejected("Instruction transport is unavailable")
                    self.steering.comment(event["id"])
                elif event["type"] != "IssueRelation":
                    raise Rejected("Unsupported queued event")
                self.apply(InboxDone(event_id))
            except Exception as exc:
                logging.warning("Controller event deferred (%s)",type(exc).__name__)
        return scanned

    def tick(self, *, scan=False):
        try:
            runtime_ready = self.runtime_ready()
        except Exception:
            runtime_ready = False
        self.apply(Hold("deployment:required-runtime", "Waiting for the latest required runtime to be verified.", release=runtime_ready))
        scanned = self.events()
        if scan and not scanned:
            self.scan()
        for view in self.controller.tasks(unsettled=True):
            try:
                self.refresh(view)
                current = self.controller.inspect(view.task_id)
                if current.active:
                    self.observe_execution(current)
                    current = self.controller.inspect(current.task_id)
                    if (runtime_ready and current.active and current.attempt.get("retry_kind")
                            and current.attempt["retry_at"] <= self.clock()):
                        issue = self.linear.issue(current.issue_id)
                        self.apply(Retry(current.task_id, self.spec(issue, self.route(current)),
                                         current.reason, current.attempt["retry_kind"]))
            except Rejected:
                pass  # A newer command or an explicit hold owns the next decision.
            except Exception as exc:
                logging.warning("Controller observation deferred for %s (%s)", view.task_id[:12], type(exc).__name__)
        for view in self.controller.waiting():
            try:
                if self.controller.holds():
                    break
                if not runtime_ready or not self.runtime_ready():
                    continue  # Admission checks required runtime even if a merge event was missed.
                route = self.route(view)
                issue = self.refresh(view)
                self.apply(Admit(view.task_id, self.spec(issue, route)))
            except Rejected:
                continue
        self.dispatch()
        self.report()
        if self.steering is not None:
            self.steering.reconcile()

    def dispatch(self):
        for effect in self.controller.effects("dispatch"):
            if effect["state"] != "pending":
                continue  # A claimed effect is discovered; never sent again.
            view = self.controller.inspect(effect["task_id"])
            self.apply(EffectUpdated(view.task_id, effect["effect_id"], "claimed"))
            try:
                route = self.runtime_route(self.route(view), view.spec.runtime_sha)
                self.github.dispatch(route, {"linear_issue_id":view.issue_id,"linear_identifier":view.identifier,
                                             "linear_project_id":view.project_id,"dispatch_key":view.attempt["attempt_id"]})
            except DispatchError as exc:
                status = "unknown" if exc.uncertain else "rejected"
            except Exception:
                status = "unknown"
            else:
                status = "accepted"
            self.apply(DispatchObserved(view.task_id, view.attempt["attempt_id"], status))
            self.apply(EffectUpdated(view.task_id, effect["effect_id"], "done"))

    def observe_execution(self, view):
        target = self.route(view)
        engine = self.runtime_route(target, view.spec.runtime_sha)
        key = view.attempt["attempt_id"]
        runs = self.github.dispatch_runs(engine, key)
        owner = f"{view.attempt['run_id']}:{view.attempt['run_attempt']}" if view.attempt["run_id"] else ""
        recorded = self.github.worker_result(ENGINE, owner) if owner else None
        if owner:
            latest = self.github.worker_result(ENGINE, owner, latest=True)
            # Listings can lag, and a manual rerun may be a different attempt.
            all_stopped = latest["status"] == "completed" and all(r["status"] == "completed" for r in runs)
        else:
            if not runs:
                return  # No run observation does not prove dispatch rejection.
            recorded = min(runs, key=lambda r:(r["id"],r["run_attempt"]))
            all_stopped = all(r["status"] == "completed" for r in runs)
        receipt = {"identifier":view.identifier,"target_dispatch_key":view.task_id,
                   "target_branch":worker_branch(view.identifier,view.task_id)}
        pull = self.github.worker_pull(target, receipt, view.task_id, view.attempt["pull_request"])
        pull_state = "absent" if pull is None else "merged" if pull.get("merged") else pull["state"].lower()
        reason, repairable = "", False
        if pull and pull_state != "merged":
            _, reason = pull_outcome(pull, target.auto_merge)
            repairable = bool(failed_required_checks(pull) or pull.get("mergeable") == "CONFLICTING"
                              or pull.get("mergeStateStatus") in {"DIRTY", "BEHIND"})
        elif pull is None and view.attempt["coding_started"] and recorded.get("conclusion") in {"failure", "timed_out"}:
            repairable = True
        self.apply(ExternalObserved(view.task_id,key,str(recorded["id"]),recorded["run_attempt"],
                                    recorded["status"]=="completed",recorded.get("conclusion") or "",all_stopped,
                                    True,(pull or {}).get("url", ""),pull_state,reason,repairable))

    def render(self, view):
        attempt = view.attempt
        body = f"Clusterfork task: {view.phase.value}.\n\n" + redact_review_text(view.reason)
        if attempt.get("run_id"):
            body += f"\n\n[Worker run](https://github.com/{ENGINE}/actions/runs/{attempt['run_id']}/attempts/{attempt['run_attempt']})"
        if attempt.get("pull_request"):
            body += f"\n\n[Pull request]({attempt['pull_request']})"
        review = json.loads(attempt.get("review_result") or "null")
        if review and review.get("verdict") in {"needs_clarification", "rejected"}:
            body += "\n\n" + review["summary"]
            for blocker in review.get("blockers", []):
                body += f"\n\n{blocker['decision']}\nConsequence: {blocker['consequence']}\nQuestion: {blocker['question']}"
            body += "\n\nAddress the feedback, then move the issue back to Todo for a new review."
            if self.registry.notify_user_url:
                body += "\n\n" + self.registry.notify_user_url
        return body

    def report(self):
        pending = {}
        for effect in self.controller.effects("report"):
            pending.setdefault(effect["task_id"], []).append(effect)
        for task_id, effects in pending.items():
            try:
                view = self.controller.inspect(task_id)
                route = self.route(view)
                body = self.render(view)
                signature = hashlib.sha256(json.dumps([body,view.active,view.phase.value,
                                                       view.attempt.get("stage")]).encode()).hexdigest()
                if self._reported.get(task_id) != signature:
                    issue = self.linear.issue(view.issue_id)
                    if issue and (issue.get("project") or {}).get("id") == view.project_id:
                        self.linear.status_comment(view.issue_id,comment_id(task_id,"controller-status"),body)
                        review = json.loads(view.attempt.get("review_result") or "null")
                        denied = review and review.get("verdict") in {"rejected","needs_clarification"}
                        self.linear.set_selected(view.issue_id,view.active and not denied)
                        self.linear.set_queued(view.issue_id,view.phase=="queued" and view.eligible)
                        self.linear.set_clarification(view.issue_id,bool(review and review.get("verdict")=="needs_clarification"))
                        stage = view.attempt.get("stage") if view.phase=="executing" else "review" if view.phase=="published" else None
                        self.linear.set_stage(view.issue_id,stage)
                        if route.linear_states and issue.get("state",{}).get("id") in {*route.ready_state_ids,route.linear_states.in_progress,route.linear_states.blocked}:
                            desired = (route.linear_states.done if view.phase=="completed" else
                                       route.linear_states.blocked if view.phase=="held" and not view.attempt.get("retry_kind") else
                                       route.linear_states.in_progress if view.phase in {"executing","published"} or view.attempt.get("attempt_kind")=="repair" else
                                       route.ready_state_ids[0])
                            if issue["state"]["id"] != desired and view.phase != "canceled":
                                self.linear.set_state(view.issue_id,desired)
                    self._reported[task_id] = signature
                for effect in effects:
                    self.apply(EffectUpdated(task_id,effect["effect_id"],"done"))
            except Exception as exc:
                logging.warning("Controller reporting deferred for %s (%s)",task_id[:12],type(exc).__name__)
