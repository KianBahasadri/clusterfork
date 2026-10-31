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

from common.policy import MAX_FAILURE_OUTPUT_BYTES, redact_review_text

CONTEXT = ContextVar("clusterfork_diagnostics", default={})
STAGES = {"initializing", "selecting_account", "coding", "testing", "publishing", "review", "waiting_for_quota"}
ACTIONS = {"start", "heartbeat", "accounts", "claim", "save", "bind_account", "exhaust_account", "checkout", "github", "progress", "finish",
           "review", "steering_open", "instructions", "instruction_result", "steering_close",
           "validation_start", "validation_heartbeat", "validation_claim", "validation_save", "validation_begin", "validation_finish"}
LINEAR_OPERATIONS = {"issue", "project", "comments", "viewer", "comment", "issueUpdate", "issueLabels",
                     "issueCreate", "issueLabelCreate", "issueAddLabel", "issueRemoveLabel", "commentUpdate", "commentCreate"}
OPERATIONS = {"worker/execution", "worker/heartbeat", "worker/account_release", "worker/final_usage",
              "worker/linear_sync", "worker/steering_sync", "worker/agent", "worker/readiness", "worker/publication_scan",
              "http/linear", "http/github", "http/oidc", "http/other", "azure/worker", "azure/health",
              "azure/route_event", "azure/reconcile", "azure/webhook", "command/git", "command/gh",
              "command/python", "command/codex", "command/other", *("broker/" + name for name in ACTIONS),
              *("linear/" + name for name in LINEAR_OPERATIONS), "github/installation", "github/access_token",
              "github/branch", "github/pulls", "github/pull", "github/dispatch", "github/runs", "github/graphql"}
KINDS = {"timeout", "http_error", "transport_error", "command_failed", "lease_lost", "worker_error", "graphql_error"}
GRAPHQL_CODES = {"FORBIDDEN", "UNAUTHENTICATED", "BAD_USER_INPUT", "GRAPHQL_VALIDATION_FAILED",
                 "INTERNAL_SERVER_ERROR", "RATELIMITED", "RATE_LIMITED"}
# Linear's SDK classifies errors by extensions.type. Never retain free-form messages.
GRAPHQL_TYPES = {"forbidden", "authentication error", "invalid input", "ratelimited", "internal error",
                 "feature not accessible", "network error", "bootstrap error", "unknown", "other",
                 "user error", "graphql error", "lock timeout", "usage limit exceeded"}
GRAPHQL_REASONS = {"errors", "invalid_response", "unsuccessful_mutation"}
ERROR_TYPES = {"Exception", "TimeoutError", "HTTPError", "URLError", "ConnectionError", "OSError",
               "WorkerFailure", "WorkerError", "DeploymentDeferred", "CodexError", "CodexRejected", "RequestError", "DispatchError",
               "PermissionError", "FileNotFoundError", "KeyError", "TypeError", "ValueError", "RuntimeError",
               "JSONDecodeError", "ServiceRequestError", "ServiceResponseError", "HttpResponseError",
               "ResourceModifiedError", "ResourceNotFoundError", "ClientAuthenticationError"}
FAILURE_REASONS = {
    "cursor_profile_unavailable": "Cursor profile grok-4.7-high / high is unavailable; no model was substituted",
    "cursor_authentication_unavailable": "Cursor saved login is invalid, expired, or belongs to another account; unattended CLI login refresh is unavailable",
    "cursor_usage_unknown": "Cursor included API usage is unavailable or unknown; paid spending is not assumed",
    "cursor_execution_failed": "Cursor ACP execution failed; no transcript was logged",
    "cursor_turn_stopped": "Cursor stopped after the task deadline or loss of its credential lease",
    "cursor_readiness_tool_denied": "Cursor readiness requested a tool outside its read-only admission permissions",
    "file_check_failed": "Cursor cloud probe did not verify the requested harmless file creation and read",
    "deployment_paused": "Azure deployment paused new work; waiting for explicit resume and stopped GitHub attempts",
    "deployment_cutover": "Worker broker instance is draining after deployment; retry startup on the current instance",
    "repository_admission_lost": "the issue no longer owns the repository slot",
    "prerequisites_unresolved": "issue prerequisites are unresolved or unverifiable",
    "workflows_permission_missing": "workflow publication requires the GitHub App installation's approved Workflows write permission",
    "publication_credentials_detected": "publication blocked because a credential was detected in changes or PR metadata",
    "publication_scan_failed": "publication blocked because the credential scan could not read an auth cache",
    "git_integrity_changed": "Agent changed the worker's Git history or branch",
}


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
        {"event": event, **CONTEXT.get(), **{k: v for k, v in fields.items() if k != "failure_output"}}, sort_keys=True))


