"""The only task-state writer. Transports supply facts, never arbitrary phases."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
import re

from .model import (Admit, Assign, DispatchObserved, ExternalObserved, Finish, Hold,
                    IssueObserved, Phase, Progress, Rejected, Retry, Review, Role,
                    Start, TaskSpec, TaskView, identity, SessionUpdated, EffectUpdated, Authorize, ExhaustAccount,
                    ContextObserved, InboxQueued, InboxDone, Initialize)


class Controller:
    def __init__(self, database):
        self.database = database

    def inspect(self, task_id):
        with self.database.transaction() as db:
            return self._view(db, task_id)

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
            if isinstance(command, Initialize):
                if db.execute("INSERT OR IGNORE INTO metadata VALUES ('initialized','1')").rowcount:
                    db.execute("INSERT OR IGNORE INTO holds VALUES ('operator:rebuild','Production admission is held for rebuild acceptance.')")
                return None
            if isinstance(command, InboxQueued):
                if not re.fullmatch(r"[0-9a-f]{64}", command.event_id) or len(json.dumps(command.event)) > 3000:
                    raise Rejected("Invalid event receipt")
                db.execute("INSERT OR IGNORE INTO inbox(event_id,payload) VALUES (?,?)", (command.event_id,json.dumps(command.event)))
                return None
            if isinstance(command, InboxDone):
                db.execute("UPDATE inbox SET done=1 WHERE event_id=?", (command.event_id,))
                return None
            if isinstance(command, Hold):
                if not re.fullmatch(r"[\w:./-]{1,200}", command.owner) or not isinstance(command.reason, str):
                    raise Rejected("Invalid admission hold")
                if command.release:
                    db.execute("DELETE FROM holds WHERE owner=?", (command.owner,))
                else:
                    db.execute("INSERT INTO holds VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET reason=excluded.reason",
                               (command.owner, command.reason))
                return None
            if isinstance(command, IssueObserved):
                self._issue(db, command)
                return self._view(db, command.task_id)
            task = self._task(db, command.task_id)
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
            else:
                attempt = self._attempt(db, task, command.attempt_id)
                if isinstance(command, DispatchObserved):
                    if command.status not in {"accepted", "rejected", "unknown"}:
                        raise Rejected("Invalid dispatch observation")
                    previous = attempt["dispatch_status"]
                    if previous == "accepted" and command.status != "accepted":
                        raise Rejected("Accepted dispatch cannot be replaced with uncertainty")
                    db.execute("UPDATE attempts SET dispatch_status=? WHERE attempt_id=?",
                               (command.status, command.attempt_id))
                    if command.status == "rejected":
                        db.execute("UPDATE attempts SET stopped=1,all_stopped=1,publication_checked=1,"
                                   "pull_state='absent',retry_kind='startup',retry_at=? WHERE attempt_id=?",
                                   (now + 60, command.attempt_id))
                        self._update(db, task, phase="held", reason="GitHub rejected dispatch; startup retry scheduled.")
                elif isinstance(command, Start):
                    self._start(db, task, attempt, command, now)
                elif isinstance(command, Assign):
                    self._assign(db, task, attempt, command, now)
                elif isinstance(command, Review):
                    self._review(db, task, attempt, command, now)
                elif isinstance(command, Progress):
                    self._progress(db, task, attempt, command)
                elif isinstance(command, Finish):
                    self._finish(db, task, attempt, command, now)
                elif isinstance(command, ExternalObserved):
                    self._external(db, task, attempt, command, now)
                elif isinstance(command, SessionUpdated):
                    self._session(db, task, attempt, command)
                elif isinstance(command, Authorize):
                    self._authority(db, task)
                    session = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase=?",
                                         (command.attempt_id, command.role.phase)).fetchone()
                    if (not session or session["finished"] or session["deadline"] <= now
                            or not command.role.selector and not session["agent_started"]):
                        raise Rejected("Execution phase is not active")
                elif isinstance(command, ExhaustAccount):
                    self._exhaust(db, task, attempt, command)
                elif isinstance(command, ContextObserved):
                    if asdict(command.binding) != json.loads(task["spec"]):
                        self._update(db, task, phase="held", reason="Reviewed task context changed; a fresh review is required.")
                        if not attempt["coding_started"] and not attempt["retry_kind"]:
                            db.execute("UPDATE attempts SET retry_kind='context',retry_at=? WHERE attempt_id=?", (now, command.attempt_id))
                else:
                    raise Rejected("Unsupported controller command")
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

    def _issue(self, db, command):
        if (type(command.priority) is not int or command.priority not in range(5)
                or not math.isfinite(command.sort_order) or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", command.repository)
                or type(command.eligible) is not bool or type(command.dependencies_ready) is not bool
                or type(command.canceled) is not bool):
            raise Rejected("Invalid issue observation")
        row = db.execute("SELECT * FROM tasks WHERE task_id=?", (command.task_id,)).fetchone()
        if row is None:
            db.execute("INSERT INTO tasks(task_id,issue_id,project_id,generation,repository,identifier,phase,"
                       "eligible,dependencies_ready,priority,sort_order,content_revision,reason,canceled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (command.task_id, command.issue_id, command.project_id, command.generation,
                        command.repository.lower(), command.identifier, "queued" if command.eligible else "skipped",
                        command.eligible, command.dependencies_ready, command.priority, command.sort_order,
                        command.content_revision, command.reason, command.canceled))
        else:
            task = dict(row)
            changes = dict(eligible=command.eligible, dependencies_ready=command.dependencies_ready,
                           priority=command.priority, sort_order=command.sort_order,
                           content_revision=command.content_revision, canceled=command.canceled)
            active = db.execute("SELECT 1 FROM grants WHERE task_id=?", (command.task_id,)).fetchone()
            if not active and task["phase"] in {"queued", "skipped"}:
                changes.update(phase="queued" if command.eligible else "skipped", reason=command.reason)
            if not active and task["phase"] == "held" and not task["eligible"] and command.eligible:
                changes.update(phase="queued", reason="Resubmitted for a new readiness review.",
                               sequence=db.execute("SELECT MAX(sequence)+1 FROM tasks").fetchone()[0])
            if active and task["spec"]:
                changed = json.loads(task["spec"])["content_revision"] != command.content_revision
                if changed or not command.eligible:
                    changes.update(phase="held", reason="Task content or eligibility changed; execution authorization held.")
                elif not command.dependencies_ready:
                    attempt = db.execute("SELECT coding_started FROM attempts WHERE attempt_id=?", (task["current_attempt"],)).fetchone()
                    if not attempt or not attempt[0]:
                        changes.update(phase="held", reason=command.reason or "Prerequisites are unresolved.")
            if any(task[k] != v for k, v in changes.items()):
                self._update(db, task, **changes)
        self._report(db, command.task_id)

    def _authority(self, db, task, *, coding=False, allow_hold=False):
        if not db.execute("SELECT 1 FROM grants WHERE repository=? AND task_id=?",
                          (task["repository"], task["task_id"])).fetchone():
            raise Rejected("Task does not own repository admission")
        if not task["eligible"] or (task["phase"] == "held" and not allow_hold):
            raise Rejected("Task authorization is held")
        if not task["dependencies_ready"]:
            attempt = db.execute("SELECT coding_started FROM attempts WHERE attempt_id=?", (task["current_attempt"],)).fetchone()
            if not attempt or not attempt[0]:
                raise Rejected("Prerequisites are unresolved")

    def _new_attempt(self, db, task, spec, now, *, initial=False, kind="normal"):
        if not isinstance(spec, TaskSpec) or spec.content_revision != task["content_revision"]:
            raise Rejected("Task specification is stale")
        if not task["eligible"] or not task["dependencies_ready"] or db.execute("SELECT 1 FROM holds LIMIT 1").fetchone():
            raise Rejected("New admissions are held")
        if initial:
            head = db.execute("SELECT task_id FROM tasks WHERE repository=? AND phase='queued' AND eligible=1 "
                              "AND dependencies_ready=1 ORDER BY CASE priority WHEN 0 THEN 5 ELSE priority END,sort_order,sequence LIMIT 1",
                              (task["repository"],)).fetchone()
            if not head or head[0] != task["task_id"]:
                raise Rejected("A higher-priority task owns the queue head")
            if db.execute("SELECT 1 FROM grants WHERE repository=?", (task["repository"],)).fetchone():
                raise Rejected("Repository already has an active task")
            db.execute("INSERT INTO grants VALUES (?,?)", (task["repository"], task["task_id"]))
        else:
            self._authority(db, task, allow_hold=True)
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM attempts WHERE task_id=?", (task["task_id"],)).fetchone()[0]
        attempt_id = identity(task["task_id"], ordinal)
        db.execute("INSERT INTO attempts(attempt_id,task_id,ordinal,dispatch_status,started_at,attempt_kind) VALUES(?,?,?,'intent',?,?)",
                   (attempt_id, task["task_id"], ordinal, now, kind))
        self._update(db, task, phase="reviewing", spec=json.dumps(asdict(spec), sort_keys=True),
                     current_attempt=attempt_id, reason="Waiting for GitHub review preflight.")
        db.execute("INSERT INTO effects(effect_id,task_id,kind,payload) VALUES(?,?,?,?)",
                   (attempt_id, task["task_id"], "dispatch", json.dumps({"attempt_id": attempt_id})))

    def _start(self, db, task, attempt, command, now):
        self._authority(db, task)
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

    def _session(self, db, task, attempt, command):
        allowed = {"thread_id", "turn_id", "steering_closed", "account_email", "account_plan",
                   "usage_before", "usage_after", "usage_segments", "exhausted_account_ids",
                   "account_failover", "probe_account_id", "released_account_id", "heartbeat_until",
                   "repair_validation_passed", "repair_result", "failure", "quota_wait", "wip_branch", "wip_commit_sha",
                   "original_account_id", "steering_started_at"}
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
        self._authority(db, task)
        if not isinstance(command.role, Role):
            raise Rejected("Invalid account role")
        if not re.fullmatch(r"(?:cursor:)?[0-9a-f]{64}", command.account_id):
            raise Rejected("Invalid provider account identity")
        row = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase=?", (command.attempt_id, command.role.phase)).fetchone()
        data = json.loads(row["data"]) if row else {}
        replacement = command.role == Role.CODING and data.get("account_failover") and not row["account_id"]
        if not command.role.selector and not replacement:
            raise Rejected("Only authenticated preflight can assign an account")
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

    def _exhaust(self, db, task, attempt, command):
        self._authority(db, task)
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

    def _review(self, db, task, attempt, command, now):
        from readiness_policy import validate_result, redact_review_value
        self._authority(db, task)
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
        verdict = result["verdict"]
        db.execute("UPDATE attempts SET review_result=? WHERE attempt_id=?", (json.dumps(result), command.attempt_id))
        db.execute("UPDATE sessions SET finished=1 WHERE attempt_id=? AND phase='review'", (command.attempt_id,))
        if verdict == "retry":
            db.execute("UPDATE attempts SET retry_kind='review',retry_at=? WHERE attempt_id=?", (now + 900, command.attempt_id))
        self._update(db, task, phase="ready" if verdict == "ready" else "held", reason=result["summary"])

    def _progress(self, db, task, attempt, command):
        self._authority(db, task, coding=True)
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
            if command.phase == "coding" and (attempt["agent_outcome"], attempt["pull_request"]) == (command.outcome, command.pull_request):
                return
            raise Rejected("This execution phase already has a result")
        if command.pull_request and not re.fullmatch(re.escape("https://github.com/" + task["repository"] + "/pull/") + r"[1-9][0-9]*", command.pull_request, flags=re.I):
            raise Rejected("Invalid publication reference")
        # A callback records a result; it never proves the Actions cleanup stopped.
        db.execute("UPDATE sessions SET finished=1 WHERE attempt_id=? AND phase=?", (command.attempt_id, command.phase))
        if command.phase == "coding":
            db.execute("UPDATE attempts SET agent_outcome=?,pull_request=? WHERE attempt_id=?",
                       (command.outcome, command.pull_request or attempt["pull_request"], command.attempt_id))
        if command.outcome == "waiting_for_quota":
            db.execute("UPDATE attempts SET retry_kind='capacity',retry_at=? WHERE attempt_id=?", (now + 900, command.attempt_id))
        self._update(db, task, phase="published" if command.pull_request else "held",
                     reason=command.reason or ("Waiting for CI and merge." if command.pull_request else "Waiting for verified GitHub completion."))

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
            if command.stopped:
                self._update(db, task, phase="held", reason=command.reason or "Waiting for complete stop and publication evidence.")
            return
        if (task["eligible"] and not task["canceled"] and not task["dependencies_ready"]
                and not attempt["coding_started"] and command.pull_state == "absent"):
            self._update(db, task, phase="queued", reason="Waiting for unresolved prerequisites.")
            db.execute("DELETE FROM grants WHERE task_id=?", (task["task_id"],))
            return
        no_changes = command.pull_state == "absent" and attempt["agent_outcome"] == "completed" and command.conclusion == "success"
        result = json.loads(attempt["review_result"] or "null")
        review_denied = result and result.get("verdict") in {"needs_clarification", "rejected"}
        if command.pull_state == "merged" or no_changes or task["canceled"] and command.pull_state in {"absent","closed"} or review_denied and command.pull_state == "absent":
            phase = "completed" if command.pull_state == "merged" or no_changes else "held" if review_denied else "canceled"
            self._update(db, task, phase=phase, reason=command.reason or ("Pull request merged." if command.pull_state == "merged" else "Verified task outcome."))
            db.execute("DELETE FROM grants WHERE task_id=?", (task["task_id"],))
        elif command.pull_state == "open" and not command.repairable:
            self._update(db, task, phase="published", reason=command.reason or "Waiting for PR checks and merge.")
        else:
            retry = attempt["retry_kind"]
            if not retry and not attempt["coding_started"] and command.pull_state == "absent" and command.conclusion in {"failure", "timed_out"}:
                retry = "startup" if result and result.get("verdict")=="ready" else "review"
            if command.repairable and not task["repair_used"]:
                retry = "repair"
            if retry and not attempt["retry_at"]:
                delay=60 if retry=="startup" else 900 if retry in {"review","capacity"} else 0
                db.execute("UPDATE attempts SET retry_kind=?,retry_at=? WHERE attempt_id=?", (retry, now+delay, command.attempt_id))
            reason = task["reason"] if retry in {"capacity","context"} or retry=="review" and result else (
                "Readiness job failed; retry scheduled after 15 minutes." if retry=="review" else
                "Worker startup failed; retry scheduled after 60 seconds." if retry=="startup" else
                "GitHub task failed; execution is held.")
            self._update(db, task, phase="held", reason=command.reason or reason)

    def _retry(self, db, task, command, now):
        attempt = self._attempt(db, task, task["current_attempt"])
        if not (attempt["stopped"] and attempt["all_stopped"] and attempt["publication_checked"]):
            raise Rejected("Previous external attempts/publication are not verified")
        if (command.kind not in {"startup", "capacity", "context", "repair", "review"} or command.kind != attempt["retry_kind"]
                or attempt["retry_at"] > now or attempt["pull_state"] == "merged"):
            raise Rejected("Retry is not authorized by the recorded task policy")
        if command.kind == "repair":
            if task["repair_used"]:
                raise Rejected("The repair budget is exhausted")
            self._update(db, task, repair_used=1)
        kind="repair" if attempt["attempt_kind"]=="repair" else command.kind
        self._new_attempt(db, task, command.spec, now, kind=kind)

    @staticmethod
    def _report(db, task_id):
        revision = db.execute("SELECT revision FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO effects(effect_id,task_id,kind,payload) VALUES(?,?,?,?)",
                   (identity(task_id, "report", revision), task_id, "report", json.dumps({"revision": revision})))

    def _view(self, db, task_id):
        row = self._task(db, task_id)
        attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (row["current_attempt"],)).fetchone()
        sessions = tuple({**dict(s), "data": json.loads(s["data"])} for s in db.execute("SELECT * FROM sessions WHERE attempt_id=? ORDER BY phase", (row["current_attempt"],)))
        return TaskView(task_id, row["issue_id"], row["project_id"], row["repository"], row["identifier"], Phase(row["phase"]),
                        row["revision"], bool(db.execute("SELECT 1 FROM grants WHERE task_id=?", (task_id,)).fetchone()),
                        bool(row["eligible"]), bool(row["dependencies_ready"]), row["reason"],
                        TaskSpec(**json.loads(row["spec"])) if row["spec"] else None, dict(attempt) if attempt else {}, sessions, bool(row["canceled"]))
