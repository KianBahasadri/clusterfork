"""Small, shared operational records. Never serialize exceptions or request bodies."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
from functools import wraps
import logging
import math
import re
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

CONTEXT = ContextVar("clusterfork_diagnostics", default={})
STAGES = {"initializing", "selecting_account", "coding", "testing", "publishing", "review", "waiting_for_quota"}
ACTIONS = {"start", "heartbeat", "accounts", "claim", "save", "checkout", "github", "progress", "finish",
           "review", "steering_open", "instructions", "instruction_result", "steering_close"}
LINEAR_OPERATIONS = {"issue", "project", "comments", "viewer", "comment", "issueUpdate", "issueLabels",
                     "issueLabelCreate", "issueAddLabel", "issueRemoveLabel", "commentUpdate", "commentCreate"}
OPERATIONS = {"worker/execution", "worker/heartbeat", "worker/account_release", "worker/final_usage",
              "worker/linear_sync", "worker/steering_sync", "worker/agent", "worker/readiness",
              "http/linear", "http/github", "http/oidc", "http/other", "azure/worker", "azure/health",
              "azure/route_event", "azure/reconcile", "azure/webhook", "command/git", "command/gh",
              "command/python", "command/codex", "command/other", *("broker/" + name for name in ACTIONS),
              *("linear/" + name for name in LINEAR_OPERATIONS), "github/installation", "github/access_token",
              "github/branch", "github/pulls", "github/pull", "github/dispatch", "github/runs", "github/graphql"}
KINDS = {"timeout", "http_error", "transport_error", "command_failed", "lease_lost", "worker_error"}
ERROR_TYPES = {"Exception", "TimeoutError", "HTTPError", "URLError", "ConnectionError", "OSError",
               "WorkerFailure", "WorkerError", "CodexError", "CodexRejected", "RequestError", "DispatchError",
               "PermissionError", "FileNotFoundError", "KeyError", "TypeError", "ValueError", "RuntimeError",
               "JSONDecodeError", "ServiceRequestError", "ServiceResponseError", "HttpResponseError",
               "ResourceModifiedError", "ResourceNotFoundError", "ClientAuthenticationError"}


def valid_operation(value):
    return isinstance(value, str) and (value in OPERATIONS or re.fullmatch(r"validation/[1-9][0-9]{0,2}", value))


def identifiers(values):
    result = {}
    for name, pattern in {"dispatch_key": r"[0-9a-f]{64}", "run_id": r"[1-9][0-9]{0,29}",
                          "run_attempt": r"[1-9][0-9]{0,9}", "request_id": r"[0-9a-f-]{36}"}.items():
        value = values.get(name)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            result[name] = value
    if isinstance(values.get("stage"), str) and values["stage"] in STAGES:
        result["stage"] = values["stage"]
    if values.get("phase") in ("readiness", "development"):
        result["phase"] = values["phase"]
    return result


@contextmanager
def context(**values):
    token = CONTEXT.set({**CONTEXT.get(), **identifiers(values)})
    try:
        yield
    finally:
        CONTEXT.reset(token)


def record(event, *, level=logging.INFO, **fields):
    # Values supplied here are controlled operation names, counts, IDs and statuses.
    # Payloads, URLs, headers, argv, exception messages and locals never enter logs.
    logging.log(level, "Clusterfork: %s", json.dumps(
        {"event": event, **CONTEXT.get(), **fields}, sort_keys=True))


def validate_failure(value):
    if (not isinstance(value, dict) or not valid_operation(value.get("operation"))
            or not isinstance(value.get("kind"), str) or value["kind"] not in KINDS):
        raise ValueError("Invalid failure diagnostic")
    result = {"operation": value["operation"], "kind": value["kind"], **identifiers(value)}
    if isinstance(value.get("error_type"), str) and value["error_type"] in ERROR_TYPES:
        result["error_type"] = value["error_type"]
    for name, maximum in {"elapsed_ms": 86_400_000, "timeout_seconds": 86_400,
                          "http_status": 599, "exit_code": 255}.items():
        number = value.get(name)
        if number is not None:
            minimum = -255 if name == "exit_code" else 0
            if type(number) not in (int, float) or not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError("Invalid failure measurement")
            result[name] = number
    # Stack locations contain only repository filenames and line numbers, no source or locals.
    locations = value.get("locations", [])
    if not isinstance(locations, list) or len(locations) > 8 or any(
        not isinstance(item, str) or not re.fullmatch(r"[a-z_]{1,60}\.py:[1-9][0-9]{0,5}", item) for item in locations
    ):
        raise ValueError("Invalid failure locations")
    if locations:
        result["locations"] = locations
    return result


def failure(exc, operation="worker/execution", **fields):
    previous = getattr(exc, "clusterfork_failure", None)
    if previous:
        exc.clusterfork_failure = validate_failure({**CONTEXT.get(), **fields, **previous})
        return exc.clusterfork_failure
    reason = exc.reason if isinstance(exc, URLError) else exc
    kind = ("timeout" if isinstance(reason, TimeoutError) else "http_error" if isinstance(exc, HTTPError)
            else "transport_error" if isinstance(exc, (URLError, ConnectionError)) else "worker_error")
    error_type = type(exc).__name__
    data = {"operation": operation, "kind": kind, "error_type": error_type if error_type in ERROR_TYPES else "Exception",
            **CONTEXT.get(), **fields}
    status = getattr(exc, "code", None) if isinstance(exc, HTTPError) else getattr(exc, "status", None)
    if type(status) is int and 100 <= status <= 599:
        data["http_status"] = status
    locations = []
    for frame in traceback.extract_tb(exc.__traceback__):
        path = frame.filename.replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        if ("/automation/" in path or "/home/site/wwwroot/" in path) and re.fullmatch(r"[a-z_]{1,60}\.py", name):
            locations.append(f"{name}:{frame.lineno}")
    data["locations"] = locations[-8:]
    data = validate_failure(data)
    exc.clusterfork_failure = data
    return data


@contextmanager
def operation(name, *, quiet=False, **fields):
    if not valid_operation(name):
        raise ValueError("Unknown diagnostic operation")
    level = logging.DEBUG if quiet else logging.INFO
    started = time.monotonic()
    record("operation_started", level=level, operation=name, **fields)
    try:
        yield
    except Exception as exc:
        details = failure(exc, name, elapsed_ms=round((time.monotonic() - started) * 1000), **fields)
        record("operation_failed", level=logging.ERROR, **details)
        raise
    else:
        record("operation_completed", level=level, operation=name,
               elapsed_ms=round((time.monotonic() - started) * 1000), **fields)


def http_operation(url, data=None):
    parsed = urlparse(url)
    if parsed.hostname == "api.linear.app" and isinstance(data, dict):
        match = re.search(r"\{\s*(\w+)", data.get("query", ""))
        if match and match[1] in LINEAR_OPERATIONS:
            return "linear/" + match[1]
    if parsed.hostname == "api.github.com":
        path = parsed.path
        for pattern, name in ((r"/graphql", "graphql"), (r"/repos/[^/]+/[^/]+/installation", "installation"),
                              (r"/app/installations/[0-9]+/access_tokens", "access_token"),
                              (r"/repos/[^/]+/[^/]+/commits/.+", "branch"),
                              (r"/repos/[^/]+/[^/]+/pulls", "pulls"),
                              (r"/repos/[^/]+/[^/]+/pulls/[0-9]+", "pull"),
                              (r"/repos/[^/]+/[^/]+/actions/workflows/[^/]+/dispatches", "dispatch"),
                              (r"/repos/[^/]+/[^/]+/actions/.+", "runs")):
            if re.fullmatch(pattern, path):
                return "github/" + name
    return "http/" + ({"api.github.com": "github", "api.linear.app": "linear"}.get(parsed.hostname, "other"))


def observed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with operation(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def failure_summary(value):
    data = validate_failure(value)
    stage = {"initializing": "runner initialization", "selecting_account": "account selection",
             "testing": "repository validation", "publishing": "publication"}.get(data.get("stage"), data.get("stage", "execution"))
    if data.get("phase") == "readiness":
        stage = "readiness review"
    descriptions = {"timeout": "request timed out", "http_error": "request was rejected",
                    "transport_error": "connection failed", "command_failed": "command failed",
                    "lease_lost": "credential or worker lease was lost", "worker_error": "worker operation failed"}
    text = f"Failed during {stage}: {descriptions[data['kind']]} ({data['operation']})"
    details = []
    if "elapsed_ms" in data:
        details.append(f"after {data['elapsed_ms'] / 1000:g}s")
    if "timeout_seconds" in data:
        details.append(f"timeout {data['timeout_seconds']:g}s")
    if "http_status" in data:
        details.append(f"HTTP {data['http_status']}")
    if "exit_code" in data:
        details.append(f"exit {data['exit_code']}")
    if data["kind"] == "worker_error" and data.get("error_type"):
        details.append(data["error_type"])
    if details:
        text += "; " + ", ".join(details)
    return text + "."