def permits_failure_output(name):
    return name in {"command/git", "command/gh"} or bool(re.fullmatch(r"validation/[1-9][0-9]{0,2}", name))


def validate_failure(value):
    if (not isinstance(value, dict) or not valid_operation(value.get("operation"))
            or not isinstance(value.get("kind"), str) or value["kind"] not in KINDS):
        raise ValueError("Invalid failure diagnostic")
    result = {"operation": value["operation"], "kind": value["kind"], **identifiers(value)}
    if isinstance(value.get("error_type"), str) and value["error_type"] in ERROR_TYPES:
        result["error_type"] = value["error_type"]
    if isinstance(value.get("reason_code"), str) and value["reason_code"] in FAILURE_REASONS:
        result["reason_code"] = value["reason_code"]
    for name, maximum in {"elapsed_ms": 86_400_000, "timeout_seconds": 86_400,
                          "http_status": 599, "exit_code": 255, "graphql_error_count": 1000}.items():
        number = value.get(name)
        if number is not None:
            minimum = -255 if name == "exit_code" else 0
            if type(number) not in (int, float) or not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError("Invalid failure measurement")
            result[name] = number
    for name, allowed in {"graphql_code": GRAPHQL_CODES, "graphql_type": GRAPHQL_TYPES,
                          "graphql_field": LINEAR_OPERATIONS, "graphql_reason": GRAPHQL_REASONS}.items():
        if isinstance(value.get(name), str) and value[name] in allowed:
            result[name] = value[name]
    # Stack locations contain only repository filenames and line numbers, no source or locals.
    locations = value.get("locations", [])
    if not isinstance(locations, list) or len(locations) > 8 or any(
        not isinstance(item, str) or not re.fullmatch(r"[a-z_]{1,60}\.py:[1-9][0-9]{0,5}", item) for item in locations
    ):
        raise ValueError("Invalid failure locations")
    if locations:
        result["locations"] = locations
    if "failure_output" in value:
        output = value["failure_output"]
        if (not isinstance(output, str) or "\x00" in output
                or len(output.encode("utf-8")) > MAX_FAILURE_OUTPUT_BYTES
                or not permits_failure_output(value["operation"])):
            raise ValueError("Invalid private failure output")
        result["failure_output"] = redact_review_text(output).encode("utf-8")[-MAX_FAILURE_OUTPUT_BYTES:].decode("utf-8", errors="ignore")
    return result


def graphql_failure(name, reason, errors=None):
    """Bound GraphQL diagnostics to known classifications and root field names."""
    data = {"operation": name, "kind": "graphql_error", "error_type": "RequestError",
            "graphql_reason": reason}
    if isinstance(errors, list):
        data["graphql_error_count"] = min(len(errors), 1000)
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        extensions = first.get("extensions")
        if isinstance(extensions, dict):
            data.update(graphql_code=extensions.get("code"), graphql_type=extensions.get("type"))
        path = first.get("path")
        if isinstance(path, list) and path:
            data["graphql_field"] = path[0]
    return validate_failure(data)


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
    if getattr(exc, "reason_code", None) in FAILURE_REASONS:
        data["reason_code"] = exc.reason_code
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
        deferred = details.get("reason_code") == "deployment_paused"
        record("operation_deferred" if deferred else "operation_failed",
               level=logging.INFO if deferred else logging.ERROR, **details)
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
                    "lease_lost": "credential or worker lease was lost", "worker_error": "worker operation failed",
                    "graphql_error": "GraphQL operation was rejected"}
    description = FAILURE_REASONS.get(data.get("reason_code"), descriptions[data["kind"]])
    text = f"Failed during {stage}: {description} ({data['operation']})"
    details = []
    if "elapsed_ms" in data:
        details.append(f"after {data['elapsed_ms'] / 1000:g}s")
    if "timeout_seconds" in data:
        details.append(f"timeout {data['timeout_seconds']:g}s")
    if "http_status" in data:
        details.append(f"HTTP {data['http_status']}")
    if "exit_code" in data:
        details.append(f"exit {data['exit_code']}")
    if data.get("graphql_code") or data.get("graphql_type"):
        details.append(data.get("graphql_code") or data["graphql_type"])
    if data["kind"] == "worker_error" and data.get("error_type"):
        details.append(data["error_type"])
    if details:
        text += "; " + ", ".join(details)
    return text + "."
