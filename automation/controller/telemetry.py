"""Bounded, optional projections of committed evidence. Never a lifecycle input."""

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import math
import re
import threading
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from common import diagnostics
from .model import ExternalObserved, Finish, Progress, Review, Start, StepResult, identity
from .workflows import ENGINE

STREAM = "Custom-ClusterforkController"
MAX_EVENT_BYTES = 4096
MAX_PENDING = 1024
MAX_AGE = 6 * 3600
MAX_TRIES = 5
MAX_BATCH = 64
MAX_BATCH_BYTES = 256 * 1024
DAILY_BYTES = 16 * 1024 * 1024
SAMPLES_PER_MINUTE = 32
HTTP_CONTEXT = ContextVar("controller_http_telemetry", default=None)
CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "timed_out", "action_required", "stale", "skipped", "startup_failure"}
QUIET = {"broker/heartbeat", "broker/save", "broker/instructions", "controller/health"}


def timestamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds")


def metadata(db, key, default=""):
    row = db.execute("SELECT value FROM metadata WHERE key=?", ("telemetry:" + key,)).fetchone()
    return row[0] if row else default


def put_metadata(db, key, value):
    db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               ("telemetry:" + key, str(value)))


def safe_failure(value):
    # Reuse the worker allowlist, then take only public classifications. In
    # particular, never copy failure_output, exception messages or arbitrary keys.
    if not isinstance(value, dict):
        return {}
    try:
        failure = diagnostics.validate_failure({k: v for k, v in value.items() if k != "failure_output"})
    except (ValueError, TypeError):
        return {}
    return {k: failure[k] for k in ("operation", "kind", "error_type", "reason_code", "http_status", "exit_code",
                                   "graphql_code", "graphql_type", "graphql_reason", "request_id") if k in failure}


def correlation(values):
    ids = diagnostics.identifiers(values)
    result = {"RunId": ids.get("run_id", ""), "RunAttempt": int(ids.get("run_attempt", "0")),
              "RequestId": ids.get("request_id", ""), "AttemptId": ids.get("dispatch_key", "")}
    if result["RunId"] and result["RunAttempt"]:
        result["ActionsUrl"] = f"https://github.com/{ENGINE}/actions/runs/{result['RunId']}/attempts/{result['RunAttempt']}"
    return result


def correlate(phase):
    target = HTTP_CONTEXT.get()
    if target is not None:
        target.update(correlation(diagnostics.CONTEXT.get()))
        if phase in {"review", "coding"}:
            target["Phase"] = phase


def event(db, event_id, kind, now, **fields):
    return {"TimeGenerated": timestamp(now), "EventId": event_id, "EventType": kind,
            "StreamStartedAt": metadata(db, "started_at"), "Revision": metadata(db, "revision"), **fields}


def enqueue(db, value, now, *, attempt_id=None):
    if attempt_id is not None:
        receipt = db.execute("INSERT OR IGNORE INTO telemetry_receipts(event_id,attempt_id) VALUES (?,?)",
                             (value["EventId"], attempt_id))
        if not receipt.rowcount:
            return
        value = {**value, "EvidenceSequence": receipt.lastrowid}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_EVENT_BYTES:
        raise ValueError("Telemetry record exceeds its bound")
    if db.execute("SELECT COUNT(*) FROM telemetry_outbox").fetchone()[0] >= MAX_PENDING:
        put_metadata(db, "dropped", int(metadata(db, "dropped", "0")) + 1)
        return
    db.execute("INSERT OR IGNORE INTO telemetry_outbox(event_id,created_at,payload) VALUES (?,?,?)",
               (value["EventId"], now, encoded))


def capture(db, command, now):
    """Project only successful commands, atomically with their evidence commit.

    The savepoint makes this optional local projection independent of lifecycle.
    Receipts are a fixed set per attempt, retained with its existing evidence even
    after the bounded delivery queue expires. No historical outcomes are backfilled.
    """
    if not isinstance(command, (Start, Review, Finish, Progress, StepResult, ExternalObserved)):
        return
    saved = False
    try:
        db.execute("SAVEPOINT telemetry_projection")
        saved = True
        if metadata(db, "started_at"):
            _capture(db, command, now)
        db.execute("RELEASE telemetry_projection")
    except Exception:
        if saved:
            db.execute("ROLLBACK TO telemetry_projection")
            db.execute("RELEASE telemetry_projection")
        logging.warning("Controller telemetry projection unavailable")


