"""Recoverable Linear projection of durable repository-slot ownership.

Selection intent changes in the same transaction as admission/release. External
writes cannot be fenced by Azure ETags, so late responses are repaired against
the current slot. Timer refreshes also repair a process lost after a late write.
"""

import logging
from itertools import chain

from worker_core import WorkerError


def selection_changed(row):
    row.update(selection_version=row.get("selection_version", 0) + 1, selection_pending=True)


class SelectionReporter:
    def __init__(self, linear, tables):
        self.linear = linear
        self.tables = tables

    def selected(self, table, issue_id):
        # An issue can reenter through another project, generation, or repository.
        # Historical cleanup must preserve its marker in any current slot.
        others = (candidate for candidate in self.tables() if candidate is not table)
        for candidate in chain((table,), others):
            try:
                slot, _ = candidate.get("slot")
            except WorkerError as exc:
                if exc.status != 404:
                    raise
                continue  # A mapped repository may not have a queue yet.
            if slot["active_key"]:
                active, _ = candidate.get(slot["active_key"])
                if active["issue_id"] == issue_id and not active.get("readiness_rejected"):
                    return True
        return False

    @staticmethod
    def defer(table, key):
        for _ in range(32):
            row, etag = table.get(key)
            if row.get("selection_pending"):
                return
            row["selection_pending"] = True
            try:
                table.replace(row, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Selection reporting changed concurrently; retry")

    def sync(self, table, key, *, refresh=False):
        try:
            for _ in range(32):
                row, _ = table.get(key)
                if not refresh and not row.get("selection_pending"):
                    return
                selected = self.selected(table, row["issue_id"])
                self.linear.set_selected(row["issue_id"], selected)
                latest, etag = table.get(key)
                if (latest.get("selection_version") != row.get("selection_version")
                        or self.selected(table, row["issue_id"]) != selected):
                    # Our external write may have arrived after a newer report.
                    # Repair even if that reporter already acknowledged its intent.
                    refresh = True
                    continue
                latest["selection_pending"] = False
                try:
                    table.replace(latest, etag)
                    return
                except WorkerError as exc:
                    if exc.status != 409:
                        raise
                    refresh = True
            raise WorkerError("Selection reporting changed concurrently; retry")
        except Exception:
            # An ambiguous label response can have changed Linear after another
            # reporter acknowledged a newer version. Persist a retry in that case.
            try:
                self.defer(table, key)
            except Exception:
                pass  # Timer refresh also repairs acknowledged historical rows.
            logging.error("Linear selection label update deferred for %s", key)

    def reconcile(self, table, *, refresh=False):
        for row in table.list():
            if row.get("selection_version") and (refresh or row.get("selection_pending")):
                self.sync(table, row["RowKey"], refresh=refresh)
