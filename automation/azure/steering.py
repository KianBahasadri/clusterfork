"""Durable, attempt-bound Linear instructions. This service never dispatches work."""

from datetime import datetime
import logging
import re
import time
from uuid import uuid4

from dispatch_core import uuid_string
from pending_writes import PendingWrites
from worker_core import WorkerError

DELIVERY_SECONDS = 90
OBSERVATION_SECONDS = 90
MAX_INSTRUCTION_BYTES = 16_000
OPEN_STAGES = {"initializing", "selecting_account", "coding", "testing"}


class Steering:
    def __init__(self, registry, receipts, runs, table, linear, queue, clock=time.time):
        self.registry, self.receipts, self.runs = registry, receipts, runs
        self.table, self.linear, self.queue, self.clock = table, linear, queue, clock
        self.writes = PendingWrites("report", clock)

    def accepting(self, run):
        return (run.get("status") == "running" and run.get("lease_until", 0) > self.clock()
                and run.get("stage") in OPEN_STAGES and not run.get("steering_closed")
                and bool(run.get("attempt_id")))

    @staticmethod
    def pending(row):
        return (row.get("issue_id") and (row.get("state") in {"queued", "delivering"}
                or row.get("reported_state") != row.get("state") or row.get("report_pending")))

    def observation_issues(self):
        """Select background observations entirely from local active/pending state."""
        issues = {row["issue_id"] for row in self.table.list() if self.pending(row)}
        for run in self.runs.list():
            if not self.accepting(run):
                continue
            try:
                receipt, _ = self.receipts.get(run["RowKey"])
            except WorkerError as exc:
                if exc.status != 404:
                    raise
                continue
            route = self.registry.routes.get(receipt["project_id"])
            if not route or receipt["repository"] != route.repository:
                continue
            try:
                self.queue.assert_worker(route.repository, receipt["RowKey"], run["owner"])
            except WorkerError:
                continue
            issues.add(receipt["issue_id"])
        return issues

    def comment(self, comment_id):
        comment = self.linear.comment(comment_id)
        if comment and comment.get("issue"):
            self.observe(uuid_string(comment["issue"]["id"]))

    def active(self, issue):
        project = issue.get("project") or {}
        route = self.registry.routes.get(project.get("id"))
        if (not route or issue.get("archivedAt") or project.get("archivedAt")
                or issue.get("state", {}).get("type") in {"completed", "canceled"}
                or not any(label["name"] == self.registry.enrollment_label
                           for label in project.get("labels", {}).get("nodes", []))):
            return None
        candidates = []
        for receipt in self.receipts.list():
            if (receipt.get("issue_id") != issue["id"] or receipt.get("project_id") != route.project_id
                    or receipt.get("repository") != route.repository):
                continue
            try:
                run, _ = self.runs.get(receipt["RowKey"])
            except WorkerError as exc:
                if exc.status != 404:
                    raise
                continue
            if self.accepting(run):
                try:
                    # A root receipt and its repair share issue_id. A stale root
                    # heartbeat cannot compete with the receipt owning the slot.
                    self.queue.assert_worker(route.repository, receipt["RowKey"], run["owner"])
                except WorkerError:
                    continue
                candidates.append((receipt, run))
        return candidates[0] if len(candidates) == 1 else None

    def observe(self, issue_id, *, issue=None):
        issue_id = uuid_string(issue_id)
        issue = self.linear.issue(issue_id) if issue is None else issue
        if not issue:
            return
        table, control_key = self.table, "control:" + issue_id
        table.create({"RowKey": control_key, "sequence": 0, "lock_until": 0, "lock": ""})
        control, etag = table.get(control_key)
        if control.get("lock_until", 0) > self.clock():
            return  # Another complete issue scan owns ordering; polls/events retry.
        fence = str(uuid4())
        control.update(lock=fence, lock_until=self.clock() + OBSERVATION_SECONDS)
        try:
            table.replace(control, etag)
        except WorkerError as exc:
            if exc.status != 409:
                raise
            return
        try:
            trusted = {uuid_string(self.linear.viewer_id()), *self.registry.steering_user_ids}
            # Fetch all pages before accepting anything. Reversed/duplicate webhook
            # arrivals therefore cannot reorder comments in the same observation.
            comments = sorted(self.linear.issue_comments(issue_id), key=lambda c: (c["createdAt"], c["id"]))
            active = self.active(issue)
            for comment in comments:
                body = comment.get("body", "")
                match = re.match(r"^@clusterfork(?:\s+|$)", body, flags=re.IGNORECASE)
                if not match or comment.get("botActor"):
                    continue
                key = uuid_string(comment["id"])
                try:
                    table.get(key)
                    continue  # Freeze the first accepted snapshot, including rejection.
                except WorkerError as exc:
                    if exc.status != 404:
                        raise
                text = body[match.end():].strip()
                state, reason = "queued", ""
                author = (comment.get("user") or {}).get("id")
                if author not in trusted:
                    state, reason = "rejected", "This Linear user is not authorized to steer Clusterfork."
                elif not text or len(text.encode()) > MAX_INSTRUCTION_BYTES or "\x00" in text:
                    state, reason = "rejected", "Use a nonempty instruction of at most 16,000 UTF-8 bytes."
                elif not active:
                    state, reason = "failed", "No active development agent is accepting instructions on this issue (it may have finished). No workflow was started."
                elif datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00")).timestamp() < active[1]["steering_started_at"]:
                    state, reason = "failed", "This comment predates the current attempt; it will not be forwarded to a replacement run. Post a new comment."
                receipt, run = active if active else ({}, {})
                control, etag = table.get(control_key)
                if control["lock"] != fence or control["lock_until"] <= self.clock():
                    raise WorkerError("Instruction observation lease was lost")
                control["sequence"] += 1
                row = {"RowKey": key, "issue_id": issue_id, "author_id": author or "",
                       "sequence": control["sequence"], "state": state, "reason": reason,
                       "text": text if state == "queued" else "", "ack_id": str(uuid4()),
                       "reported_state": "", "created_at": comment["createdAt"],
                       "dispatch_key": receipt.get("RowKey", ""), "owner": run.get("owner", ""),
                       "attempt_id": run.get("attempt_id", ""), "repository": receipt.get("repository", "")}
                table.transaction([(control, etag), (row, None)])
        finally:
            control, etag = table.get(control_key)
            if control["lock"] == fence:
                control["lock_until"] = 0
                table.replace(control, etag)
        self.repair(issue_id)

    @staticmethod
    def bound(row, key, run):
        return (row.get("dispatch_key") == key and row.get("owner") == run.get("owner")
                and row.get("attempt_id") == run.get("attempt_id"))

    def repair(self, issue_id, *, closed_run=None):
        table = self.table
        for snapshot in table.list():
            if snapshot.get("issue_id") != issue_id or not self.pending(snapshot):
                continue
            for _ in range(8):
                row, etag = table.get(snapshot["RowKey"])
                if row["state"] not in {"queued", "delivering"}:
                    break
                try:
                    run, _ = self.runs.get(row["dispatch_key"])
                except WorkerError as exc:
                    if exc.status != 404:
                        raise
                    run = {}
                closed = (not self.bound(row, row["dispatch_key"], run)
                          or run.get("status") != "running" or run.get("lease_until", 0) <= self.clock()
                          or run.get("steering_closed") or run.get("stage") not in OPEN_STAGES
                          or (row["state"] == "delivering" and any(
                              row.get(field) != run.get(field) for field in ("thread_id", "turn_id")))
                          or (closed_run is not None and self.bound(row, *closed_run)))
                expired = row["state"] == "delivering" and row["delivery_until"] <= self.clock()
                if not closed and not expired:
                    break
                row.update(state="uncertain" if row["state"] == "delivering" else "failed",
                           reason=("Delivery was handed off, but agent acceptance could not be confirmed. It will not be replayed automatically."
                                   if row["state"] == "delivering" else
                                   "The bound agent attempt stopped accepting instructions before delivery. It will not be sent to another attempt."))
                try:
                    table.replace(row, etag)
                    break
                except WorkerError as exc:
                    if exc.status != 409:
                        raise
            self.report(table, snapshot["RowKey"])

    def defer_report(self, table, key, token):
        for _ in range(8):
            row, etag = table.get(key)
            self.writes.finish(row, token, retry=True)
            try:
                table.replace(row, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Instruction acknowledgement changed concurrently; retry")

    def report(self, table, key, *, refresh=False):
        token = None
        try:
            for _ in range(8):
                row, etag = table.get(key)
                if not refresh and row.get("reported_state") == row["state"] and not row.get("report_pending"):
                    return
                token = self.writes.begin(row)
                try:
                    table.replace(row, etag)
                except WorkerError as exc:
                    if exc.status != 409:
                        raise
                    continue
                link = (f"[worker run](https://github.com/{row['repository']}/actions/runs/"
                        f"{row['owner'].replace(':', '/attempts/')})" if row["owner"] else "this issue")
                source = f"[comment](https://linear.app/issue/{row['issue_id']}#comment-{key})"
                if row["state"] in {"queued", "delivering"}:
                    message = f"Queued instruction from {source} for {link}. The agent has not confirmed acceptance yet."
                elif row["state"] == "accepted":
                    message = f"Agent accepted instruction from {source} in {link}."
                else:
                    message = f"Instruction from {source} for {link}: {row['state']}. {row['reason']}"
                self.linear.status_comment(row["issue_id"], row["ack_id"], "Clusterfork steering: " + message)
                for _ in range(8):
                    current, etag = table.get(key)
                    changed = current["state"] != row["state"]
                    self.writes.finish(current, token, retry=changed)
                    if not changed:
                        current["reported_state"] = current["state"]
                    try:
                        table.replace(current, etag)
                        break
                    except WorkerError as exc:
                        if exc.status != 409:
                            raise
                else:
                    raise WorkerError("Instruction acknowledgement changed concurrently; retry")
                token = None
                if not changed:
                    return
                refresh = True  # Repair a write that raced a newer acknowledgement.
            raise WorkerError("Instruction acknowledgement changed concurrently; retry")
        except BaseException as exc:
            try:
                self.defer_report(table, key, token)
            except Exception:
                pass  # A lost writer's durable in-flight marker remains pending.
            if not isinstance(exc, Exception):
                raise
            logging.error("Linear instruction acknowledgement deferred for %s", key)

    def poll(self, receipt, run):
        issue_id, key = receipt["issue_id"], receipt["RowKey"]
        issue = self.linear.issue(issue_id)
        self.observe(issue_id, issue=issue)
        current = self.active(issue or {})
        valid = current and current[0]["RowKey"] == key and self.bound(
            {"dispatch_key": key, "owner": run["owner"], "attempt_id": run["attempt_id"]}, key, current[1])
        self.repair(issue_id, closed_run=None if valid else (key, run))
        if not valid:
            return {"instruction": None}
        table = self.table
        for row in sorted(table.list(), key=lambda r: r.get("sequence", 0)):
            if row.get("issue_id") != issue_id or not self.bound(row, key, run):
                continue
            if row["state"] == "delivering":
                return {"instruction": None}  # At most one handoff awaiting confirmation.
            if row["state"] != "queued":
                continue
            row, etag = table.get(row["RowKey"])
            if row["state"] != "queued":
                return {"instruction": None}
            row.update(state="delivering", delivery=str(uuid4()), delivery_until=self.clock() + DELIVERY_SECONDS,
                       thread_id=run["thread_id"], turn_id=run["turn_id"])
            try:
                table.replace(row, etag)  # Persist BEFORE returning; an ambiguous response is never resent.
            except WorkerError as exc:
                if exc.status != 409:
                    raise
                return {"instruction": None}
            return {"instruction": {field: row[field] for field in (
                "issue_id", "dispatch_key", "owner", "attempt_id", "thread_id", "turn_id",
                "text", "delivery", "sequence")} | {"comment_id": row["RowKey"]}}
        return {"instruction": None}

    def result(self, receipt, run, data):
        table = self.table
        row, etag = table.get(uuid_string(data.get("comment_id")))
        status = data.get("status")
        if (row.get("issue_id") != receipt["issue_id"] or not self.bound(row, receipt["RowKey"], run)
                or row.get("delivery") != data.get("delivery")
                or row.get("thread_id") != run.get("thread_id") or row.get("turn_id") != run.get("turn_id")):
            raise WorkerError("Instruction belongs to a different attempt or session", 403)
        if status not in {"accepted", "failed", "uncertain"}:
            raise WorkerError("Invalid instruction delivery result", 400)
        if row["state"] == status:
            self.report(table, row["RowKey"])
            return {"status": status}
        if row["state"] not in {"delivering", "uncertain"}:
            raise WorkerError("Instruction already has a different delivery result")
        reasons = {"accepted": "", "failed": "The agent rejected delivery or its active turn ended before delivery. It did not accept this instruction; post a new comment while the agent is coding.",
                   "uncertain": "The agent connection ended or timed out during delivery; acceptance is unknown. This instruction will not be replayed automatically."}
        row.update(state=status, reason=reasons[status])
        table.replace(row, etag)
        self.report(table, row["RowKey"])
        return {"status": status}

    def reconcile(self):
        # Late comments on completed work are answered by Issue/Comment webhooks.
        for issue_id in self.observation_issues():
            try:
                self.observe(issue_id)
            except Exception:
                logging.error("Linear instruction reconciliation deferred for %s", issue_id)
            try:
                self.repair(issue_id)
            except Exception:
                logging.error("Linear instruction repair deferred for %s", issue_id)
