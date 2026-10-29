"""Durable FIFO admission and non-expiring repository slots.

Queue entries and their sequence/slot live in one Azure Table partition. ETags
fence admission, worker starts, and completion observations against each other.
Neither a worker heartbeat nor a wall-clock lease owns the repository slot.
"""

from __future__ import annotations

import hashlib
import logging
import time
from functools import lru_cache

from dispatch_core import DispatchError, Route, eligibility
from linear_sync import comment_id, pull_outcome
from selection_reporting import SelectionReporter, selection_changed
from worker_core import WorkerError


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
        self.selection = SelectionReporter(linear, self.selection_tables)
        self.control = control
        self.readiness.admitted = self.is_admitted

    def is_admitted(self, issue, route):
        if self.control and self.control.is_paused():
            return False
        table = self.table(route.repository)
        state, _ = table.get("slot")
        key = dispatch_key(self.registry, route, issue)
        if state["active_key"] != key:
            return False
        # Readiness timer retries can dispatch without calling queue.advance.
        # Adopt legacy slots and retry their marker before that dispatch too.
        state, _ = self.reserve(table)
        return state["active_key"] == key

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
        for _ in range(32):
            state, etag = table.get("slot")
            existing, version = optional(table, key)
            if existing and (existing["status"] != "skipped" or submitted):
                return key
            sequence = state["sequence"] + 1
            row = {"RowKey": key, "sequence": sequence, "status": "submitted" if submitted else "queued",
                   **{k: fields[k] for k in ("issue_id", "identifier", "repository", "project_id")}}
            if existing:
                # Rejoining a skipped task must not lose a pending label removal.
                row.update({k: v for k, v in existing.items() if k.startswith("selection_")})
            state["sequence"] = sequence
            try:
                table.transaction([(state, etag), (row, version)])
                return key
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Repository queue is busy; retry admission")

    def enqueue(self, issue, route):
        key = dispatch_key(self.registry, route, issue)
        for _ in range(32):
            try:
                self.insert(self.table(route.repository), key,
                            {"issue_id": issue["id"], "identifier": issue["identifier"],
                             "repository": route.repository, "project_id": route.project_id})
                return key
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Repository queue is busy; retry admission")

    def head(self, table):
        rows = [r for r in table.list() if r["RowKey"] != "slot" and r["status"] not in TERMINAL]
        # Imported submissions have already crossed the dispatch boundary and
        # take precedence over new Todo work, regardless of their Linear state.
        return min(rows, key=lambda r: (r["status"] == "queued", r["sequence"])) if rows else None

    def reserve(self, table):
        state, etag = table.get("slot")
        if not state["active_key"]:
            head = self.head(table)
            if not head:
                return state, etag
            row, version = table.get(head["RowKey"])
            row["status"] = "active"
            selection_changed(row)
            state.update(active_key=row["RowKey"], owner="")
            table.transaction([(state, etag), (row, version)])
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
            raise WorkerError("Worker no longer owns the repository slot")

    def start_worker(self, repository, key, owner):
        table = self.table(repository)
        state, etag = self.reserve(table)
        if state["active_key"] != key:
            raise WorkerError("Another task owns the repository slot")
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
        if known_owner and self.github.worker_result(route.repository, known_owner).get("status") != "completed":
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
        if pull:
            outcome, reason = pull_outcome(pull, route.auto_merge)
            if pull.get("merged"):
                return "completed", reason
            if pull.get("state") == "OPEN":
                if not run or run["status"] != "completed":
                    return "blocked", "Worker did not complete successfully; its pull request remains open. " + reason
                return "blocked" if outcome == "blocked" else "waiting", reason
            if not canceled:
                return "blocked", reason + " Rerun this task or cancel its Linear issue to abandon it."
        if canceled:
            return "abandoned", "Linear issue canceled; GitHub workers stopped and no open PR remains."
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
            current, reason = eligibility(issue or {}, self.registry)
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
        self.selection.sync(table, row["RowKey"])
        return True

    def dispatch(self, table, state, etag, row, route, *, retry=False):
        key = row["RowKey"]
        issue = self.linear.issue(row["issue_id"])
        current, reason = eligibility(issue or {}, self.registry)
        if current != route:
            message = f"No longer eligible ({reason})."
            if not self.finish_entry(table, state, etag, row, "skipped", message):
                return {"status": "waiting", "dispatch_key": key, "reason": "Waiting for the readiness worker to stop."}
            self.report(table, row, message)
            return {"status": "skipped", "dispatch_key": key}
        review = self.readiness.ensure(issue, route)
        if review["status"] != "ready":
            return {**review, "dispatch_key": key}
        approval = self.readiness.require(issue, route)
        # Fence observations before crossing the external dispatch boundary.
        # Preserve the review/previous attempt's owner so development startup
        # verifies that it stopped before taking over this same repository slot.
        table.replace(state, etag)
        receipt, version = optional(self.receipts, key)
        if receipt:
            if not retry or not receipt.get("retry_after") or receipt["retry_after"] > self.clock():
                return {"status": "already_claimed", "dispatch_key": key}
            receipt.update(status="dispatching", retry_after=0, readiness_wait_owner="",
                           readiness_review_key=approval.get("review_key", ""))
            self.receipts.replace(receipt, version)
        else:
            receipt = {"RowKey": key, "status": "dispatching", "created_at": int(self.clock()),
                       "workflow": route.workflow, "ref": route.ref, "auto_merge": route.auto_merge,
                       "readiness_review_key": approval.get("review_key", ""),
                       **{k: row[k] for k in ("issue_id", "identifier", "repository", "project_id")}}
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

    def advance(self, repository, *, refresh_selection=False):
        for _ in range(32):
            try:
                return self._advance(repository, refresh_selection=refresh_selection)
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Repository queue changed concurrently; retry reconciliation")

    def _advance(self, repository, *, refresh_selection=False):
        table = self.table(repository)
        self.selection.reconcile(table, refresh=refresh_selection)
        results = {}
        if self.control and self.control.is_paused():
            reason = "Azure deployment in progress; new workers are paused."
            for row in table.list():
                if row["RowKey"] != "slot" and row["status"] not in TERMINAL:
                    self.report(table, row, reason)
                    results[row["RowKey"]] = {"status": "deployment_paused", "dispatch_key": row["RowKey"]}
            return results
        for row in table.list():
            if row.get("status") in TERMINAL and row.get("reason"):
                self.report(table, row, row["reason"])
        while True:
            state, etag = self.reserve(table)
            key = state["active_key"]
            if not key:
                return results
            row, _ = table.get(key)
            receipt, _ = optional(self.receipts, key)
            route = self.current_route(row, receipt)
            if route is None and not receipt:
                reason = "Skipped because the project's repository mapping changed."
                if not self.finish_entry(table, state, etag, row, "skipped", reason):
                    return {key: {"status": "waiting", "dispatch_key": key,
                                  "reason": "Waiting for the readiness worker to stop."}}
                self.report(table, row, reason)
                results[key] = {"status": "skipped", "dispatch_key": key, "reason": reason}
                continue
            if route is None:
                reason = "Repository mapping changed for a legacy dispatch; restore it to verify its worker and PR."
                results[key] = {"status": "blocked", "dispatch_key": key, "reason": reason}
            elif not receipt:
                result = self.dispatch(table, state, etag, row, route)
                results[key] = result
                if result["status"] == "skipped":
                    continue
                reason = ("Dispatched; this task owns the repository until its run and PR are resolved."
                          if result["status"] in {"dispatched", "uncertain", "already_claimed"}
                          else "Waiting for a current passing readiness review; coding and publication are held.")
            else:
                action, reason = self.observe(row, receipt, route, state["owner"])
                if action in TERMINAL:
                    if not self.finish_entry(table, state, etag, row, action, reason):
                        return {key: {"status": "waiting", "dispatch_key": key,
                                      "reason": "Waiting for the admitted worker to stop."}}
                    self.report(table, row, reason)
                    results[key] = {"status": action, "dispatch_key": key, "reason": reason}
                    continue
                if action == "retry":
                    result = self.dispatch(table, state, etag, row, route, retry=True)
                    results[key] = result
                    if result["status"] == "skipped":
                        continue
                else:
                    results[key] = {"status": action, "dispatch_key": key, "reason": reason}
            self.report(table, row, reason)
            for waiting in table.list():
                if waiting["RowKey"] != "slot" and waiting["status"] in {"queued", "submitted"}:
                    self.report(table, waiting, f"Queued behind {row['identifier']}. {reason}")
                    results[waiting["RowKey"]] = {"status": "queued", "dispatch_key": waiting["RowKey"],
                                                "reason": f"Waiting for {row['identifier']}: {reason}"}
            return results

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
                self.advance(repository, refresh_selection=True)
            except Exception:
                logging.error("Repository queue reconciliation deferred for %s", repository)
