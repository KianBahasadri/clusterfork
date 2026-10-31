"""Native SQLite fixtures shared by worker and local account-transfer tests."""

import json
from pathlib import Path
import tempfile

from controller.accounts import AccountPool
from controller.store import Database


def account_pool(test, provider="codex", clock=None):
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    database = Database(Path(directory.name) / "tasks.db")
    test.addCleanup(database.close)
    return AccountPool(database, provider, **({"clock": clock} if clock else {}))


def records(pool):
    with pool.database.transaction() as db:
        return {row["account_id"]: dict(row) for row in db.execute(
            "SELECT * FROM accounts WHERE provider=?", (pool.provider,))}


def update_account(pool, identity, **fields):
    allowed = {"auth", "state", "owner", "fence", "lease_until"}
    assert fields and fields.keys() <= allowed
    with pool.database.transaction() as db:
        db.execute("UPDATE accounts SET " + ",".join(f"{key}=?" for key in fields)
                   + " WHERE provider=? AND account_id=?", (*fields.values(), pool.provider, identity))


def coding_fixture(test, now, *, context_sha=None, identifier=None):
    from test_controller_broker import ControllerBrokerTests
    fixture = ControllerBrokerTests(methodName="runTest")
    if context_sha:
        fixture.context_sha = context_sha
    if identifier:
        fixture.identifier = identifier
    fixture.setUp()
    test.addCleanup(fixture.doCleanups)
    fixture.broker.clock = lambda: now
    fixture.review()
    with fixture.database.transaction() as db:
        db.execute("DELETE FROM accounts")
    return fixture


def execution_row(fixture):
    """Project a real execution into the existing pure comment renderer's input."""
    view = fixture.controller.inspect(fixture.view.task_id)
    session = view.session("coding")
    data = session["data"]
    first = (data.get("usage_segments") or [{}])[0]
    row = {**first, **data, "account_id": first.get("account_id",session["account_id"]), "owner": f"{view.attempt['run_id']}:{view.attempt['run_attempt']}",
           "status": view.attempt["agent_outcome"] or "running"}
    if session["account_id"]:
        row["assigned_account_id"] = session["account_id"]
    for key, value in tuple(row.items()):
        if isinstance(value, (list, dict)):
            row[key] = json.dumps(value)
    return row