def _capture(db, command, now):
    attempt = dict(db.execute("SELECT * FROM attempts WHERE attempt_id=?", (command.attempt_id,)).fetchone())
    task = dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (command.task_id,)).fetchone())
    # The run binding comes from committed, authenticated controller evidence,
    # never the body of a request or a client's diagnostic fields.
    phase = attempt['execution_phase']
    source = attempt
    if isinstance(command, ExternalObserved) and not attempt['run_id'] and command.run_id:
        # Review stop can atomically issue a separate coding dispatch. Its old
        # authenticated run remains in the review session after that transition.
        review = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase='review'", (command.attempt_id,)).fetchone()
        if review and (review['run_id'], review['run_attempt']) == (command.run_id, command.run_attempt):
            source, phase = dict(review), 'review'
    ids = correlation({"run_id": source["run_id"], "run_attempt": str(source["run_attempt"]),
                       "dispatch_key": attempt["attempt_id"], "request_id": diagnostics.CONTEXT.get().get("request_id")})
    base = {**ids, "TaskId": task["task_id"], "RetryKind": attempt["retry_kind"]}
    def emit(kind, phase="", stage="", outcome="", **extra):
        key = identity("controller-telemetry-v1", command.attempt_id, kind, phase, stage, outcome,
                       extra.get("RetryKind", ""))
        enqueue(db, event(db, key, kind, now, **{**base, "Phase": phase, "Stage": stage,
                                               "Outcome": outcome, **extra}), now, attempt_id=command.attempt_id)

    if isinstance(command, Start):
        emit("phase_preflight_started" if command.role.selector else "phase_started", command.role.phase)
    elif isinstance(command, (Progress, StepResult)):
        emit("phase_stage", "coding", attempt["stage"])
    elif isinstance(command, (Review, Finish)):
        phase = "review" if isinstance(command, Review) else command.phase
        session = db.execute("SELECT data FROM sessions WHERE attempt_id=? AND phase=?",
                             (command.attempt_id, phase)).fetchone()
        result = json.loads(attempt["review_result"]) if isinstance(command, Review) else None
        emit("phase_result", phase, outcome=result["verdict"] if result else command.outcome,
             Failure=safe_failure(json.loads(session[0]).get("failure")))
    elif isinstance(command, ExternalObserved):
        # A missing callback is not a phase failure. A rejected dispatch is not
        # an observed GitHub stop. Missing conclusions are left unreported.
        if command.stopped and ids["RunId"] and command.conclusion in CONCLUSIONS:
            emit("github_stopped", phase, outcome=command.conclusion)
        if command.publication_checked and command.pull_state:
            emit("publication_observed", phase, outcome=command.pull_state)
        if command.stopped and command.all_attempts_stopped and command.publication_checked:
            emit("task_outcome", phase, outcome=task["phase"], RetryKind=attempt["retry_kind"],
                 TaskFinal=task["phase"] in {"completed", "canceled", "skipped"},
                 AllStopped=True, PublicationChecked=True)


def http_operation(method, path):
    # Classifications are finite. Never export the path, query, method or headers.
    if method == "GET" and path.split("?", 1)[0] == "/api/health":
        return "controller/health"
    if method == "POST":
        if path in {"/api/linear", "/api/accounts"}:
            return "controller/" + path.rsplit("/", 1)[1]
        action = path.removeprefix("/api/worker/")
        if path.startswith("/api/worker/") and action in diagnostics.ACTIONS:
            return "broker/" + action
    return "controller/unknown"


def http_failure(exc, operation):
    try:
        return safe_failure(diagnostics.failure(exc, operation))
    except Exception:
        return {}  # Failure diagnostics are optional even for malformed exceptions.


