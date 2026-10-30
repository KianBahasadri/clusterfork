"""Recoverable Linear projection of durable repository waiting/admission state.

Label intent changes in the same transaction as enqueue/admission/release. External
writes cannot be fenced by Azure ETags, so late responses are repaired against
the current queue/slot state. Durable in-flight markers recover lost writers.
"""

import logging
import time
from itertools import chain

from pending_writes import PendingWrites
from routing_timing import record, timestamps
from worker_core import WorkerError


def selection_changed(row):
    row.update(selection_version=row.get("selection_version", 0) + 1, selection_pending=True)


class SelectionReporter:
    def __init__(self, linear, tables, clock=time.time):
        self.linear = linear
        self.tables = tables
        self.clock = clock
        self.writes = PendingWrites("selection", clock)

    def labels(self, table, issue_id):
        # An issue can reenter through another project, generation, or repository.
        # Historical cleanup must preserve its current waiting/selected marker.
        others = (candidate for candidate in self.tables() if candidate is not table)
        active = queued = False
        for candidate in chain((table,), others):
            try:
                slot, _ = candidate.get("slot")
            except WorkerError as exc:
                if exc.status != 404:
                    raise
                continue  # A mapped repository may not have a queue yet.
            if slot["active_key"]:
                # Root and repair rows can name the same issue. Only the slot's
                # current attempt determines its selected marker.
                owner, _ = candidate.get(slot["active_key"])
                if owner["issue_id"] == issue_id:
                    if not owner.get("readiness_rejected"):
                        return True, False
                    active = True
            queued = queued or any(row.get("status") == "queued" and row.get("issue_id") == issue_id
                                   for row in candidate.list())
        return False, queued and not active

    def write_labels(self, issue_id, selected, queued):
        # Remove the previous marker first. Attempt both writes even if one
        # fails; the shared durable intent retries any partial/ambiguous result.
        updates = [(self.linear.set_selected, selected), (self.linear.set_queued, queued)]
        if not queued:
            updates.reverse()
        error = None
        for update, enabled in updates:
            try:
                update(issue_id, enabled)
            except Exception as exc:
                error = exc
        if error is not None:
            raise error

    def defer(self, table, key, token):
        for _ in range(32):
            row, etag = table.get(key)
            self.writes.finish(row, token, retry=True)
            try:
                table.replace(row, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Selection reporting changed concurrently; retry")

    def sync(self, table, key, *, refresh=False):
        token = None
        try:
            for _ in range(32):
                row, etag = table.get(key)
                if not refresh and not row.get("selection_pending"):
                    return
                selected, queued = self.labels(table, row["issue_id"])
                token = self.writes.begin(row)
                try:
                    table.replace(row, etag)
                except WorkerError as exc:
                    if exc.status != 409:
                        raise
                    continue
                started_at = self.clock()
                record("selection_label_started", at=started_at, dispatch_key=key, issue_id=row["issue_id"],
                       selected=selected, queued=queued, selection_version=row.get("selection_version"), **timestamps(row))
                self.write_labels(row["issue_id"], selected, queued)
                reported_at = self.clock()
                record("selection_label_written", at=reported_at, dispatch_key=key, issue_id=row["issue_id"],
                       selected=selected, queued=queued, selection_version=row.get("selection_version"),
                       started_at=started_at, **timestamps(row))
                for _ in range(32):
                    latest, etag = table.get(key)
                    changed = (latest.get("selection_version") != row.get("selection_version")
                               or self.labels(table, row["issue_id"]) != (selected, queued))
                    self.writes.finish(latest, token, retry=changed)
                    if not changed:
                        latest["selection_reported_at"] = reported_at
                    try:
                        table.replace(latest, etag)
                        break
                    except WorkerError as exc:
                        if exc.status != 409:
                            raise
                else:
                    raise WorkerError("Selection reporting changed concurrently; retry")
                token = None
                if not changed:
                    return
                # Our external write may have arrived after a newer report.
                refresh = True
            raise WorkerError("Selection reporting changed concurrently; retry")
        except BaseException as exc:
            # An ambiguous label response can have changed Linear after another
            # reporter acknowledged a newer version. Persist a retry in that case.
            try:
                self.defer(table, key, token)
            except Exception:
                pass  # The persisted in-flight marker still requires repair.
            if not isinstance(exc, Exception):
                raise
            record("selection_label_deferred", at=self.clock(), dispatch_key=key, error_type=type(exc).__name__)
            logging.error("Linear repository labels deferred for %s", key)

    def reconcile(self, table):
        for row in table.list():
            if row.get("selection_version") and row.get("selection_pending"):
                self.sync(table, row["RowKey"])
