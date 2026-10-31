"""The only task-state writer. Transports supply facts, never arbitrary phases."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import math
import re

from .model import (Admit, Assign, DispatchObserved, ExternalObserved, Finish, Hold,
                    IssueObserved, Phase, Progress, Rejected, Retry, Review, Role,
                    Start, TaskSpec, TaskView, identity, SessionUpdated, EffectUpdated, Authorize, ExhaustAccount,
                    ContextObserved, InboxQueued, InboxDone, Initialize, ReportConfirmed, AccountRequest, InstructionRequest,
                    Resubmit, EvidenceChanged, ProjectionUpdated, dependency_admission_applies)
from common.policy import REVIEW_MAX_ATTEMPTS, REVIEW_RETRY_DELAYS, review_failure_class


class Controller:
    def __init__(self, database):
        self.database = database
        from .accounts import Accounts
        self.accounts=Accounts(database)
        self._upgrade_readiness()
        self._upgrade_intents()

    def _upgrade_intents(self):
        from .linear_state import record_intent
        with self.database.transaction() as db:
            if db.execute("INSERT OR IGNORE INTO metadata VALUES ('linear:intents-v1','1')").rowcount:
                for row in db.execute('SELECT payload FROM inbox').fetchall():
                    record_intent(db, json.loads(row[0]))

    def _upgrade_readiness(self):
        """Charge durable pre-upgrade history; never give old loops a fresh cycle."""
        with self.database.transaction() as db:
            if not db.execute("INSERT OR IGNORE INTO metadata VALUES ('readiness:bounded-v2','1')").rowcount:
                return
            for row in db.execute('SELECT * FROM tasks').fetchall():
                task = dict(row)
                attempts = db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY ordinal', (task['task_id'],)).fetchall()
                charged = 0
                for attempt in attempts:
                    capacity = attempt['retry_kind'] == 'capacity' and not attempt['review_result']
                    charged += not capacity
                    db.execute('UPDATE attempts SET review_charged=? WHERE attempt_id=?',
                               (not capacity, attempt['attempt_id']))
                # Imported history has no trustworthy explicit submission boundary.
                if db.execute('SELECT 1 FROM legacy_history WHERE task_id=?', (task['task_id'],)).fetchone():
                    charged = max(charged, REVIEW_MAX_ATTEMPTS)
                db.execute('UPDATE tasks SET review_attempts=?,review_submitted_at=? WHERE task_id=?',
                           (charged, attempts[0]['started_at'] if attempts else 0, task['task_id']))
                if task['phase'] in {'completed', 'canceled', 'skipped'}:
                    continue
                if not attempts:
                    if charged:
                        self._review_attention(db, task, None, 'legacy_unverified',
                                               'Imported readiness history has no trustworthy explicit submission boundary; inspect the prior failure before resubmitting.', 0)
                        self._report(db, task['task_id'])
                    continue
                attempt = dict(attempts[-1])
                result = json.loads(attempt['review_result'] or 'null')
                if not attempt['coding_started'] and (attempt['retry_kind'] in {'review', 'startup', 'context'}
                        or result and result.get('verdict') in {'retry', 'rejected', 'needs_clarification'}
                        or charged >= REVIEW_MAX_ATTEMPTS and not result):
                    detail = (result or {}).get('summary') or 'Previous review execution did not produce a verified assessment.'
                    self._review_attention(db, task, attempt, 'legacy_unverified',
                                           'Legacy readiness retry requires intervention. ' + detail, attempt['started_at'])
                    self._report(db, task['task_id'])

    def attempt_history(self, task_id):
        with self.database.transaction() as db:
            return tuple(dict(row) for row in db.execute(
                'SELECT * FROM attempts WHERE task_id=? ORDER BY ordinal', (task_id,)))

    def inspect(self, task_id):
        with self.database.transaction() as db:
            return self._view(db, task_id)

    def authorize(self, task_id, attempt_id, role, action='execute', *, now):
        """A fresh, read-only policy query; no report effects or network calls."""
        if type(now) not in (float, int) or not math.isfinite(now):
            raise ValueError('A finite authorization timestamp is required')
        from .authority import authorize
        with self.database.transaction() as db:
            task = self._task(db, task_id)
            attempt = self._attempt(db, task, attempt_id)
            authorize(db, task, attempt, role, action, now, self.accounts)
            return self._view(db, task_id)

    def accepts_instructions(self, view, *, now):
        try:
            self.authorize(view.task_id, view.attempt['attempt_id'], Role.CODING, 'instructions', now=now)
            return True
        except (Rejected, KeyError):
            return False

    def context_failure(self, task_id, attempt_id, error, *, now):
        """Interpret trusted retrieval facts; absence of a response is not drift."""
        from common.clients import RequestError
        from common.context import ContextError
        if isinstance(error, RequestError):
            if error.retryable or error.status not in {401, 403, 404}:
                raise error
        elif isinstance(error, ContextError):
            if error.status in {'timeout', 'incomplete'}:
                raise RequestError(503) from None
            if error.status in {'budget_exceeded', 'oversized'}:
                raise Rejected('Related context retrieval budget is exhausted') from None
            if error.status not in {'material_evidence_changed', 'required_source_unavailable',
                                    'new_evidence_requires_review', 'missing_approval', 'required_source_incomplete',
                                    'missing', 'denied', 'inaccessible', 'identity_mismatch', 'changed'}:
                raise Rejected('Related context could not be validated') from None
        else:
            raise error
        self.apply(EvidenceChanged(task_id, attempt_id), now=now)
        raise Rejected('Related context requires a fresh readiness assessment') from None

    @staticmethod
    def observes_workflow(known, policy):
        from .linear_state import observes_workflow
        return observes_workflow(known, policy)

    def _projected_states(self, issue_id):
        with self.database.transaction() as db:
            payloads = db.execute("SELECT e.payload FROM effects e JOIN tasks t USING(task_id) "
                                  "WHERE t.issue_id=? AND e.kind='linear_state'", (issue_id,)).fetchall()
            return {value['state'] for row in payloads if (value := json.loads(row[0])).get('issued')
                    or value.get('outcome') in {'claimed', 'uncertain', 'confirmed'}}

    def submission_snapshot(self, snapshot, submission, blocked):
        from .linear_state import submission_snapshot
        return submission_snapshot(snapshot, submission, blocked, owned_states=self._projected_states(snapshot['id']))

    def may_project_attention(self, snapshot, attention_at, blocked):
        from .linear_state import may_project_attention
        return may_project_attention(snapshot, attention_at, blocked, owned_states=self._projected_states(snapshot['id']))

    def report_signature(self,key):
        with self.database.transaction() as db:
            row=db.execute('SELECT signature FROM comment_signatures WHERE key=?',(key,)).fetchone()
            return row[0] if row else None

    def for_attempt(self, attempt_id):
        with self.database.transaction() as db:
            row = db.execute("SELECT task_id FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise Rejected("Unknown dispatch identity")
            return self._view(db, row[0])

    def previous_attempt(self, task_id):
        with self.database.transaction() as db:
            attempts = db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY ordinal DESC LIMIT 2", (task_id,)).fetchall()
            if len(attempts)<2:
                return None
            previous=dict(attempts[1])
            session=db.execute("SELECT data FROM sessions WHERE attempt_id=? AND phase='coding'", (previous["attempt_id"],)).fetchone()
            return {**previous,"data":json.loads(session[0]) if session else {}}

    def execution_history(self, task_id):
        with self.database.transaction() as db:
            result=[]
            for row in db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY ordinal",(task_id,)).fetchall():
                session=db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase='coding'",(row['attempt_id'],)).fetchone()
                if session:
                    result.append({'attempt':dict(row),'session':{**dict(session),'data':json.loads(session['data'])}})
            return tuple(result)

    def review_history(self,task_id):
        with self.database.transaction() as db:
            return tuple({**dict(row), 'result': json.loads(row['review_result']) if row['review_result'] else {
                'verdict': 'execution_failed' if row['retry_decision'] == 'attention' else 'temporary_failure',
                'summary': row['review_detail'] or row['failure_class'].replace('_', ' ')}}
                for row in db.execute('SELECT * FROM attempts WHERE task_id=? AND '
                                      '(review_result IS NOT NULL OR failure_class != \'\') ORDER BY ordinal',
                                      (task_id,)).fetchall())

    def repair_parent(self, task_id):
        with self.database.transaction() as db:
            row=db.execute("SELECT * FROM attempts WHERE task_id=? AND coding_started=1 AND attempt_kind!='repair' ORDER BY ordinal DESC LIMIT 1",(task_id,)).fetchone()
            if row is None:
                return None
            previous=dict(row)
            session=db.execute("SELECT data FROM sessions WHERE attempt_id=? AND phase='coding'",(previous["attempt_id"],)).fetchone()
            return {**previous,"data":json.loads(session[0]) if session else {}}

    def waiting(self, repository=None):
        with self.database.transaction() as db:
            query = "SELECT task_id FROM tasks WHERE phase='queued' AND eligible=1 AND dependencies_ready=1"
            args = ()
            if repository is not None:
                query += " AND repository=?"
                args = (repository.lower(),)
            query += " ORDER BY CASE priority WHEN 0 THEN 5 ELSE priority END, sort_order, sequence"
            return tuple(self._view(db, row[0]) for row in db.execute(query, args).fetchall())

    def active(self):
        with self.database.transaction() as db:
            return tuple(self._view(db, r[0]) for r in db.execute("SELECT task_id FROM grants").fetchall())

    def tasks(self, *, unsettled=False):
        with self.database.transaction() as db:
            query = "SELECT task_id FROM tasks"
            if unsettled:
                query += " WHERE phase='queued' OR task_id IN (SELECT task_id FROM grants)"
            return tuple(self._view(db, r[0]) for r in db.execute(query).fetchall())

    def holds(self):
        with self.database.transaction() as db:
            return {r[0]: r[1] for r in db.execute("SELECT owner,reason FROM holds")}

    def effects(self, kind=None):
        with self.database.transaction() as db:
            query = "SELECT * FROM effects WHERE state!='done'"
            return tuple(dict(row) for row in db.execute(query + (" AND kind=?" if kind else ""), (kind,) if kind else ()))

    def inbox(self):
        with self.database.transaction() as db:
            return tuple((r[0],json.loads(r[1])) for r in db.execute("SELECT event_id,payload FROM inbox WHERE done=0 ORDER BY rowid"))

    def apply(self, command, *, now):
        if not isinstance(now, (float, int)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValueError("A finite observation timestamp is required")
        with self.database.transaction() as db:
            if isinstance(command,InstructionRequest):
                from .instructions import apply_request
                return apply_request(self,db,command,now)
            if isinstance(command, ReportConfirmed):
                if (not re.fullmatch(r'[a-zA-Z0-9:-]{1,200}',command.key)
                        or not re.fullmatch(r'[0-9a-f]{64}',command.signature)):
                    raise Rejected('Invalid reporting acknowledgement')
                db.execute('INSERT INTO comment_signatures VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET '
                           'signature=excluded.signature,confirmed_at=excluded.confirmed_at',
                           (command.key,command.signature,now))
                return None
            if isinstance(command, Initialize):
                if db.execute("INSERT OR IGNORE INTO metadata VALUES ('initialized','1')").rowcount:
                    db.execute("INSERT OR IGNORE INTO holds VALUES ('operator:rebuild','Production admission is held for rebuild acceptance.')")
                if db.execute("INSERT OR IGNORE INTO metadata VALUES ('projection:usage-and-repair-v1','1')").rowcount:
                    rows=db.execute("SELECT task_id FROM tasks WHERE EXISTS (SELECT 1 FROM attempts a "
                                    "WHERE a.task_id=tasks.task_id AND (a.review_result IS NOT NULL OR EXISTS "
                                    "(SELECT 1 FROM sessions s WHERE s.attempt_id=a.attempt_id AND s.phase='coding')))").fetchall()
                    for row in rows:
                        db.execute('UPDATE tasks SET revision=revision+1 WHERE task_id=?',(row[0],))
                        self._report(db,row[0])
                return None
            if isinstance(command, InboxQueued):
                if not re.fullmatch(r"[0-9a-f]{64}", command.event_id) or len(json.dumps(command.event)) > 3000:
                    raise Rejected("Invalid event receipt")
                db.execute("INSERT OR IGNORE INTO inbox(event_id,payload) VALUES (?,?)", (command.event_id,json.dumps(command.event)))
                from .linear_state import record_intent, human_withdrawal
                record_intent(db, command.event)
                for row in db.execute("SELECT * FROM tasks WHERE issue_id=? AND phase NOT IN ('completed','canceled','skipped')",
                                      (command.event.get('id'),)).fetchall():
                    task = dict(row)
                    if human_withdrawal(db, task):
                        attempt = self._attempt(db, task, task['current_attempt']) if task['current_attempt'] else None
                        if attempt and not task['review_attention']:
                            self._review_attention(db, task, attempt, 'human_withdrawal',
                                                   'Human workflow action withdrew execution authorization.', now)
                        self._update(db, self._task(db, task['task_id']), eligible=0, pending_submission_at=0)
                        self._report(db, task['task_id'])
                return None
            if isinstance(command, InboxDone):
                row = db.execute('SELECT payload FROM inbox WHERE event_id=?', (command.event_id,)).fetchone()
                event = json.loads(row[0]) if row else {}
                submission = event.get('submission')
                policies = db.execute("SELECT w.policy FROM tasks t JOIN linear_workflows w USING(task_id) "
                                      "WHERE t.issue_id=? AND t.eligible=0 AND t.phase NOT IN ('completed','canceled','skipped')",
                                      (event.get('id'),)).fetchall()
                if submission and any(submission['to_state'] in json.loads(row[0])['ready'] for row in policies):
                    # An acknowledged structurally ineligible action is explicitly
                    # withdrawn, rather than silently spending an idempotency key.
                    db.execute('UPDATE linear_intents SET withdrawn=1 WHERE issue_id=? AND action_id=?',
                               (event['id'], identity(event['id'], submission)))
                db.execute("UPDATE inbox SET done=1 WHERE event_id=?", (command.event_id,))
                return None
            if isinstance(command, Hold):
                if (not re.fullmatch(r"[\w:./-]{1,200}", command.owner) or not isinstance(command.reason, str)
                        or len(command.reason)>2000):
                    raise Rejected("Invalid admission hold")
                previous=db.execute("SELECT reason FROM holds WHERE owner=?",(command.owner,)).fetchone()
                changed=bool(previous) if command.release else previous is None or previous[0]!=command.reason
                if command.release:
                    db.execute("DELETE FROM holds WHERE owner=?", (command.owner,))
                else:
                    db.execute("INSERT INTO holds VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET reason=excluded.reason",
                               (command.owner, command.reason))
                if changed:
                    waiting=db.execute("SELECT task_id FROM tasks WHERE phase IN ('queued','held') AND eligible=1").fetchall()
                    db.execute("UPDATE tasks SET revision=revision+1 WHERE phase IN ('queued','held') AND eligible=1")
                    for row in waiting:
                        self._report(db,row[0])
                return None
            if isinstance(command, IssueObserved):
                if self._issue(db, command, now) is False:
                    return None
                return self._view(db, command.task_id)
            task = self._task(db, command.task_id)
            if isinstance(command, ProjectionUpdated):
                from .linear_state import update_projection
                result = update_projection(db, task, command)
                if result and command.outcome == 'confirmed' and task['review_attention'] and task['eligible']:
                    self._update(db, task, eligible=0)
                    self._report(db, task['task_id'])
                return result
            if isinstance(command,AccountRequest):
                attempt=self._attempt(db,task,command.attempt_id)
                if command.operation!='save':
                    self.authorize(command.task_id,command.attempt_id,command.role,command.operation,now=now)
                view=self._view(db,command.task_id)
                owner=f"{command.attempt_id}:{attempt['run_id']}:{attempt['run_attempt']}:{command.role.phase}"
                from .account_commands import execute
                return execute(self,db,view,command.role,owner,command.operation,command.data,now)
            if isinstance(command, EffectUpdated):
                if command.state not in {"claimed", "done"}:
                    raise Rejected("Invalid external effect state")
                current = db.execute("SELECT * FROM effects WHERE effect_id=? AND task_id=?",
                                     (command.effect_id, command.task_id)).fetchone()
                if not current or current["state"] == "done" or command.state == "claimed" and current["state"] != "pending":
                    raise Rejected("External effect is already claimed or complete")
                db.execute("UPDATE effects SET state=? WHERE effect_id=?", (command.state, command.effect_id))
                return self._view(db, command.task_id)
            if task["phase"] in {"completed", "canceled", "skipped"}:
                raise Rejected("Task is terminal")
            if isinstance(command, Admit):
                if task["phase"] != "queued":
                    raise Rejected("Task is not queued")
                self._new_attempt(db, task, command.spec, now, initial=True)
            elif isinstance(command, Retry):
                self._retry(db, task, command, now)
            elif isinstance(command, Resubmit):
                self._resubmit(db, task, command, now)
            else:
                attempt = self._attempt(db, task, command.attempt_id)
                if isinstance(command, DispatchObserved):
                    if command.status not in {"accepted", "rejected", "unknown"}:
                        raise Rejected("Invalid dispatch observation")
                    previous = attempt["dispatch_status"]
                    if previous == 'rejected':
                        if command.status != 'rejected':
                            raise Rejected('Rejected dispatch cannot be replaced')
                        return self._view(db, command.task_id)
                    if previous == "accepted" and command.status != "accepted":
                        raise Rejected("Accepted dispatch cannot be replaced with uncertainty")
                    db.execute("UPDATE attempts SET dispatch_status=? WHERE attempt_id=?",
                               (command.status, command.attempt_id))
                    if command.status == "rejected":
                        db.execute("UPDATE attempts SET stopped=1 WHERE attempt_id=?", (command.attempt_id,))
                        self._review_failure(db, task, attempt, command.failure, now)
                elif isinstance(command, Start):
                    self._start(db, task, attempt, command, now)
                elif isinstance(command, Assign):
                    self._assign(db, task, attempt, command, now)
                elif isinstance(command, Review):
                    self._review(db, task, attempt, command, now)
                elif isinstance(command, Progress):
                    self._progress(db, task, attempt, command, now)
                elif isinstance(command, Finish):
                    self._finish(db, task, attempt, command, now)
                elif isinstance(command, ExternalObserved):
                    self._external(db, task, attempt, command, now)
                elif isinstance(command, SessionUpdated):
                    self._session(db, task, attempt, command)
                elif isinstance(command, Authorize):
                    return self.authorize(command.task_id, command.attempt_id, command.role, command.action, now=now)
                elif isinstance(command, ExhaustAccount):
                    self._exhaust(db, task, attempt, command, now)
                elif isinstance(command, ContextObserved):
                    if (asdict(command.binding) != json.loads(task["spec"])
                            and not task['review_attention'] and not attempt['retry_kind']):
                        self._update(db, task, phase="held", reason="Reviewed task context changed; a fresh review is required.")
                        if not attempt["coding_started"] and not attempt["retry_kind"]:
                            self._review_attention(db, task, attempt, 'context_changed',
                                                   'Reviewed context changed before coding; approval is invalid and a fresh assessment is required.', now)
                elif isinstance(command, EvidenceChanged):
                    self._review_attention(db, task, attempt, 'context_changed',
                                           'Related context changed, conflicts with approval, or is unavailable; a fresh readiness assessment is required.', now)
                else:
                    raise Rejected("Unsupported controller command")
            # Local projection shares this commit. Export runs on a separate thread,
            # after the transaction; projection failures cannot reject the command.
            from .telemetry import capture
            capture(db, command, now)
            self._report(db, command.task_id)
            return self._view(db, command.task_id)

    @staticmethod
    def _task(db, task_id):
        row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise Rejected("Unknown task")
        return dict(row)

    @staticmethod
    def _attempt(db, task, attempt_id):
        if task["current_attempt"] != attempt_id:
            raise Rejected("Attempt was superseded")
        row = db.execute("SELECT * FROM attempts WHERE attempt_id=? AND task_id=?",
                         (attempt_id, task["task_id"])).fetchone()
        if row is None:
            raise Rejected("Unknown attempt")
        return dict(row)

    @staticmethod
    def _update(db, task, **fields):
        if fields and all(task.get(key) == value for key, value in fields.items()):
            return
        assignments = [*(f"{k}=?" for k in fields), "revision=revision+1"]
        db.execute("UPDATE tasks SET " + ",".join(assignments) + " WHERE task_id=?",
                   (*fields.values(), task["task_id"]))

    def _issue(self, db, command, now):
        if (type(command.priority) is not int or command.priority not in range(5)
                or not math.isfinite(command.sort_order) or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", command.repository)
                or type(command.eligible) is not bool or type(command.dependencies_ready) is not bool
                or type(command.canceled) is not bool or type(command.submission_current) is not bool):
            raise Rejected("Invalid issue observation")
        row = db.execute("SELECT * FROM tasks WHERE task_id=?", (command.task_id,)).fetchone()
        from .linear_state import observed_eligibility
        command = replace(command, eligible=observed_eligibility(db, dict(row) if row else None, command))
        if row is None:
            if command.workflow and not command.eligible:
                return False
            if db.execute("SELECT 1 FROM tasks WHERE issue_id=? AND current_attempt IS NOT NULL "
                          "AND phase NOT IN ('completed','canceled','skipped')", (command.issue_id,)).fetchone():
                raise Rejected('An unresolved submission already owns this issue identity')
            db.execute("INSERT INTO tasks(task_id,issue_id,project_id,generation,repository,identifier,phase,"
                       "eligible,dependencies_ready,priority,sort_order,content_revision,reason,canceled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (command.task_id, command.issue_id, command.project_id, command.generation,
                        command.repository.lower(), command.identifier, "queued" if command.eligible else "skipped",
                        command.eligible, command.dependencies_ready, command.priority, command.sort_order,
                        command.content_revision, command.reason, command.canceled))
            if command.workflow:
                db.execute('INSERT INTO linear_workflows VALUES (?,?)', (command.task_id, json.dumps(command.workflow)))
        else:
            task = dict(row)
            changes = dict(eligible=command.eligible, dependencies_ready=command.dependencies_ready,
                           priority=command.priority, sort_order=command.sort_order,
                           content_revision=command.content_revision, canceled=command.canceled)
            active = db.execute("SELECT 1 FROM grants WHERE task_id=?", (command.task_id,)).fetchone()
            withdrawn = not command.eligible or not command.submission_current
            if task['pending_submission_at'] and withdrawn:
                changes['pending_submission_at'] = 0
            if not active and task["phase"] in {"queued", "skipped"}:
                changes.update(phase="queued" if command.eligible else "skipped", reason=command.reason)
                if task['phase']=='skipped' and command.eligible:
                    changes['sequence']=db.execute('SELECT MAX(sequence)+1 FROM tasks').fetchone()[0]
                if task['review_cycle'] > 1 and withdrawn:
                    reason = 'Human readiness resubmission was withdrawn; explicitly move the issue to Todo to resubmit.'
                    changes.update(phase='held', reason=reason, review_attention=reason,
                                   review_attention_at=task['review_submitted_at'])
            if active and task["spec"]:
                changed = json.loads(task["spec"])["content_revision"] != command.content_revision
                attempt = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (task['current_attempt'],)).fetchone()
                if task['review_attention'] or attempt and attempt['retry_kind']:
                    pass  # Reporting/eligibility changes cannot erase actionable feedback.
                elif changed or not command.eligible:
                    changes.update(phase="held", reason="Task content or eligibility changed; execution authorization held.")
                    if command.workflow and command.workflow['state'] == command.workflow['blocked']:
                        self._review_attention(db, task, dict(attempt), 'human_withdrawal',
                                               'Issue moved to Needs attention; execution authorization held.', now)
                elif not command.dependencies_ready and dependency_admission_applies(attempt):
                    changes.update(phase="held", reason=command.reason or "Prerequisites are unresolved.")
            if any(task[k] != v for k, v in changes.items()):
                self._update(db, task, **changes)
            self._accept_resubmission(db, self._task(db, command.task_id))
        self._report(db, command.task_id)

    def _new_attempt(self, db, task, spec, now, *, initial=False, kind="normal"):
        if not isinstance(spec, TaskSpec) or spec.content_revision != task["content_revision"]:
            raise Rejected("Task specification is stale")
        if spec.target_repository and spec.target_repository.lower()!=task['repository']:
            raise Rejected('Task specification belongs to another repository')
        if not task["eligible"] or not task["dependencies_ready"] or db.execute("SELECT 1 FROM holds LIMIT 1").fetchone():
            raise Rejected("New admissions are held")
        if task['review_attention']:
            raise Rejected('The readiness budget requires an explicit human resubmission')
        if task['review_attempts'] >= REVIEW_MAX_ATTEMPTS:
            attempt = self._attempt(db, task, task['current_attempt']) if task['current_attempt'] else None
            self._review_attention(db, task, attempt,
                                   'budget_exhausted', 'Readiness automatic attempt budget is exhausted.', now)
            return
        if initial:
            previous = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (task['current_attempt'],)).fetchone()
            if previous and previous['retry_kind'] == 'review' and previous['retry_at'] > now:
                raise Rejected('The recorded readiness retry delay has not elapsed')
            head = db.execute("SELECT task_id FROM tasks WHERE repository=? AND phase='queued' AND eligible=1 "
                              "AND dependencies_ready=1 AND NOT EXISTS (SELECT 1 FROM attempts a "
                              "WHERE a.attempt_id=tasks.current_attempt AND a.retry_kind='review' AND a.retry_at>?) "
                              "ORDER BY CASE priority WHEN 0 THEN 5 ELSE priority END,sort_order,sequence LIMIT 1",
                              (task["repository"], now)).fetchone()
            if not head or head[0] != task["task_id"]:
                raise Rejected("A higher-priority task owns the queue head")
            if db.execute("SELECT 1 FROM grants WHERE repository=?", (task["repository"],)).fetchone():
                raise Rejected("Repository already has an active task")
            db.execute("INSERT INTO grants VALUES (?,?)", (task["repository"], task["task_id"]))
        else:
            from .authority import admission
            admission(db, task, allow_hold=True)
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM attempts WHERE task_id=?", (task["task_id"],)).fetchone()[0]
        attempt_id = identity(task["task_id"], ordinal)
        db.execute("INSERT INTO attempts(attempt_id,task_id,ordinal,dispatch_status,started_at,attempt_kind,review_cycle) "
                   "VALUES(?,?,?,'intent',?,?,?)", (attempt_id, task["task_id"], ordinal, now, kind, task['review_cycle']))
        self._update(db, task, phase="reviewing", spec=json.dumps(asdict(spec), sort_keys=True),
                     current_attempt=attempt_id, reason="Waiting for GitHub review preflight.",
                     review_attempts=task['review_attempts']+1,
                     review_submitted_at=task['review_submitted_at'] or now)
        db.execute("INSERT INTO effects(effect_id,task_id,kind,payload) VALUES(?,?,?,?)",
                   (attempt_id, task["task_id"], "dispatch", json.dumps({"attempt_id": attempt_id})))
        if initial:
            self._queue_reports(db,task['repository'])

    def _start(self, db, task, attempt, command, now):
        self.authorize(command.task_id, command.attempt_id, command.role, 'start', now=now)
        if not isinstance(command.role, Role) or not re.fullmatch(r"[1-9][0-9]*", command.run_id) or command.run_attempt < 1:
            raise Rejected("Invalid authenticated job identity")
        if attempt["stopped"]:
            raise Rejected("This attempt already stopped")
        if attempt["run_id"] and (attempt["run_id"], attempt["run_attempt"]) != (command.run_id, command.run_attempt):
            raise Rejected("This dispatch belongs to another GitHub attempt")
        if command.role.phase == "coding":
            result = json.loads(attempt["review_result"] or "null")
            if not result or result.get("verdict") != "ready" or not command.review_job_succeeded:
                raise Rejected("A valid successful review job is required before coding")
        db.execute("UPDATE attempts SET run_id=?,run_attempt=?,dispatch_status='accepted' WHERE attempt_id=?",
                   (command.run_id, command.run_attempt, command.attempt_id))
        db.execute("INSERT OR IGNORE INTO sessions(attempt_id,phase,deadline) VALUES (?,?,?)",
                   (command.attempt_id, command.role.phase, now + (720 if command.role.phase == "review" else 6 * 3600)))
        session = dict(db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase=?",
                                  (command.attempt_id, command.role.phase)).fetchone())
        if session["finished"] or session["deadline"] <= now:
            raise Rejected("This phase is finished or expired")
        if command.role.selector:
            if session["agent_started"]:
                raise Rejected("Account preflight cannot restart an agent phase")
            db.execute("UPDATE sessions SET selector_started=1 WHERE attempt_id=? AND phase=?",
                       (command.attempt_id, command.role.phase))
        else:
            if not session["account_id"]:
                raise Rejected("Preflight must assign an account before agent startup")
            deadline = (now + (720 if command.role.phase == "review" else 6*3600)
                        if not session["agent_started"] else session["deadline"])
            db.execute("UPDATE sessions SET agent_started=1,deadline=? WHERE attempt_id=? AND phase=?",
                       (deadline, command.attempt_id, command.role.phase))
            if not session['agent_started']:
                data={**json.loads(session['data']),'agent_started_at':now}
                db.execute("UPDATE sessions SET data=? WHERE attempt_id=? AND phase=?",
                           (json.dumps(data),command.attempt_id,command.role.phase))

    def _session(self, db, task, attempt, command):
        allowed = {"thread_id", "turn_id", "steering_closed", "account_email", "account_plan",
                   "usage_before", "usage_after", "usage_segments", "exhausted_account_ids",
                   "account_failover", "probe_account_id", "released_account_id", "heartbeat_until",
                   "repair_validation_passed", "repair_result", "failure", "quota_wait", "wip_branch", "wip_commit_sha",
                   "original_account_id", "steering_started_at", "account_reserved"}
        allowed.update({'provider','model','effort'})
        if command.phase not in {"review", "coding"} or not isinstance(command.fields, dict) or set(command.fields) - allowed:
            raise Rejected("Unsupported execution metadata")
        row = db.execute("SELECT data FROM sessions WHERE attempt_id=? AND phase=?", (command.attempt_id, command.phase)).fetchone()
        if not row:
            raise Rejected("Unknown phase metadata")
        data = {**json.loads(row[0]), **command.fields}
        db.execute("UPDATE sessions SET data=? WHERE attempt_id=? AND phase=?",
                   (json.dumps(data), command.attempt_id, command.phase))
        self._update(db, task)

    def _assign(self, db, task, attempt, command, now):
        if not isinstance(command.role, Role):
            raise Rejected("Invalid account role")
        if not re.fullmatch(r"(?:cursor:)?[0-9a-f]{64}", command.account_id):
            raise Rejected("Invalid provider account identity")
        row = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase=?", (command.attempt_id, command.role.phase)).fetchone()
        data = json.loads(row["data"]) if row else {}
        replacement = command.role == Role.CODING and data.get("account_failover") and not row["account_id"]
        if not command.role.selector and not replacement:
            raise Rejected("Only authenticated preflight can assign an account")
        self.authorize(command.task_id, command.attempt_id, command.role, 'bind_account', now=now)
        if not row or not row["selector_started"] or row["agent_started"] and not replacement or row["finished"]:
            raise Rejected("Account preflight is not active")
        if replacement and (command.account_id.startswith("cursor:") or command.account_id in data.get("exhausted_account_ids", [])
                            or data.get("probe_account_id")):
            raise Rejected("Replacement account probe must be released and eligible")
        if row["account_id"] and row["account_id"] != command.account_id:
            raise Rejected("Account assignment cannot be replaced")
        deadline = now+900 if not row["account_id"] and not replacement else row["deadline"]
        db.execute("UPDATE sessions SET account_id=?,deadline=? WHERE attempt_id=? AND phase=?",
                   (command.account_id, deadline, command.attempt_id, command.role.phase))

    def _exhaust(self, db, task, attempt, command, now):
        self.authorize(command.task_id, command.attempt_id, Role.CODING, 'exhaust_account', now=now)
        row = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase='coding'", (command.attempt_id,)).fetchone()
        data = json.loads(row["data"]) if row else {}
        if (not row or not row["agent_started"] or row["finished"] or attempt["stage"] != "coding"
                or command.account_id != row["account_id"] or command.account_id.startswith("cursor:")
                or data.get("released_account_id") != command.account_id or not data.get("thread_id")
                or data["thread_id"] != command.thread_id or data.get("steering_closed")):
            raise Rejected("Failover requires the released active Codex account and same coding thread")
        exhausted = data.get("exhausted_account_ids", [])
        if command.account_id not in exhausted:
            exhausted.append(command.account_id)
        data.update(exhausted_account_ids=exhausted, account_failover=True, released_account_id="")
        db.execute("UPDATE sessions SET account_id='',data=? WHERE attempt_id=? AND phase='coding'",
                   (json.dumps(data), command.attempt_id))
        self._update(db, task)

    def _release_reservation(self,db,task,attempt,phase,now):
        session=db.execute('SELECT * FROM sessions WHERE attempt_id=? AND phase=?',(attempt['attempt_id'],phase)).fetchone()
        data=json.loads(session['data']) if session else {}
        if not data.get('account_reserved') or not session['account_id']:
            return
        owner=f"{attempt['attempt_id']}:{attempt['run_id']}:{attempt['run_attempt']}:{phase}"
        account,_=self.accounts.get(session['account_id'])
        if account['owner']==owner and account['lease_until']>now:
            self.accounts.save(session['account_id'],owner,account['fence'],release=True)
        data.update(account_reserved=False,released_account_id=session['account_id'])
        db.execute('UPDATE sessions SET data=? WHERE attempt_id=? AND phase=?',(json.dumps(data),attempt['attempt_id'],phase))

    def _review(self, db, task, attempt, command, now):
        from common.policy import validate_result, redact_review_value
        self.authorize(command.task_id, command.attempt_id, Role.REVIEW, 'review', now=now)
        session = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase='review'", (command.attempt_id,)).fetchone()
        if not session or not session["agent_started"] or session["finished"]:
            raise Rejected("The review job is not accepting a result")
        if asdict(command.binding) != json.loads(task["spec"]):
            raise Rejected("Readiness binding does not match current task specification")
        try:
            result = redact_review_value(validate_result(command.result))
        except (ValueError, TypeError) as exc:
            # Only the validator's bounded diagnostics are retained, never raw output.
            result = {"verdict": "rejected", "summary": "Invalid reviewer result: " + str(exc)[:1500]}
        if result.get("verdict") == "ready":
            from .context import seal
            seal(db, command.attempt_id, command.evidence_revision)
        verdict = result["verdict"]
        self._release_reservation(db,task,attempt,'review',now)
        db.execute("UPDATE attempts SET review_result=? WHERE attempt_id=?", (json.dumps(result), command.attempt_id))
        db.execute("UPDATE sessions SET finished=1 WHERE attempt_id=? AND phase='review'", (command.attempt_id,))
        if verdict == 'ready':
            db.execute("UPDATE attempts SET retry_kind='',retry_at=0,retry_decision='approved' WHERE attempt_id=?",
                       (command.attempt_id,))
            self._update(db, task, phase='ready', reason=result['summary'])
        else:
            detail = result['summary']
            for entry in result.get('evidence', []):
                detail += f"\nEvidence: {entry['evidence']}. {entry['reason']} Next action: {entry['next_action']}"
            for blocker in result.get('blockers', []):
                detail += f"\nDecision: {blocker['decision']} Consequence: {blocker['consequence']} Question: {blocker['question']}"
            self._review_attention(db, task, attempt, verdict, detail, now)

    def _progress(self, db, task, attempt, command, now):
        self.authorize(command.task_id, command.attempt_id, Role.CODING, 'progress', now=now)
        if command.stage not in {"initializing", "selecting_account", "coding", "testing", "publishing"}:
            raise Rejected("Invalid coding stage")
        session = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase='coding'", (command.attempt_id,)).fetchone()
        if not session or not session["agent_started"] or session["finished"]:
            raise Rejected("Coding job is not active")
        order = ("initializing", "selecting_account", "coding", "testing", "publishing")
        correction = attempt["stage"] == "testing" and command.stage == "coding" and not json.loads(session["data"]).get("steering_closed")
        if order.index(command.stage) < order.index(attempt["stage"]) and not correction:
            raise Rejected("Coding stage cannot move backwards")
        if command.stage == "coding" and command.context_sha != json.loads(task["spec"])["context_sha"]:
            raise Rejected("Coding checkout does not match reviewed context")
        if command.fields:
            self._session(db, task, attempt, SessionUpdated(command.task_id, command.attempt_id, "coding", command.fields))
        db.execute("UPDATE attempts SET stage=?,coding_started=MAX(coding_started,?) WHERE attempt_id=?",
                   (command.stage, command.stage in {"coding", "testing", "publishing"}, command.attempt_id))
        self._update(db, task, phase="executing", reason="Worker stage: " + command.stage)

    def _finish(self, db, task, attempt, command, now):
        if command.phase not in {"review", "coding"} or command.outcome not in {"completed", "failed", "waiting_for_quota"}:
            raise Rejected("Invalid phase outcome")
        session = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase=?", (command.attempt_id, command.phase)).fetchone()
        if not session:
            raise Rejected("Unknown execution phase")
        if command.phase=="coding" and command.outcome=="completed" and attempt["attempt_kind"]=="repair":
            validation=json.loads(session["data"])
            if not validation.get("repair_validation_passed") or not validation.get("repair_result"):
                raise Rejected("Repair must record successful validation before completion")
        if session["finished"]:
            if command.phase == 'review' or (attempt["agent_outcome"], attempt["pull_request"]) == (command.outcome, command.pull_request):
                return
            raise Rejected("This execution phase already has a result")
        if command.pull_request and not re.fullmatch(re.escape("https://github.com/" + task["repository"] + "/pull/") + r"[1-9][0-9]*", command.pull_request, flags=re.I):
            raise Rejected("Invalid publication reference")
        self._release_reservation(db,task,attempt,command.phase,now)
        session=db.execute('SELECT * FROM sessions WHERE attempt_id=? AND phase=?',(command.attempt_id,command.phase)).fetchone()
        # A callback records a result; it never proves the Actions cleanup stopped.
        db.execute("UPDATE sessions SET finished=1 WHERE attempt_id=? AND phase=?", (command.attempt_id, command.phase))
        data={**json.loads(session['data']),'finished_at':now}
        if command.failure:
            from common.diagnostics import validate_failure
            data['failure'] = validate_failure(command.failure)
        db.execute("UPDATE sessions SET data=? WHERE attempt_id=? AND phase=?",
                   (json.dumps(data),command.attempt_id,command.phase))
        if command.phase == "coding":
            db.execute("UPDATE attempts SET agent_outcome=?,pull_request=? WHERE attempt_id=?",
                       (command.outcome, command.pull_request or attempt["pull_request"], command.attempt_id))
        if command.outcome == "waiting_for_quota":
            if not task['review_attention']:
                db.execute("UPDATE attempts SET retry_kind='capacity',retry_at=?,retry_decision='capacity_wait' WHERE attempt_id=?",
                           (now + 900, command.attempt_id))
                if command.phase == 'review' and attempt['review_charged'] and not attempt['review_result']:
                    db.execute('UPDATE tasks SET review_attempts=review_attempts-1 WHERE task_id=?', (task['task_id'],))
                    db.execute('UPDATE attempts SET review_charged=0 WHERE attempt_id=?', (command.attempt_id,))
        if command.phase == 'review' and command.outcome == 'failed':
            if not task['review_attention'] and not attempt['retry_kind']:
                self._review_failure(db, task, attempt, command.failure, now)
            return
        if task['review_attention']:
            return
        self._update(db, task, phase="published" if command.pull_request else "held",
                     reason=command.reason or ("Waiting for CI and merge." if command.pull_request else "Waiting for verified GitHub completion."))

    def _review_attention(self, db, task, attempt, classification, detail, now):
        from common.policy import redact_review_text
        reason = redact_review_text(detail) + '\nResolve the failure or supply the evidence, then explicitly move the issue to Todo to resubmit.'
        if attempt:
            db.execute("UPDATE attempts SET retry_kind='',retry_at=0,failure_class=?,retry_decision='attention',review_detail=? WHERE attempt_id=?",
                       (classification, reason, attempt['attempt_id']))
        # Further feedback in this held cycle is not a new submission boundary.
        # A late callback must not render a queued human action artificially old.
        attention_at = task['review_attention_at'] if task['review_attention'] else now
        self._update(db, task, phase='held', reason=reason, review_attention=reason, review_attention_at=attention_at)

    def _schedule_review(self, db, task, attempt, classification, detail, now):
        if task['review_attention'] or attempt['retry_decision'] in {'retry', 'attention'}:
            return
        if task['review_attempts'] >= REVIEW_MAX_ATTEMPTS:
            self._review_attention(db, task, attempt, classification,
                                   f'Readiness exhausted all {REVIEW_MAX_ATTEMPTS} automatic attempts. ' + detail, now)
            return
        remaining = REVIEW_MAX_ATTEMPTS-task['review_attempts']
        delay = REVIEW_RETRY_DELAYS[max(0, task['review_attempts']-1)]
        reason = f'{detail} Automatic review retry in {delay//60} minutes; {remaining} attempt(s) remaining in this submission.'
        db.execute("UPDATE attempts SET failure_class=?,retry_decision='retry',retry_kind='review',retry_at=?,review_detail=? WHERE attempt_id=?",
                   (classification, now+delay, reason, attempt['attempt_id']))
        self._update(db, task, phase='held', reason=reason)

    def _review_failure(self, db, task, attempt, diagnostic, now):
        from common.diagnostics import failure_summary
        classification = review_failure_class(diagnostic)
        detail = failure_summary(diagnostic) if diagnostic else 'Readiness execution failed without verified temporary-failure evidence.'
        if classification.startswith('temporary_'):
            self._schedule_review(db, task, attempt, classification, detail, now)
        elif not task['review_attention']:
            self._review_attention(db, task, attempt, classification, detail, now)

    def _resubmit(self, db, task, command, now):
        if (not re.fullmatch('[0-9a-f]{64}', command.submission_id)
                or type(command.submitted_at) not in (float, int) or not math.isfinite(command.submitted_at)
                or command.submitted_at > now or command.submitted_at <= max(
                    task['review_submitted_at'], task['review_attention_at'], task['pending_submission_at'])):
            return
        if command.transition and (set(command.transition) != {'actor_id', 'from_state', 'to_state', 'changed_at'}
                or command.transition['changed_at'] != command.submitted_at
                or command.submission_id != identity(task['issue_id'], command.transition)):
            raise Rejected('Invalid human submission identity')
        from .linear_state import intent
        latest = intent(db, task['issue_id'])
        if (not task['review_attention'] or not task['eligible'] or task['canceled']
                or latest and (latest['conflicted'] or latest['withdrawn'] or latest['changed_at'] > command.submitted_at
                              or command.transition and latest['action_id'] != command.submission_id)):
            return
        if not db.execute('INSERT OR IGNORE INTO review_submissions VALUES (?,?,?,?)',
                          (command.submission_id, task['task_id'], command.submitted_at,
                           json.dumps(command.transition, sort_keys=True))).rowcount:
            return
        self._update(db, task, pending_submission_at=command.submitted_at)
        self._accept_resubmission(db, self._task(db, task['task_id']))

    def _accept_resubmission(self, db, task):
        from .linear_state import human_withdrawal
        if (not task['pending_submission_at'] or not task['eligible'] or task['canceled']
                or human_withdrawal(db, task)
                or db.execute('SELECT 1 FROM grants WHERE task_id=?', (task['task_id'],)).fetchone()):
            return
        self._update(db, task, phase='queued', reason='Human resubmitted for a new bounded readiness review.',
                     review_cycle=task['review_cycle']+1, review_attempts=0, review_attention='', review_attention_at=0,
                     review_submitted_at=task['pending_submission_at'], pending_submission_at=0,
                     sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM tasks').fetchone()[0])

    def _external(self, db, task, attempt, command, now):
        if (attempt["run_id"] and (attempt["run_id"], attempt["run_attempt"]) != (command.run_id, command.run_attempt)):
            raise Rejected("Observation belongs to another run attempt")
        if command.pull_state not in {"", "open", "merged", "closed", "absent"}:
            raise Rejected("Invalid publication observation")
        if attempt["stopped"] and not command.stopped:
            raise Rejected("Stale running observation cannot replace terminal evidence")
        db.execute("UPDATE attempts SET run_id=?,run_attempt=?,stopped=?,all_stopped=?,publication_checked=?,"
                   "conclusion=?,pull_request=?,pull_state=? WHERE attempt_id=?",
                   (command.run_id, command.run_attempt, command.stopped, command.all_attempts_stopped,
                    command.publication_checked, command.conclusion, command.pull_request or attempt["pull_request"],
                    command.pull_state, command.attempt_id))
        verified = command.stopped and command.all_attempts_stopped and command.publication_checked
        if not verified:
            if command.stopped and not task['review_attention'] and not attempt['retry_kind']:
                self._update(db, task, phase="held", reason=command.reason or "Waiting for complete stop and publication evidence.")
            return
        self._release_stopped_credentials(db, task)
        no_changes = command.pull_state == "absent" and attempt["agent_outcome"] == "completed" and command.conclusion == "success"
        if command.pull_state == 'merged' or no_changes or task['canceled'] and command.pull_state in {'absent', 'closed'}:
            phase = 'completed' if command.pull_state == 'merged' or no_changes else 'canceled'
            self._update(db, task, phase=phase, reason=command.reason or (
                'Pull request merged.' if command.pull_state == 'merged' else 'Verified task outcome.'))
            db.execute("UPDATE attempts SET retry_kind='',retry_at=0 WHERE attempt_id=?", (command.attempt_id,))
            self._release_grant(db, task)
            return
        if (not task['review_attention'] and task["eligible"] and not task["dependencies_ready"]
                and dependency_admission_applies(attempt) and command.pull_state == "absent"):
            # A prerequisite wait before assessment is not a failed review.
            session = db.execute("SELECT agent_started FROM sessions WHERE attempt_id=? AND phase='review'",
                                 (command.attempt_id,)).fetchone()
            if attempt['review_charged'] and not attempt['review_result'] and (not session or not session[0]):
                db.execute('UPDATE tasks SET review_attempts=review_attempts-1 WHERE task_id=?', (task['task_id'],))
                db.execute('UPDATE attempts SET review_charged=0 WHERE attempt_id=?', (command.attempt_id,))
            self._update(db, task, phase="queued", reason="Waiting for unresolved prerequisites.")
            self._release_grant(db, task)
            return
        result = json.loads(attempt['review_result'] or 'null')
        if (not task['review_attention'] and not attempt['coding_started'] and not attempt['retry_kind']
                and attempt['agent_outcome'] != 'completed'):
            # A failed job alone is not a temporary service failure. GitHub's
            # timed_out conclusion is structured execution evidence even if the
            # worker never reached startup or could not return its callback.
            session = db.execute("SELECT data FROM sessions WHERE attempt_id=? ORDER BY phase LIMIT 1",
                                 (command.attempt_id,)).fetchone()
            diagnostic = json.loads(session[0]).get('failure', {}) if session else {}
            if not diagnostic and command.conclusion == 'timed_out':
                diagnostic = {'operation': 'worker/readiness', 'kind': 'timeout'}
            if command.conclusion != 'success' or not result:
                self._review_failure(db, task, attempt, diagnostic, now)
            elif task['eligible']:
                self._review_attention(db, task, attempt, 'execution_failed',
                                       'The approved run stopped before coding produced an outcome; inspect the run before resubmitting.', now)
        task = self._task(db, task['task_id'])
        attempt = self._attempt(db, task, command.attempt_id)
        if task['review_attention']:
            if command.pull_state in {'absent', 'closed'}:
                self._release_grant(db, task)
                self._accept_resubmission(db, self._task(db, task['task_id']))
            return  # An unresolved PR always retains ownership and attention.
        if command.pull_state == 'open' and not command.repairable:
            self._update(db, task, phase='published', reason=command.reason or 'Waiting for PR checks and merge.')
        elif command.repairable and not task['repair_used']:
            if task['review_attempts'] >= REVIEW_MAX_ATTEMPTS:
                self._review_attention(db, task, attempt, 'budget_exhausted',
                                       'Readiness budget is exhausted; coding repair requires another assessment.', now)
            else:
                db.execute("UPDATE attempts SET retry_kind='repair',retry_at=? WHERE attempt_id=?", (now, command.attempt_id))
                self._update(db, task, phase='held', reason=command.reason or 'One coding repair is authorized.')
        elif attempt['retry_kind']:
            self._update(db, task, phase='held', reason=task['reason'])
        else:
            self._update(db, task, phase='held', reason=command.reason or 'GitHub task failed; execution is held.')

    def _release_stopped_credentials(self, db, task):
        # The caller has verified all external attempts stopped. Clear only their
        # exact fenced owners, retaining refreshed auth and unrelated leases.
        for attempt in db.execute('SELECT * FROM attempts WHERE task_id=?', (task['task_id'],)).fetchall():
            for phase in ('review', 'coding'):
                owner = f"{attempt['attempt_id']}:{attempt['run_id']}:{attempt['run_attempt']}:{phase}"
                db.execute("UPDATE accounts SET owner='',fence='',lease_until=0,version=version+1 WHERE owner=?", (owner,))
                session = db.execute('SELECT data FROM sessions WHERE attempt_id=? AND phase=?',
                                     (attempt['attempt_id'], phase)).fetchone()
                if session:
                    data = {**json.loads(session[0]), 'account_reserved': False, 'probe_account_id': ''}
                    db.execute('UPDATE sessions SET finished=1,data=? WHERE attempt_id=? AND phase=?',
                               (json.dumps(data), attempt['attempt_id'], phase))

    def _release_grant(self, db, task):
        if db.execute('DELETE FROM grants WHERE task_id=?', (task['task_id'],)).rowcount:
            self._queue_reports(db, task['repository'])

    def _retry(self, db, task, command, now):
        attempt = self._attempt(db, task, task["current_attempt"])
        if not (attempt["stopped"] and attempt["all_stopped"] and attempt["publication_checked"]):
            raise Rejected("Previous external attempts/publication are not verified")
        if (command.kind not in {"startup", "capacity", "context", "repair", "review"} or command.kind != attempt["retry_kind"]
                or attempt["retry_at"] > now or attempt["pull_state"] == "merged"):
            raise Rejected("Retry is not authorized by the recorded task policy")
        if task['review_attention'] or task['review_attempts'] >= REVIEW_MAX_ATTEMPTS:
            self._review_attention(db, task, attempt, 'budget_exhausted', 'Readiness automatic attempt budget is exhausted.', now)
            if attempt['pull_state'] in {'absent', 'closed'}:
                self._release_grant(db, task)
            return
        if command.kind in {'startup', 'context', 'review'} and (attempt['retry_decision'] != 'retry'
                or not attempt['failure_class'].startswith('temporary_')):
            raise Rejected('No verified temporary execution failure authorizes this retry')
        if command.kind == "repair":
            if task["repair_used"]:
                raise Rejected("The repair budget is exhausted")
            self._update(db, task, repair_used=1)
        kind="repair" if attempt["attempt_kind"]=="repair" else command.kind
        self._new_attempt(db, task, command.spec, now, kind=kind)

    def _queue_reports(self,db,repository):
        rows=db.execute("SELECT task_id FROM tasks WHERE repository=? AND phase='queued' AND eligible=1",(repository,)).fetchall()
        for row in rows:
            db.execute('UPDATE tasks SET revision=revision+1 WHERE task_id=?',(row[0],))
            self._report(db,row[0])

    def _report(self, db, task_id):
        from .linear_state import ensure_projection
        ensure_projection(db, self._task(db, task_id))
        revision = db.execute("SELECT revision FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO effects(effect_id,task_id,kind,payload) VALUES(?,?,?,?)",
                   (identity(task_id, "report", revision), task_id, "report", json.dumps({"revision": revision})))

    def _view(self, db, task_id):
        row = self._task(db, task_id)
        attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (row["current_attempt"],)).fetchone()
        sessions = tuple({**dict(s), "data": json.loads(s["data"])} for s in db.execute("SELECT * FROM sessions WHERE attempt_id=? ORDER BY phase", (row["current_attempt"],)))
        submission = db.execute('SELECT transition FROM review_submissions WHERE task_id=? AND submitted_at=?',
                                (task_id, row['pending_submission_at'] or row['review_submitted_at'])).fetchone()
        return TaskView(task_id, row["issue_id"], row["project_id"], row["repository"], row["identifier"], Phase(row["phase"]),
                        row["revision"], bool(db.execute("SELECT 1 FROM grants WHERE task_id=?", (task_id,)).fetchone()),
                        bool(row["eligible"]), bool(row["dependencies_ready"]), row["reason"],
                        TaskSpec(**json.loads(row["spec"])) if row["spec"] else None, dict(attempt) if attempt else {}, sessions, bool(row["canceled"]), row["content_revision"],
                        row["review_cycle"], row["review_attempts"], row["review_attention"], bool(row['pending_submission_at']), row['generation'],
                        row['review_attention_at'], row['review_submitted_at'], json.loads(submission[0]) if submission else {})
