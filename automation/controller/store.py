"""One process owns a local transactional database; readers get committed views."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sqlite3
import threading

from .model import Rejected

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL UNIQUE,
 issue_id TEXT NOT NULL, project_id TEXT NOT NULL, generation TEXT NOT NULL,
 repository TEXT NOT NULL, identifier TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('queued','reviewing','ready','executing','published','held','completed','skipped','canceled')),
 eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
 dependencies_ready INTEGER NOT NULL CHECK(dependencies_ready IN (0,1)),
 priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 4), sort_order REAL NOT NULL,
 content_revision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 canceled INTEGER NOT NULL DEFAULT 0 CHECK(canceled IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 1, spec TEXT,
 current_attempt TEXT, repair_used INTEGER NOT NULL DEFAULT 0 CHECK(repair_used IN (0,1)),
 review_cycle INTEGER NOT NULL DEFAULT 1, review_attempts INTEGER NOT NULL DEFAULT 0 CHECK(review_attempts >= 0),
 review_attention TEXT NOT NULL DEFAULT '', review_attention_at REAL NOT NULL DEFAULT 0,
 review_submitted_at REAL NOT NULL DEFAULT 0, pending_submission_at REAL NOT NULL DEFAULT 0,
 UNIQUE(generation, project_id, issue_id)
);
CREATE TABLE IF NOT EXISTS grants (
 repository TEXT PRIMARY KEY,
 task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id)
);
CREATE TABLE IF NOT EXISTS attempts (
 attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 ordinal INTEGER NOT NULL CHECK(ordinal > 0),
 dispatch_status TEXT NOT NULL CHECK(dispatch_status IN ('intent','accepted','rejected','unknown')),
 run_id TEXT NOT NULL DEFAULT '', run_attempt INTEGER NOT NULL DEFAULT 0,
 stopped INTEGER NOT NULL DEFAULT 0, all_stopped INTEGER NOT NULL DEFAULT 0,
 publication_checked INTEGER NOT NULL DEFAULT 0,
 conclusion TEXT NOT NULL DEFAULT '', review_result TEXT,
 agent_outcome TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'initializing',
 coding_started INTEGER NOT NULL DEFAULT 0,
 pull_request TEXT NOT NULL DEFAULT '', pull_state TEXT NOT NULL DEFAULT '',
 retry_kind TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0,
 started_at REAL NOT NULL DEFAULT 0,
 attempt_kind TEXT NOT NULL DEFAULT 'normal' CHECK(attempt_kind IN ('normal','startup','capacity','context','review','repair')),
 review_cycle INTEGER NOT NULL DEFAULT 1, review_charged INTEGER NOT NULL DEFAULT 1,
 failure_class TEXT NOT NULL DEFAULT '', retry_decision TEXT NOT NULL DEFAULT '', review_detail TEXT NOT NULL DEFAULT '',
 UNIQUE(task_id,ordinal), UNIQUE(run_id,run_attempt,attempt_id)
);
CREATE TABLE IF NOT EXISTS sessions (
 attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 phase TEXT NOT NULL CHECK(phase IN ('review','coding')),
 account_id TEXT NOT NULL DEFAULT '', agent_started INTEGER NOT NULL DEFAULT 0,
 selector_started INTEGER NOT NULL DEFAULT 0,
 finished INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL DEFAULT 0,
 data TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(attempt_id,phase)
);
CREATE TABLE IF NOT EXISTS holds (owner TEXT PRIMARY KEY, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS effects (
 effect_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 kind TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','claimed','done')) DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_history (
 legacy_key TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
 event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1))
);
CREATE TABLE IF NOT EXISTS review_submissions (
 submission_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), submitted_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS comment_signatures (
 key TEXT PRIMARY KEY, signature TEXT NOT NULL, confirmed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS image_snapshots (
 revision TEXT PRIMARY KEY, digest TEXT NOT NULL, images TEXT NOT NULL, observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
 provider TEXT NOT NULL CHECK(provider IN ('codex','cursor')), account_id TEXT NOT NULL,
 auth TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', fence TEXT NOT NULL DEFAULT '',
 lease_until REAL NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(provider,account_id)
);
CREATE TABLE IF NOT EXISTS account_usage (
 provider TEXT NOT NULL, account_id TEXT NOT NULL, measurement TEXT,
 attempted_at REAL NOT NULL, error TEXT,
 PRIMARY KEY(provider,account_id),
 FOREIGN KEY(provider,account_id) REFERENCES accounts(provider,account_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS instructions (
 comment_id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 state TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(issue_id,sequence)
);
CREATE TABLE IF NOT EXISTS telemetry_receipts (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS telemetry_outbox (
 event_id TEXT PRIMARY KEY, created_at REAL NOT NULL, payload TEXT NOT NULL,
 tries INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0
);
PRAGMA user_version=8;
"""


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._owner = os.open(str(self.path) + ".owner", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._owner)
            self._owner = None
            raise Rejected("Another controller owns this task database") from None
        self.connection = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8):
            self.close()
            raise Rejected("Unsupported task database version")
        if version == 1:
            with self.transaction() as db:
                db.execute("ALTER TABLE attempts ADD COLUMN attempt_kind TEXT NOT NULL DEFAULT 'normal' "
                           "CHECK(attempt_kind IN ('normal','startup','capacity','context','review','repair'))")
                db.execute("PRAGMA user_version=2")
            version=2
        if version == 2:
            with self.transaction() as db:
                db.execute("ALTER TABLE tasks ADD COLUMN canceled INTEGER NOT NULL DEFAULT 0 CHECK(canceled IN (0,1))")
                db.execute("PRAGMA user_version=3")
        if version and version < 8:
            with self.transaction() as db:
                for table, fields in {
                    'tasks': {'review_cycle': 'INTEGER NOT NULL DEFAULT 1',
                              'review_attempts': 'INTEGER NOT NULL DEFAULT 0 CHECK(review_attempts >= 0)',
                              'review_attention': "TEXT NOT NULL DEFAULT ''",
                              'review_attention_at': 'REAL NOT NULL DEFAULT 0',
                              'review_submitted_at': 'REAL NOT NULL DEFAULT 0',
                              'pending_submission_at': 'REAL NOT NULL DEFAULT 0'},
                    'attempts': {'review_cycle': 'INTEGER NOT NULL DEFAULT 1',
                                 'review_charged': 'INTEGER NOT NULL DEFAULT 1',
                                 'failure_class': "TEXT NOT NULL DEFAULT ''",
                                 'retry_decision': "TEXT NOT NULL DEFAULT ''",
                                 'review_detail': "TEXT NOT NULL DEFAULT ''"},
                }.items():
                    columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                    for name, definition in fields.items():
                        if name not in columns:
                            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        self.connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        with self._lock:
            if self.connection.in_transaction:
                # Controller commands may compose local resource operations. The
                # outer command owns commit/rollback while this reentrant lock
                # excludes every other request thread.
                yield self.connection
                return
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def close(self):
        if getattr(self, "connection", None):
            self.connection.close()
            self.connection = None
        if self._owner is not None:
            os.close(self._owner)
            self._owner = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
