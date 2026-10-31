"""External adapters observe and execute; the controller alone changes task policy."""

import hashlib
import json
import logging
import time
from types import SimpleNamespace

from .authorization import release_ref
from .model import (Admit, ContextObserved, DispatchObserved, EffectUpdated, ExternalObserved,
                    Hold, InboxDone, IssueObserved, Rejected, Retry, TaskSpec, identity, ReportConfirmed)
from .images import Images
from .workflows import ENGINE, WORKFLOW
from .reporting import attempt_reports,repair_terminal,repair_report,review_report
from common.routing import DispatchError, dependency_status, eligibility
from common.images import image_digest
from common.publication import comment_id, failed_required_checks, pull_outcome
from common.policy import issue_revision, policy_version, redact_review_text
from common.reporting import worker_branch
from common.reporting import AUTO_REPAIR_LABEL
from common.diagnostics import failure_summary


class Driver:
    def __init__(self, controller, registry, linear, github, runtime_sha, *, runtime_ready, runtime_source_ready=None,
                 clock=time.time, steering=None):
        self.controller, self.registry = controller, registry
        self.linear, self.github, self.runtime_sha = linear, github, runtime_sha
        self.runtime_ready, self.clock = runtime_ready, clock
        self.runtime_source_ready=runtime_source_ready or runtime_ready
        self.steering = steering
        self.images=Images(controller.database,linear.download_image,clock=clock)

    def apply(self, command):
        return self.controller.apply(command, now=self.clock())

    def route(self, view):
        route = self.registry.routes.get(view.project_id)
        if not route or route.repository.lower() != view.repository:
            raise Rejected("Task project mapping changed")
        return route

    def runtime_route(self, route, revision):
        return SimpleNamespace(repository=ENGINE,workflow=WORKFLOW,
                               ref=release_ref(revision).removeprefix('refs/tags/'),worker_revision='')

    def original_route(self,view):
        current=self.registry.routes.get(view.project_id)
        compatible=current and current.repository.lower()==view.repository
        return SimpleNamespace(project_id=view.project_id,
                               repository=view.spec.target_repository or (current.repository if compatible else view.repository),
                               ref=view.spec.target_ref if view.spec.target_repository or not compatible else current.ref,
                               auto_merge=view.spec.auto_merge if view.spec.target_repository or not compatible else current.auto_merge)

    def observe_issue(self, issue, *, known=None):
        if (known and known.phase in {'completed','canceled'}
                and known.task_id==identity(self.registry.generation,known.project_id,known.issue_id)):
            return known
        if not issue:
            if known:
                return self.apply(IssueObserved(known.issue_id, known.project_id, known.repository,
                                               self.registry.generation, known.identifier, False, False,
                                               content_revision=known.spec.content_revision if known.spec else "",
                                               reason="Issue is unavailable or removed.", canceled=known.canceled))
            return None
        mapped = self.registry.routes.get((issue.get("project") or {}).get("id"))
        if known and (not mapped or mapped.project_id!=known.project_id or mapped.repository.lower()!=known.repository):
            return self.apply(IssueObserved(known.issue_id,known.project_id,known.repository,self.registry.generation,
                                           known.identifier,False,False,content_revision=issue_revision(issue),
                                           reason='Issue or repository mapping changed; original execution remains fenced.',
                                           canceled=known.canceled or issue.get('state',{}).get('type')=='canceled'))
        if not mapped:
            if known:
                return self.observe_issue(None, known=known)
            return None
        previous=json.loads(known.attempt.get('review_result') or 'null') if known else None
        denied=previous and previous.get('verdict') in {'rejected','needs_clarification'}
        extra = (mapped.linear_states.in_progress, mapped.linear_states.blocked) if known and known.active and not denied and mapped.linear_states else ()
        route, reason = eligibility(issue, self.registry, additional_state_ids=extra)
        if route != mapped and (known is None or known.task_id!=identity(self.registry.generation,mapped.project_id,issue['id'])):
            return known
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
        images = self.images.snapshot(issue)
        return TaskSpec(issue_revision(issue), self.github.branch_sha(route), runtime_sha or self.runtime_sha,
                        policy_version(self.registry, route), image_digest(images),route.repository,route.ref,route.auto_merge)

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
        self.images.prune({revision for view in self.controller.tasks(unsettled=True)
                           for revision in (view.content_revision,view.spec.content_revision if view.spec else '') if revision})

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
        target = self.original_route(view)
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
                if view.attempt['dispatch_status']!='rejected':
                    return  # Missing runs do not resolve an uncertain dispatch.
                recorded={'id':'','run_attempt':0,'status':'completed','conclusion':'failure'}
            else:
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
        reason=view.reason
        if view.phase=='queued' and not reason:
            ahead=next((v for v in self.controller.active() if v.repository==view.repository),None)
            reason=f'Waiting for {ahead.identifier} to complete.' if ahead else 'Waiting for repository admission.'
        body = f"Clusterfork task: {view.phase.value}.\n\n" + redact_review_text(reason)
        session=view.session('coding')
        diagnostic=(session['data'].get('failure') if session else None)
        if diagnostic and view.phase in {'held','published'}:
            body+='\n\n'+failure_summary(diagnostic)
        if view.phase=='queued' or view.phase=='held' and attempt.get('retry_kind'):
            for owner,reason in sorted(self.controller.holds().items()):
                body+='\n\n'+redact_review_text(reason or f'Admission paused by {owner}.')
        if attempt.get("run_id"):
            body += f"\n\n[Worker run](https://github.com/{ENGINE}/actions/runs/{attempt['run_id']}/attempts/{attempt['run_attempt']})"
        if attempt.get("pull_request"):
            body += f"\n\n[Pull request]({attempt['pull_request']})"
        return body

    def report(self):
        pending = {}
        for effect in self.controller.effects("report"):
            pending.setdefault(effect["task_id"], []).append(effect)
        for task_id, effects in pending.items():
            try:
                view = self.controller.inspect(task_id)
                route = self.route(view)
                issue=self.linear.issue(view.issue_id)
                project=(issue or {}).get('project') or {}
                if not issue or project.get('id')!=view.project_id or issue.get('archivedAt') or project.get('archivedAt'):
                    continue
                reviews=self.controller.review_history(task_id)
                for review in reviews:
                    self.comment(view.issue_id,comment_id(review['attempt_id'],'controller-review'),review_report(review,self.registry))
                for execution in self.controller.execution_history(task_id):
                    attempt=execution['attempt']
                    for kind,body in attempt_reports(execution,self.registry):
                        key=comment_id(attempt['attempt_id'],f"{attempt['run_id']}:{attempt['run_attempt']}:{kind}")
                        self.comment(view.issue_id,key,body)
                if repair_terminal(view):
                    self.comment(view.issue_id,comment_id(task_id,'auto-repair-report'),
                                 repair_report(view,self.controller.repair_parent(task_id),self.registry))
                body = self.render(view)
                signature = hashlib.sha256(json.dumps([body,view.active,view.phase.value,
                                                       view.attempt.get("stage"),view.attempt.get('attempt_id'),
                                                       view.attempt.get('attempt_kind'),view.eligible,
                                                       repair_terminal(view)]).encode()).hexdigest()
                if self.controller.report_signature('task:'+task_id) != signature:
                    if issue and (issue.get("project") or {}).get("id") == view.project_id:
                        if self.linear.status_comment(view.issue_id,comment_id(task_id,"controller-status"),body) is False:
                            raise RuntimeError('Linear status was not acknowledged')
                        review = json.loads(view.attempt.get("review_result") or "null")
                        denied = review and review.get("verdict") in {"rejected","needs_clarification"}
                        self.linear.set_selected(view.issue_id,view.active and not denied)
                        self.linear.set_queued(view.issue_id,view.phase=="queued" and view.eligible)
                        clarification=reviews and reviews[-1]['result'].get('verdict')=='needs_clarification'
                        self.linear.set_clarification(view.issue_id,bool(clarification and view.phase not in {'completed','canceled','skipped'}))
                        self.linear.set_label(view.issue_id,AUTO_REPAIR_LABEL,
                                              view.attempt.get('attempt_kind')=='repair' and not repair_terminal(view))
                        stage = view.attempt.get("stage") if view.phase=="executing" else "review" if view.phase=="published" else None
                        if view.phase=='held' and view.attempt.get('retry_kind')=='capacity':
                            stage='waiting_for_quota'
                        self.linear.set_stage(view.issue_id,stage)
                        enrolled=any(label.get('name')==self.registry.enrollment_label for label in project.get('labels',{}).get('nodes',[]))
                        if enrolled and route.linear_states and issue.get("state",{}).get("id") in {*route.ready_state_ids,route.linear_states.in_progress,route.linear_states.blocked}:
                            desired = (route.linear_states.done if view.phase=="completed" else
                                       route.linear_states.blocked if view.phase=="held" and not view.attempt.get("retry_kind") else
                                       route.linear_states.in_progress if view.phase in {"executing","published"} or (
                                           view.attempt.get("attempt_kind")=="repair" and view.attempt.get('retry_kind')!='capacity') else
                                       route.ready_state_ids[0])
                            if issue["state"]["id"] != desired and view.phase != "canceled":
                                self.linear.set_state(view.issue_id,desired)
                                if denied and desired==route.linear_states.blocked:
                                    self.observe_issue({**issue,'state':{**issue['state'],'id':desired}},known=view)
                    self.apply(ReportConfirmed('task:'+task_id,signature))
                for effect in effects:
                    self.apply(EffectUpdated(task_id,effect["effect_id"],"done"))
            except Exception as exc:
                logging.warning("Controller reporting deferred for %s (%s)",task_id[:12],type(exc).__name__)

    def comment(self,issue_id,key,body):
        signature=hashlib.sha256(body.encode()).hexdigest()
        if self.controller.report_signature('comment:'+key)!=signature:
            if self.linear.status_comment(issue_id,key,body) is False:
                raise RuntimeError('Linear report was not acknowledged')
            self.apply(ReportConfirmed('comment:'+key,signature))