class Telemetry:
    """One background sender; handlers only add bounded in-memory measurements."""

    def __init__(self, database, credential, config, revision, *, clock=time.time, send=None):
        endpoint, rule = config["endpoint"].rstrip("/"), config["rule_id"]
        if (not re.fullmatch(r"https://[a-z0-9.-]+\.ingest\.monitor\.azure\.com", endpoint)
                or not re.fullmatch(r"dcr-[0-9a-f]{32}", rule)):
            raise ValueError("Invalid controller telemetry destination")
        self.url = endpoint + f"/dataCollectionRules/{rule}/streams/{STREAM}?api-version=2023-01-01"
        self.database, self.credential, self.clock = database, credential, clock
        self.send = send or self._send
        self.instance = str(uuid4())
        self.lock = threading.Lock()
        self.metrics, self.samples = {}, []
        self.sample_minute, self.sample_count, self.dropped = -1, 0, 0
        self.last_heartbeat = -math.inf
        self.next_flush = -math.inf
        now = clock()
        with database.transaction() as db:
            if not metadata(db, "started_at"):
                put_metadata(db, "started_at", timestamp(now))
            put_metadata(db, "revision", revision)
            enqueue(db, event(db, identity(self.instance, "start"), "controller_started", now), now)

    def request(self, operation, status, elapsed_ms, ids=None, failure=None):
        """No I/O and no database access on an HTTP request's telemetry path."""
        if not diagnostics.valid_operation(operation) or type(status) is not int or not 100 <= status <= 599:
            return
        if not isinstance(elapsed_ms, (int, float)) or not math.isfinite(elapsed_ms):
            return
        now = self.clock()
        minute = int(now // 60)
        status_class = str(status // 100) + "xx"
        elapsed_ms = min(120000, max(0, elapsed_ms))
        with self.lock:
            key = (operation, status_class)
            if key not in self.metrics and len(self.metrics) >= 256:
                self.dropped += 1
                return
            metric = self.metrics.setdefault(key, {"Count": 0, "Failures": 0, "DurationMs": 0, "MaxDurationMs": 0})
            metric["Count"] += 1
            metric["Failures"] += int(status >= 400)
            metric["DurationMs"] += elapsed_ms
            metric["MaxDurationMs"] = max(metric["MaxDurationMs"], elapsed_ms)
            if minute != self.sample_minute:
                self.sample_minute, self.sample_count = minute, 0
            if status < 400 and operation in QUIET:
                return
            if self.sample_count >= SAMPLES_PER_MINUTE or len(self.samples) >= SAMPLES_PER_MINUTE:
                self.dropped += 1
                return
            self.sample_count += 1
            values = ids or {}
            ids = correlation({"run_id": values.get("RunId"), "run_attempt": str(values.get("RunAttempt", "")),
                               "dispatch_key": values.get("AttemptId"), "request_id": values.get("RequestId")})
            if values.get("Phase") in {"review", "coding"}:
                ids["Phase"] = values["Phase"]
            if status >= 400 and not failure:
                failure = {"operation": operation, "kind": "http_error", "http_status": status}
            self.samples.append({"TimeGenerated": timestamp(now), "EventId": identity(self.instance, str(uuid4())),
                                 "Operation": operation, "Status": status, "DurationMs": elapsed_ms,
                                 **ids, "Failure": safe_failure(failure)})

    def _collect(self, now):
        with self.lock:
            metrics, samples, dropped = self.metrics, self.samples, self.dropped
            self.metrics, self.samples, self.dropped = {}, [], 0
        with self.database.transaction() as db:
            expired = db.execute("DELETE FROM telemetry_outbox WHERE created_at<? OR tries>=?",
                                 (now - MAX_AGE, MAX_TRIES)).rowcount
            total = int(metadata(db, "dropped", "0")) + dropped + expired
            put_metadata(db, "dropped", total)
            for (operation, status_class), value in metrics.items():
                key = identity(self.instance, now, operation, status_class)
                enqueue(db, event(db, key, "http_summary", now, Operation=operation, StatusClass=status_class, **value), now)
            for value in samples:
                enqueue(db, {**event(db, value["EventId"], "http_request", now), **value}, now)
            if now - self.last_heartbeat >= 300:
                enqueue(db, event(db, identity(self.instance, now), "controller_heartbeat", now, Dropped=total), now)
                self.last_heartbeat = now

    def flush(self):
        """One bounded batch per minute. The reservation survives ambiguous sends."""
        now = self.clock()
        if now < self.next_flush:
            return
        self.next_flush = now + 60
        self._collect(now)
        with self.database.transaction() as db:
            rows = db.execute("SELECT * FROM telemetry_outbox WHERE next_at<=? ORDER BY created_at,event_id LIMIT ?",
                              (now, MAX_BATCH)).fetchall()
            selected, size = [], 2
            for row in rows:
                length = len(row["payload"].encode()) + 1
                if size + length > MAX_BATCH_BYTES:
                    break
                size += length
                selected.append(row)
            if not selected:
                return
            day = timestamp(now)[:10]
            used = int(metadata(db, "bytes", "0")) if metadata(db, "day") == day else 0
            if used + size > DAILY_BYTES:
                return
            put_metadata(db, "day", day)
            put_metadata(db, "bytes", used + size)
            for row in selected:
                db.execute("UPDATE telemetry_outbox SET tries=tries+1,next_at=? WHERE event_id=?",
                           (now + min(900, 60 * 2 ** row["tries"]), row["event_id"]))
        # Both token acquisition and ingestion MUST remain outside the owner lock
        # and every SQLite transaction, including during retries and shutdown.
        self.send(b"[" + b",".join(row["payload"].encode() for row in selected) + b"]")
        with self.database.transaction() as db:
            db.executemany("DELETE FROM telemetry_outbox WHERE event_id=?", ((row["event_id"],) for row in selected))

    def _send(self, payload):
        token = self.credential.get_token("https://monitor.azure.com/.default").token
        request = Request(self.url, data=payload, headers={"Authorization": "Bearer " + token,
                                                         "Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            if response.status != 204:
                raise RuntimeError("Telemetry ingestion was not acknowledged")

    def run(self, stop):
        while not stop.is_set():
            try:
                self.flush()
            except Exception:
                # Never serialize a response, URL, exception message or token.
                logging.warning("Controller telemetry delivery deferred")
            stop.wait(60)
