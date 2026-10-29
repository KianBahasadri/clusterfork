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
        return self.state()[0].get("paused") is True

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
