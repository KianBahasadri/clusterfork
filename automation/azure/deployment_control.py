"""Durable deployment pause and public build metadata."""

import json
from pathlib import Path

from worker_core import WorkerError


class DeploymentControl:
    def __init__(self, table):
        self.table = table

    def state(self):
        try:
            return self.table.get("deployment")
        except WorkerError as exc:
            if exc.status != 404:
                raise
            return {"RowKey": "deployment", "paused": False}, None

    def is_paused(self):
        state, _ = self.state()
        return state.get("paused") is True or self.instance_mismatch(state=state)

    def instance_mismatch(self, metadata=None, *, state=None):
        """Fence packaged instances against the durable target deployment revision."""
        revision = (build_metadata() if metadata is None else metadata).get("revision")
        if not revision:
            return False  # Unpackaged/local processes have no deployment identity.
        if state is None:
            state, _ = self.state()
        return bool(state.get("revision") and revision != state["revision"])

    def pause(self, owner, revision):
        for _ in range(32):
            row, etag = self.state()
            row.update(paused=True, owner=owner, revision=revision)
            if etag is None:
                if self.table.create(row):
                    return
                continue
            try:
                self.table.replace(row, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Deployment pause changed concurrently")

    def resume(self, owner, revision):
        row, etag = self.state()
        if row.get("owner") != owner or row.get("revision") != revision:
            raise WorkerError("A newer deployment owns the pause")
        row["paused"] = False
        self.table.replace(row, etag)


def build_metadata():
    path = Path(__file__).with_name("deployment.json")
    return json.loads(path.read_text()) if path.is_file() else {}
