"""Durable priority admission with FIFO ties and non-expiring repository slots.

Queue entries and their sequence/slot live in one Azure Table partition. ETags
fence admission, worker starts, and completion observations against each other.
Neither a worker heartbeat nor a wall-clock lease owns the repository slot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from functools import lru_cache
from uuid import uuid4

from dispatch_core import DispatchError, Route, dependency_status, eligibility
from linear_sync import comment_id, failed_required_checks, pull_outcome
from auto_repair import AutoRepair, RECEIPT_FIELDS
from selection_reporting import SelectionReporter, selection_changed
from routing_timing import record, timestamps
from worker_core import WorkerError
from worker_reporting import is_auto_repair


TERMINAL = {"completed", "abandoned", "skipped"}


def dispatch_key(registry, route, issue):
    return hashlib.sha256(f"{registry.generation}:{route.project_id}:{issue['id']}".encode()).hexdigest()


def optional(table, key):
    try:
        return table.get(key)
    except WorkerError as exc:
        if exc.status != 404:
            raise
        return None, None


class RepositoryQueue:
    def __init__(self, registry, receipts, runs, tables, linear, github, readiness, clock=time.time, control=None):
        self.registry, self.receipts, self.runs = registry, receipts, runs
        self.tables = lru_cache(maxsize=None)(tables)
        self.linear, self.github, self.clock = linear, github, clock
        self.readiness = readiness
        self.selection = SelectionReporter(linear, self.selection_tables, clock=clock)
        self.control = control
        self.repair = AutoRepair(self)
        self.readiness.admitted = self.is_admitted
        self.readiness.admission_reason = self.admission_reason
        self.readiness.deployment_paused = self.deployment_paused
        self.readiness.review_selection = self.review_selection

    optional_entry = staticmethod(optional)

    def optional_run(self, key):
        return optional(self.runs, key)

    def approval(self, issue, route, row, *, images=None):
        if row.get("is_auto_repair") and row.get("repair_parent_key"):
            return self.repair.approval(issue, route, row)
        return (self.readiness.require(issue, route, images=images) if images is not None
                else self.readiness.require(issue, route))

    def review_selection(self, issue, route, rejected):
        """Clear rejected work's marker while its runner drains under the slot fence."""
        table = self.table(route.repository)
        key = dispatch_key(self.registry, route, issue)
        for _ in range(32):
            state, etag = table.get("slot")
            if state["active_key"] != key:
                return
            row, version = table.get(key)
            if bool(row.get("readiness_rejected")) == rejected:
                self.selection.sync(table, key)
                return
            row["readiness_rejected"] = rejected
            selection_changed(row)
            try:
                table.transaction([(state, etag), (row, version)])
            except WorkerError as exc:
                if exc.status != 409:
                    raise
                continue
            self.selection.sync(table, key)
            return
        raise WorkerError("Review selection changed concurrently; retry")

    def is_admitted(self, issue, route):
        return self.admission_reason(issue, route) is None

    def deployment_paused(self):
        return bool(self.control and self.control.is_paused())

    def admission_reason(self, issue, route, *, allow_paused=False):
        table = self.table(route.repository)
        state, _ = table.get("slot")
        key = dispatch_key(self.registry, route, issue)
        if state["active_key"] != key:
            return "repository_admission_lost"
        if not allow_paused and self.deployment_paused():
            return "deployment_paused"
        if not self.dependencies_ready(issue, route):
            return "prerequisites_unresolved"
        # Readiness timer retries can dispatch without calling queue.advance.
        # Adopt legacy slots and retry their marker before that dispatch too.
        state, _ = self.reserve(table)
        return None if state["active_key"] == key else "repository_admission_lost"

    def started_coding(self, row):
        if row.get("coding_started"):
            return True
        run, _ = optional(self.runs, row["RowKey"])
        # Adopt work started before this gate existed. A completed outcome also
        # crossed the coding boundary, including validated no-change work.
        return bool(run and (run.get("status") == "completed"
                             or run.get("stage") in {"coding", "testing", "publishing", "review"}))

    @staticmethod
    def unverified_dependencies(row):
        reason = row.get("dependency_reason", "")
        if not reason.startswith("Unable to verify"):
            known = json.loads(row.get("dependency_ids", "[]"))
            detail = " Last observed: " + reason if reason else ""
            if not detail and known:
                detail = " Previously observed prerequisite IDs: " + ", ".join(known) + "."
            reason = "Unable to verify current blocked-by prerequisites; waiting for a successful Linear read." + detail
        return {"dependency_ids": row.get("dependency_ids", "[]"), "dependency_reason": reason}

    def dependency_fields(self, row):
        try:
            ids, reason = dependency_status(self.linear.issue_dependencies(row["issue_id"]))
            return {"dependency_ids": ids, "dependency_reason": reason}
        except Exception:
            logging.error("Issue dependency lookup deferred for %s", row["issue_id"])
            return self.unverified_dependencies(row)

    def dependencies_ready(self, issue, route, *, owner=None, coding=False, key=None):
        """Fresh broker boundary check, fenced against release and coding start."""
        table = self.table(route.repository)
        key = key or dispatch_key(self.registry, route, issue)
        for _ in range(32):
            state, etag = table.get("slot")
            row, version = table.get(key)
            if state["active_key"] != key or (owner is not None and state["owner"] != owner):
                return False
            if self.started_coding(row):
                return True  # Added dependencies do not interrupt coding already started.
            fields = self.dependency_fields(row)
            updated = {**row, **fields}
            if fields["dependency_reason"]:
                # Fence a concurrent coding callback before persisting the
                # hold. Only a fresh dispatch clears this; its epoch lets a
                # crash before receipt creation resume without losing the retry.
                if not row.get("dependency_hold"):
                    updated["dependency_epoch"] = str(uuid4())
                updated["dependency_hold"] = True
            receipt, _ = optional(self.receipts, key) if owner is not None else (None, None)
            held_attempt = owner is not None and (updated.get("dependency_hold") or (
                receipt and updated.get("dependency_epoch", "") != receipt.get("dependency_epoch", "")))
            if coding and not fields["dependency_reason"] and not held_attempt:
                if self.deployment_paused():
                    raise WorkerError("Azure deployment paused coding admission", 503, reason_code="deployment_paused")
                updated["coding_started"] = True
            try:
                if updated != row:
                    table.transaction([(state, etag), (updated, version)])
            except WorkerError as exc:
                if exc.status != 409:
                    raise
                continue
            if fields["dependency_reason"] or held_attempt:
                self.record_dependency_wait(updated)
                self.report(table, updated, fields["dependency_reason"] or
                            "Prerequisites changed before coding; waiting for a fresh admitted dispatch.")
                return False
            return True
        raise WorkerError("Issue dependencies changed concurrently; retry")

    def record_dependency_wait(self, held):
        """Keep a pre-coding failure retryable, scoped to its existing run."""
        for _ in range(32):
            row, _ = self.table(held["repository"]).get(held["RowKey"])
            if not row.get("dependency_hold") or row.get("dependency_epoch") != held.get("dependency_epoch"):
                return
            run, etag = optional(self.runs, held["RowKey"])
            if (not run or run.get("dependency_wait") or run.get("status") == "completed"
                    or run.get("stage", "initializing") not in {"initializing", "selecting_account"}):
                return
            run["dependency_wait"] = True
            try:
                self.runs.replace(run, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Dependency-waiting worker changed concurrently; retry")

    def prework_stopped(self, state, row, route, receipt):
        """A dependency hold never treats a lease expiry as a stopped runner."""
        if not self.readiness.prework_stopped(row["issue_id"], route):
            return False
        if receipt:
            runs = self.github.dispatch_runs(route, row["RowKey"])
            if any(run.get("status") != "completed" for run in runs):
                return False
            if not runs and receipt["status"] != "rejected":
                return False  # An unconfirmed dispatch may still arrive.
            run, _ = optional(self.runs, row["RowKey"])
            owners = {state["owner"], (run or {}).get("owner", "")} - {""}
            if any(self.github.worker_result(route.repository, owner).get("status") != "completed"
                   for owner in owners):
                return False
            if self.github.worker_pull(route, receipt, row["RowKey"], (run or {}).get("pull_request", "")):
                return False  # A legacy/unexpected publication still owns this slot.
        return True

    def defer_dependencies(self, table, state, etag, row, route, receipt):
        reason = row["dependency_reason"]
        released = (not row.get("repair_parent_key") and self.prework_stopped(state, row, route, receipt)
                    and self.finish_entry(table, state, etag, row, "queued", reason))
        if row.get("repair_parent_key"):
            reason += " The repair retains the repository slot until the parent's failed work is resolved."
        elif not released:
            reason += " Waiting for the previously dispatched worker to stop before releasing the repository slot."
        self.report(table, row, reason)
        return {"status": "dependency_waiting", "dispatch_key": row["RowKey"],
                "reason": reason, "released": released}

    def table(self, repository):
        # GitHub names are case insensitive, including across project mappings.
        table = self.tables(repository.lower())
        state, _ = optional(table, "slot")
        if state is None:
            table.create({"RowKey": "slot", "active_key": "", "owner": "", "sequence": 0,
                          "initialized": False})
            state, _ = table.get("slot")
        if not state["initialized"]:
            # Import pre-queue receipts before admitting new work. Even old,
            # ambiguous dispatches must be checked for live runs and open PRs.
            receipts = sorted((r for r in self.receipts.list()
                               if r["repository"].lower() == repository.lower()),
                              key=lambda r: (r.get("created_at", 0), r["RowKey"]))
            for receipt in receipts:
                self.insert(table, receipt["RowKey"], receipt, submitted=True)
            state, etag = table.get("slot")
            state["initialized"] = True
            table.replace(state, etag)
        return table

    def insert(self, table, key, fields, *, submitted=False):
        # The counter and entry change together: no lost queue entries or
        # duplicate sequence numbers after contention or a dispatcher crash.
        priority = fields.get("priority") or 0
        for _ in range(32):
            state, etag = table.get("slot")
            existing, version = optional(table, key)
            if existing and (existing["status"] != "skipped" or submitted):
                if submitted or existing["status"] != "queued" or existing.get("priority") == priority:
                    return key
                # Priority edits retain the original FIFO position and label
                # intent. Touch the slot too, fencing a concurrent head choice.
                row = {**existing, "priority": priority}
            else:
                sequence = state["sequence"] + 1
                row = {"RowKey": key, "sequence": sequence, "status": "submitted" if submitted else "queued",
                       "priority": priority, "queued_at": self.clock(), **timestamps(fields),
                       **{k: fields[k] for k in ("issue_id", "identifier", "repository", "project_id")}}
                row.update({name: fields[name] for name in RECEIPT_FIELDS if name in fields})
                if is_auto_repair(fields):
                    row["is_auto_repair"] = True
                if existing:
                    # Rejoining a skipped task must not lose a pending label removal.
                    row.update({k: v for k, v in existing.items() if k.startswith("selection_")})
                if not submitted:
                    selection_changed(row)
                state["sequence"] = sequence
            try:
                table.transaction([(state, etag), (row, version)])
                return key
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Repository queue is busy; retry admission")

    def enqueue(self, issue, route, *, timing=None):
        key = dispatch_key(self.registry, route, issue)
        for _ in range(32):
            try:
                self.insert(self.table(route.repository), key,
                            {"issue_id": issue["id"], "identifier": issue["identifier"],
                             "priority": issue.get("priority", 0),
                             "repository": route.repository, "project_id": route.project_id,
                             "is_auto_repair": is_auto_repair(issue),
                             **timestamps(timing or {})})
                return key
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Repository queue is busy; retry admission")

    def head(self, table):
        rows = [r for r in table.list() if r["RowKey"] != "slot" and r["status"] not in TERMINAL
                and (r["status"] != "queued" or ("dependency_ids" in r and not r.get("dependency_reason")))]
        # Imported submissions have already crossed the dispatch boundary and
        # take precedence over new Todo work, regardless of their Linear state.
        # Linear uses 1..4 for Urgent..Low and 0 for No priority (sorted last).
        return min(rows, key=lambda r: (r["status"] == "queued",
                                       (r.get("priority") or 5) if r["status"] == "queued" else 0,
                                       r["sequence"])) if rows else None

    def reserve(self, table):
        state, etag = table.get("slot")
        if not state["active_key"]:
            # Refresh before choosing, including a handoff in this same advance
            # and legacy rows without priority. An occupied slot never preempts.
            self.refresh_waiting(table)
            state, etag = table.get("slot")
        if not state["active_key"]:
            head = self.head(table)
            if not head:
                return state, etag
            row, version = table.get(head["RowKey"])
            row["status"] = "active"
            row["selected_at"] = self.clock()
            selection_changed(row)
            state.update(active_key=row["RowKey"], owner="")
            table.transaction([(state, etag), (row, version)])
            record("repository_admitted", at=self.clock(), dispatch_key=row["RowKey"],
                   issue_id=row["issue_id"], repository=row["repository"], **timestamps(row))
        else:
            row, version = table.get(state["active_key"])
            if not row.get("selection_version"):
                # Adopt slots created by a broker predating selection reporting.
                selection_changed(row)
                table.transaction([(state, etag), (row, version)])
        # Report before readiness or development dispatch, including recovery
        # after a reservation interrupted before its dispatch receipt existed.
        self.selection.sync(table, state["active_key"])
        return table.get("slot")

    def assert_worker(self, repository, key, owner):
        state, _ = self.table(repository).get("slot")
        if state["active_key"] != key or state["owner"] != owner:
            raise WorkerError("Worker no longer owns the repository slot", reason_code="repository_admission_lost")

    def start_worker(self, repository, key, owner):
        table = self.table(repository)
        state, etag = self.reserve(table)
        if state["active_key"] != key:
            raise WorkerError("Another task owns the repository slot", reason_code="repository_admission_lost")
        row, _ = table.get(key)
        if row.get("repair_attempted") and row.get("repair_issue_id"):
            raise WorkerError("The parent worker yielded its slot to its single auto-repair", 403)
        existing, _ = optional(self.runs, key)
        previous = state["owner"] or (existing or {}).get("owner", "")
        if previous and previous != owner:
            result = self.github.worker_result(repository, previous)
            if result.get("status") != "completed":
                raise WorkerError("Previous GitHub worker has not stopped")
        # Touch even an idempotent start. A completion observation that began
        # before this start must not release this slot afterwards.
        state["owner"] = owner
        table.replace(state, etag)

    def current_route(self, row, receipt):
        route = self.registry.routes.get(row["project_id"])
        if receipt and receipt.get("workflow") and receipt.get("ref") and (
                not route or route.repository.lower() != row["repository"].lower()
                or route.workflow != receipt["workflow"] or route.ref != receipt["ref"]):
            # Resolve active work against its actual dispatch destination even
            # after a mapping is removed or changed. Empty ready states prevent
            # an old quota wait from dispatching through this recovery route.
            return Route(row["project_id"], row["repository"], receipt["workflow"], receipt["ref"], (),
                         auto_merge=receipt.get("auto_merge", True))
        if not route or route.repository.lower() != row["repository"].lower():
            return None
        return route

    def observe(self, row, receipt, route, owner):
        """Return an action only from authoritative, complete remote observations."""
        key = row["RowKey"]
        issue = self.linear.issue(row["issue_id"])
        canceled = (issue or {}).get("state", {}).get("type") == "canceled"
        runs = self.github.dispatch_runs(route, key)
        if any(run.get("status") != "completed" for run in runs):
            return "waiting", "Waiting for the previous GitHub worker to stop."
        run, _ = optional(self.runs, key)
        known_owner = owner or (run or {}).get("owner", "")
        known_result = self.github.worker_result(route.repository, known_owner) if known_owner else None
        if known_result and known_result.get("status") != "completed":
            # Listings can lag a newly started run. Its recorded identity must
            # also be stopped, even when cancellation would otherwise release it.
            return "waiting", "Waiting for the recorded GitHub worker to stop."
        if route.workflow == "clusterfork-pilot.yml" and runs:
            # Routing-only receipts have no worker callbacks or publications.
            if all(r.get("conclusion") == "success" for r in runs):
                return "completed", "Routing pilot completed successfully."
            if canceled:
                return "abandoned", "Routing pilot stopped and its Linear issue was canceled."
            return "blocked", "Routing pilot failed. Rerun it or cancel its Linear issue."
        # Always look up the branch, including after a lost finish callback or
        # an outcome claiming no changes. An unresolved PR retains the slot.
        pull = self.github.worker_pull(route, receipt, key, (run or {}).get("pull_request", ""))
        repairable = (not canceled and not is_auto_repair(issue, row) and not is_auto_repair(issue, receipt)
                      and not row.get("repair_attempted") and not receipt.get("repair_attempted")
                      and not row.get("repair_issue_id") and not receipt.get("repair_issue_id"))
        latest = max(runs, key=lambda r: (r["id"], r["run_attempt"])) if runs else known_result
        latest_owner = f"{latest['id']}:{latest['run_attempt']}" if runs else known_owner
        if (receipt.get("readiness_wait_owner") == latest_owner
                or receipt.get("deployment_wait_owner") == latest_owner
                or (run and (run.get("dependency_wait") or run.get("status") == "waiting_for_dependencies"))):
            repairable = False  # Admission holds have not attempted coding/validation.
        failed_run = bool((run and run.get("status") == "failed" and run.get("owner") == latest_owner)
                          or (latest and latest.get("conclusion") == "failure"))
        if pull:
            outcome, reason = pull_outcome(pull, route.auto_merge)
            if pull.get("merged"):
                return "completed", reason
            if pull.get("state") == "OPEN":
                if repairable and (failed_run or failed_required_checks(pull)):
                    return "repair", reason if not failed_run else "Worker execution or validation failed; its PR remains open."
                if not run or run["status"] != "completed":
                    return "blocked", "Worker did not complete successfully; its pull request remains open. " + reason
                return "blocked" if outcome == "blocked" else "waiting", reason
            if not canceled:
                return "blocked", reason + " Rerun this task or cancel its Linear issue to abandon it."
        if canceled:
            return "abandoned", "Linear issue canceled; GitHub workers stopped and no open PR remains."
        if receipt.get("deployment_wait_owner"):
            # A startup deferral did no coding and cannot trigger auto-repair.
            # The recorded run is checked directly even if listings lag.
            deferred = self.github.worker_result(route.repository, receipt["deployment_wait_owner"], latest=True)
            if deferred.get("status") != "completed":
                return "waiting", "Waiting for the deployment-deferred GitHub attempt to stop."
            current, reason = eligibility(issue or {}, self.registry)
            if current != route:
                return "skipped", f"Deployment-deferred issue is no longer eligible ({reason})."
            return "retry", "Deployment resumed; rechecking current readiness before retrying startup."
        if repairable and failed_run and (runs or known_result):
            return "repair", "Worker execution or validation failed."
        if receipt["status"] == "rejected":
            if receipt.get("retry_after", 0) <= self.clock():
                return "retry", "Retrying a rejected dispatch."
            return "waiting", "Waiting to retry a rejected dispatch."
        if not runs:
            return "blocked", "Dispatch has no confirmed GitHub run; it may still arrive. Resolve or cancel this task."
        # A newer manual rerun that never reached the broker cannot inherit an
        # earlier attempt's successful or quota-wait outcome.
        owners = {f"{r['id']}:{r['run_attempt']}" for r in runs}
        if receipt.get("readiness_wait_owner") in owners:
            current, reason = eligibility(issue or {}, self.registry)
            if current != route:
                return "skipped", f"Readiness-waiting issue is no longer eligible ({reason})."
            if receipt["retry_after"] <= self.clock():
                return "retry", "Rechecking readiness before retrying coding admission."
            return "waiting", "Coding start held for a current passing readiness review; awaiting retry."
        if not run or run.get("owner") not in owners:
            return "blocked", "GitHub worker ended without recording a result. Rerun this task or cancel its Linear issue."
        latest = next(r for r in runs if f"{r['id']}:{r['run_attempt']}" == run["owner"])
        if run["status"] == "completed" and latest.get("conclusion") == "success":
            return "completed", "Validated work completed without repository changes."
        if run["status"] == "waiting_for_quota" and latest.get("conclusion") == "success":
            extra = (route.linear_states.in_progress, route.linear_states.blocked) if row.get("repair_parent_key") and route.linear_states else ()
            current, reason = eligibility(issue or {}, self.registry, additional_state_ids=extra)
            if current != route:
                return "skipped", f"Quota-waiting issue is no longer eligible ({reason})."
            if receipt.get("retry_after", 0) and receipt["retry_after"] <= self.clock():
                return "retry", "Retrying the head issue after its quota wait."
            return "waiting", "Waiting for account quota; the head issue retains its queue position."
        return "blocked", "Worker failed, was canceled, or lost its result. Rerun this task or cancel its Linear issue."

    def finish_entry(self, table, state, etag, row, status, reason):
        if state["owner"] and self.github.worker_result(row["repository"], state["owner"]).get("status") != "completed":
            return False  # A read-only reviewer also owns the slot until its job stops.
        current, version = table.get(row["RowKey"])
        current.update(status=status, reason=reason)
        selection_changed(current)
        state.update(active_key="", owner="")
        table.transaction([(state, etag), (current, version)])
        # The next head's addition takes priority over historical removals and
        # comments. advance repairs all durable intents after reporting it.
        return True

    def dispatch(self, table, state, etag, row, route, *, retry=False):
        key = row["RowKey"]
        issue = self.linear.issue(row["issue_id"])
        generated = row.get("is_auto_repair") and row.get("repair_parent_key")
        extra = (route.linear_states.in_progress, route.linear_states.blocked) if generated and route.linear_states else ()
        current, reason = eligibility(issue or {}, self.registry, additional_state_ids=extra)
        if current != route:
            message = f"No longer eligible ({reason})."
            if generated:
                # Skipping a repair cannot abandon its parent's broken branch.
                self.report(table, row, message)
                return {"status": "blocked", "dispatch_key": key, "reason": message}
            if not self.finish_entry(table, state, etag, row, "skipped", message):
                return {"status": "waiting", "dispatch_key": key, "reason": "Waiting for the readiness worker to stop."}
            return {"status": "skipped", "dispatch_key": key}
        if not self.dependencies_ready(issue, route, key=key):
            return {"status": "dependency_waiting", "dispatch_key": key}
        review = {"status": "ready"} if generated else self.readiness.ensure(issue, route)
        if review["status"] != "ready":
            if table.get(key)[0].get("dependency_reason"):
                return {"status": "dependency_waiting", "dispatch_key": key}
            return {**review, "dispatch_key": key}
        approval = self.approval(issue, route, row)
        if not self.dependencies_ready(issue, route, key=key):
            return {"status": "dependency_waiting", "dispatch_key": key}
        # Fence observations before crossing the external dispatch boundary.
        # Preserve the review/previous attempt's owner so development startup
        # verifies that it stopped before taking over this same repository slot.
        current_row, row_version = table.get(key)
        current_row["dependency_hold"] = False
        table.transaction([(state, etag), (current_row, row_version)])
        receipt, version = optional(self.receipts, key)
        if receipt:
            dependency_retry = current_row.get("dependency_epoch", "") != receipt.get("dependency_epoch", "")
            if not retry or (not dependency_retry and (
                    not receipt.get("deployment_wait_owner") and (
                        not receipt.get("retry_after") or receipt["retry_after"] > self.clock()))):
                return {"status": "already_claimed", "dispatch_key": key}
            receipt.update(status="dispatching", retry_after=0, readiness_wait_owner="",
                           deployment_wait_owner="",
                           dependency_epoch=current_row.get("dependency_epoch", ""),
                           readiness_review_key=approval.get("review_key", ""))
            self.receipts.replace(receipt, version)
        else:
            receipt = {"RowKey": key, "status": "dispatching", "created_at": int(self.clock()),
                       "workflow": route.workflow, "ref": route.ref, "auto_merge": route.auto_merge,
                       "readiness_review_key": approval.get("review_key", ""),
                       "dependency_epoch": current_row.get("dependency_epoch", ""),
                       **{k: row[k] for k in ("issue_id", "identifier", "repository", "project_id")}}
            receipt.update({name: row[name] for name in RECEIPT_FIELDS if name in row})
            if not self.receipts.create(receipt):
                return {"status": "already_claimed", "dispatch_key": key}
        inputs = {"linear_issue_id": row["issue_id"], "linear_identifier": row["identifier"],
                  "linear_project_id": row["project_id"], "dispatch_key": key}
        try:
            self.github.dispatch(route, inputs)
        except DispatchError as exc:
            receipt, version = self.receipts.get(key)
            receipt.update(status="uncertain" if exc.uncertain else "rejected",
                           retry_after=0 if exc.uncertain else int(self.clock()) + 60)
            self.receipts.replace(receipt, version)
            if not exc.uncertain:
                raise
            return {"status": "uncertain", "dispatch_key": key}
        # Read again: a fast worker may have saved a quota retry meanwhile.
        receipt, version = self.receipts.get(key)
        receipt["status"] = "dispatched"
        self.receipts.replace(receipt, version)
        return {"status": "dispatched", "dispatch_key": key}

    def report(self, table, row, reason):
        if row.get("reported_reason") == reason:
            return
        try:
            self.linear.status_comment(row["issue_id"], comment_id(row["RowKey"], "queue"),
                                       f"Clusterfork repository queue ({row['repository']}): {reason}")
            current, etag = table.get(row["RowKey"])
            current.update(reason=reason, reported_reason=reason)
            table.replace(current, etag)
        except Exception:
            logging.error("Repository queue explanation deferred for %s", row["RowKey"])

    def refresh_waiting_entry(self, table, key):
        for _ in range(32):
            state, etag = table.get("slot")
            row, version = table.get(key)
            if row["status"] != "queued" or state["active_key"] == key:
                return
            original = dict(row)
            try:
                issue = self.linear.issue(row["issue_id"])
                route, reason = eligibility(issue or {}, self.registry)
            except Exception:
                # A failed eligibility read cannot leave a stale dependency pass.
                logging.error("Waiting issue refresh deferred for %s", row["RowKey"])
                row.update(self.unverified_dependencies(row))
                route, reason, issue = None, "unavailable", None
            if reason == "unavailable":
                pass
            elif (not route or route.project_id != row["project_id"]
                    or route.repository.lower() != row["repository"].lower()):
                if route:
                    reason = "project_or_repository_changed"
                row.update(status="skipped", reason=f"No longer eligible for this queue ({reason}).")
                selection_changed(row)
            else:
                row.update(priority=issue.get("priority") or 0, **self.dependency_fields(row))
                if not row.get("selection_version"):
                    selection_changed(row)
            if row == original:
                return
            # Fence priority/dependency edits, removal, and adoption against admission/reentry.
            try:
                table.transaction([(state, etag), (row, version)])
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Waiting issue changed concurrently; retry reconciliation")

    def refresh_waiting(self, table, issue_ids=None):
        for row in table.list():
            if row.get("status") == "queued" and (issue_ids is None or row["issue_id"] in issue_ids):
                try:
                    self.refresh_waiting_entry(table, row["RowKey"])
                except Exception:
                    # Unavailable Linear data retains position and known priority.
                    logging.error("Waiting issue refresh deferred for %s", row["RowKey"])

    def observe_issues(self, issues):
        # A moved issue may no longer point to its original repository. Repair
        # those waiting entries too, without advancing unrelated repositories.
        observed = {issue["id"] for issue in issues}
        if not observed:
            return
        for table in self.selection_tables():
            self.refresh_waiting(table, observed)
            for row in table.list():
                if row.get("issue_id") in observed:
                    self.selection.sync(table, row["RowKey"])

    def advance(self, repository):
        table = None
        try:
            for _ in range(32):
                try:
                    table = self.table(repository)
                    return self._advance(table)
                except WorkerError as exc:
                    if exc.status != 409:
                        raise
            raise WorkerError("Repository queue changed concurrently; retry reconciliation")
        finally:
            if table is not None:
                # Retain repair even when GitHub observation/dispatch fails.
                self.refresh_waiting(table)
                self.selection.reconcile(table)
                self.repair.reconcile(table)
                for row in table.list():
                    if row.get("status") in TERMINAL and row.get("reason"):
                        self.report(table, row, row["reason"])
                    elif row.get("status") == "queued" and row.get("dependency_reason"):
                        self.report(table, row, row["dependency_reason"])

    def _advance(self, table):
        results = {}
        if self.control and self.control.is_paused():
            reason = "Azure deployment in progress; new workers are paused."
            for row in table.list():
                if row["RowKey"] != "slot" and row["status"] not in TERMINAL:
                    self.report(table, row, reason)
                    results[row["RowKey"]] = {"status": "deployment_paused", "dispatch_key": row["RowKey"]}
            return results
        while True:
            state, etag = self.reserve(table)
            key = state["active_key"]
            if not key:
                results.update(self.waiting_results(table))
                return results
            row, _ = table.get(key)
            receipt, _ = optional(self.receipts, key)
            route = self.current_route(row, receipt)
            if route and receipt and row.get("repair_attempted") and row.get("repair_issue_id"):
                reason = "The single automatic repair is being admitted for this repository slot."
                if self.repair.admit(table, state, etag, row, receipt, route, reason):
                    results[key] = {"status": "repairing", "dispatch_key": key}
                    continue
                self.report(table, row, reason)
                results[key] = {"status": "blocked", "dispatch_key": key, "reason": reason}
                return results
            if route is not None and not self.started_coding(row):
                self.dependencies_ready({"id": row["issue_id"]}, route, key=key)
                state, etag = table.get("slot")
                if state["active_key"] != key:
                    raise WorkerError("Repository admission changed during dependency lookup")
                row, _ = table.get(key)
                if row.get("dependency_reason") and not self.started_coding(row):
                    result = self.defer_dependencies(table, state, etag, row, route, receipt)
                    results[key] = result
                    if result["released"]:
                        continue
                    results.update(self.waiting_results(table, row, result["reason"]))
                    return results
            if route is None and not receipt:
                if row.get("repair_parent_key"):
                    reason = "Repair repository mapping changed; restore it to resolve the parent's failed work."
                    self.report(table, row, reason)
                    return {key: {"status": "blocked", "dispatch_key": key, "reason": reason}}
                reason = "Skipped because the project's repository mapping changed."
                if not self.finish_entry(table, state, etag, row, "skipped", reason):
                    return {key: {"status": "waiting", "dispatch_key": key,
                                  "reason": "Waiting for the readiness worker to stop."}}
                results[key] = {"status": "skipped", "dispatch_key": key, "reason": reason}
                continue
            if route is None:
                reason = "Repository mapping changed for a legacy dispatch; restore it to verify its worker and PR."
                results[key] = {"status": "blocked", "dispatch_key": key, "reason": reason}
            elif not receipt:
                result = self.dispatch(table, state, etag, row, route)
                results[key] = result
                if result["status"] in {"skipped", "dependency_waiting"}:
                    continue
                reason = ("Dispatched; this task owns the repository until its run and PR are resolved."
                          if result["status"] in {"dispatched", "uncertain", "already_claimed"}
                          else "Waiting for a current passing readiness review; coding and publication are held.")
            else:
                if (row.get("dependency_epoch", "") != receipt.get("dependency_epoch", "")
                        and not self.started_coding(row)):
                    stopped = self.prework_stopped(state, row, route, receipt)
                    action = "retry" if stopped else "waiting"
                    reason = "Prerequisites completed; waiting for the previous worker to stop before retrying."
                else:
                    action, reason = self.observe(row, receipt, route, state["owner"])
                if action == "repair":
                    if self.repair.admit(table, state, etag, row, receipt, route, reason):
                        results[key] = {"status": "repairing", "dispatch_key": key, "reason": reason}
                        continue
                    action = "blocked"
                if row.get("repair_parent_key") and action in {"completed", "blocked", "abandoned", "skipped"}:
                    finished = self.repair.finish(table, state, etag, row, route, action, reason)
                    if action == "completed" and finished:
                        results[key] = {"status": "completed", "dispatch_key": key, "reason": reason}
                        continue
                    action = "blocked"
                if action in TERMINAL:
                    if not self.finish_entry(table, state, etag, row, action, reason):
                        return {key: {"status": "waiting", "dispatch_key": key,
                                      "reason": "Waiting for the admitted worker to stop."}}
                    results[key] = {"status": action, "dispatch_key": key, "reason": reason}
                    continue
                if action == "retry":
                    result = self.dispatch(table, state, etag, row, route, retry=True)
                    results[key] = result
                    if result["status"] in {"skipped", "dependency_waiting"}:
                        continue
                else:
                    results[key] = {"status": action, "dispatch_key": key, "reason": reason}
            self.report(table, row, reason)
            results.update(self.waiting_results(table, row, reason))
            return results

    def waiting_results(self, table, active=None, reason=""):
        if active is not None:
            # A blocker event can arrive while another task still owns the
            # slot. Report the refreshed relationship/status, not the old hold.
            self.refresh_waiting(table)
        results = {}
        for row in table.list():
            if row.get("status") not in {"queued", "submitted"}:
                continue
            dependency = row.get("dependency_reason", "")
            message = dependency or (f"Queued behind {active['identifier']}. {reason}" if active else "Queued for admission.")
            self.report(table, row, message)
            results[row["RowKey"]] = {"status": "dependency_waiting" if dependency else "queued",
                                      "dispatch_key": row["RowKey"], "reason": message}
        return results

    def dependency_repositories(self, issue_ids):
        """Wake dependents even when the changed prerequisite has no route/project."""
        observed, repositories = set(issue_ids), set()
        for table in self.selection_tables():
            for row in table.list():
                if row.get("status") in TERMINAL or row.get("RowKey") == "slot":
                    continue
                if (row["issue_id"] in observed or "dependency_ids" not in row
                        or row.get("dependency_reason", "").startswith("Unable to verify")
                        or observed.intersection(json.loads(row["dependency_ids"]))):
                    repositories.add(row["repository"].lower())
        return sorted(repositories)

    def repositories(self):
        repositories = {r.repository.lower() for r in self.registry.routes.values()}
        repositories.update(r["repository"].lower() for r in self.receipts.list())
        return sorted(repositories)

    def selection_tables(self):
        # Read existing slots without initializing/importing other repositories.
        for repository in self.repositories():
            yield self.tables(repository)

    def reconcile(self):
        for repository in self.repositories():
            try:
                self.advance(repository)
            except Exception:
                logging.error("Repository queue reconciliation deferred for %s", repository)

    def deployment_pending(self):
        for receipt in self.receipts.list():
            if not receipt.get("deployment_wait_owner"):
                continue
            table = self.table(receipt["repository"])
            state, _ = table.get("slot")
            row, _ = table.get(receipt["RowKey"])
            if state["active_key"] == receipt["RowKey"] and not row.get("dependency_reason"):
                return True
        return False
