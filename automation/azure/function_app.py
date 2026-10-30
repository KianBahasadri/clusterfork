"""Shared Linear webhook receiver and queued GitHub routing pilot."""

import hashlib
import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path

import azure.functions as func

from dispatch_clients import GitHub, Linear
from dispatch_core import Dispatcher, Registry, WebhookError, webhook_event
from worker_core import Accounts, WorkerError, WorkerService
from worker_store import AzureTable, verify_oidc
from linear_sync import LinearSync
from repository_queue import RepositoryQueue
from readiness import ReadinessGate
from deployment_control import DeploymentControl, build_metadata
from steering import Steering
from routing_timing import record, timestamps
from diagnostics import ACTIONS, context, failure, observed, operation, record as diagnostic_record

# Azure SDK Information logs include HTTP headers and overwhelm operational records.
# Keep SDK warnings/errors; our explicit timings describe the application operations.
logging.getLogger("azure").setLevel(logging.WARNING)

app = func.FunctionApp()
QUEUE = "clusterfork-events"


@lru_cache(maxsize=1)
def registry():
    return Registry.load(Path(__file__).with_name("projects.json"))


@lru_cache(maxsize=1)
def queue_client():
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    return QueueClient.from_connection_string(
        os.environ["AzureWebJobsStorage"], QUEUE,
        message_encode_policy=TextBase64EncodePolicy(), connection_timeout=2, read_timeout=2,
        retry_total=0)


def enqueue(event, *, visibility_timeout=0):
    event = {**event, "enqueued_at": time.time()}
    queue_client().send_message(json.dumps(event), **({"visibility_timeout": visibility_timeout}
                                                   if visibility_timeout else {}))
    record("event_enqueued", event_type=event["type"], entity_id=event["id"], **timestamps(event))


def dispatcher():
    service = worker_service()
    return Dispatcher(registry(), service.linear, service.queue, steering=service.steering)


@lru_cache(maxsize=1)
def worker_service():
    connection = os.environ["AzureWebJobsStorage"]
    receipts = AzureTable(connection, "ClusterforkDispatches", "pilot")
    runs = AzureTable(connection, "ClusterforkWorkers", "worker")
    linear = Linear(os.environ.get("LINEAR_API_KEY"),
                    client_id=os.environ.get("LINEAR_OAUTH_CLIENT_ID"),
                    client_secret=os.environ.get("LINEAR_OAUTH_CLIENT_SECRET"))
    github = GitHub(os.environ["GITHUB_APP_ID"], os.environ["GITHUB_APP_PRIVATE_KEY"])
    accounts = Accounts(AzureTable(connection, "ClusterforkAccounts", "codex"))
    readiness = ReadinessGate(registry(), AzureTable(connection, "ClusterforkReadiness", "readiness"),
                              accounts, linear, github)
    sync = LinearSync(registry(), receipts, runs,
                      AzureTable(connection, "ClusterforkLinearSync", "linear"), linear, github)
    queue = RepositoryQueue(registry(), receipts, runs,
                            lambda repository: AzureTable(connection, "ClusterforkRepositoryQueue",
                                                          hashlib.sha256(repository.encode()).hexdigest()),
                            linear, github, readiness,
                            control=DeploymentControl(AzureTable(connection, "ClusterforkOperations", "control")))
    steering = Steering(registry(), receipts, runs,
                        AzureTable(connection, "ClusterforkInstructions", "instructions"),
                        linear, queue)
    return WorkerService(
        registry(), receipts, runs,
        accounts,
        linear, github, queue, sync=sync, steering=steering)


@app.route(route="worker/{action}", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def worker(req: func.HttpRequest) -> func.HttpResponse:
    try:
        claims = verify_oidc(req.headers.get("Authorization", ""))
        if len(req.get_body()) > 128_000:
            raise WorkerError("Worker request is too large", 413)
        data = json.loads(req.get_body())
        if not isinstance(data, dict):
            raise WorkerError("Invalid worker request", 400)
        action = req.route_params["action"]
        with context(dispatch_key=data.get("dispatch_key"), run_id=claims.get("run_id"),
                     run_attempt=claims.get("run_attempt"), request_id=data.get("request_id"), stage=data.get("stage")):
            with operation(f"broker/{action}" if action in ACTIONS else "azure/worker",
                           quiet=action in {"heartbeat", "save", "instructions"}):
                result = worker_service().handle(action, claims, data)
        return func.HttpResponse(json.dumps(result), mimetype="application/json",
                                 headers={"Cache-Control": "no-store"})
    except WorkerError as exc:
        diagnostic_record("broker_rejected", level=logging.WARNING, **failure(exc, "azure/worker"))
        return func.HttpResponse(str(exc), status_code=exc.status)
    except (ValueError, TypeError, KeyError):
        return func.HttpResponse("Invalid worker request", status_code=400)
    except Exception as exc:
        diagnostic_record("broker_unavailable", level=logging.ERROR, **failure(exc, "azure/worker"))
        return func.HttpResponse("Worker broker unavailable", status_code=503)


def missing_config():
    missing = [name for name in ("LINEAR_WEBHOOK_SECRET", "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY")
               if not os.environ.get(name)]
    if not (os.environ.get("LINEAR_API_KEY") or (os.environ.get("LINEAR_OAUTH_CLIENT_ID")
                                                and os.environ.get("LINEAR_OAUTH_CLIENT_SECRET"))):
        missing.insert(0, "LINEAR_API_KEY or (LINEAR_OAUTH_CLIENT_ID and LINEAR_OAUTH_CLIENT_SECRET)")
    return missing


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@observed("azure/health")
def health(req: func.HttpRequest) -> func.HttpResponse:
    missing = missing_config()
    mode = "codex-worker" if any(route.workflow == "clusterfork-worker.yml" for route in registry().routes.values()) else "routing-pilot"
    result = {"mode": mode, "missing": missing, "serial_admission": True,
              "projects": len(registry().routes), "diagnostics_version": 1,
              "monitoring_enabled": bool(os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"))}
    try:
        result.update(deployment=build_metadata(),
                      deployment_paused=worker_service().queue.control.is_paused() if not missing else None)
    except Exception as exc:
        # Public health diagnostics reveal the error class, never credentials or response bodies.
        result["runtime_error"] = type(exc).__name__
    return func.HttpResponse(json.dumps(result),
                             status_code=503 if missing or "runtime_error" in result else 200,
                             mimetype="application/json")


@app.route(route="linear", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
@observed("azure/webhook")
def linear_webhook(req: func.HttpRequest) -> func.HttpResponse:
    received_at = time.time()
    try:
        event = webhook_event(req.get_body(), req.headers, os.environ.get("LINEAR_WEBHOOK_SECRET"))
    except WebhookError as exc:
        return func.HttpResponse(str(exc), status_code=exc.status)
    if event:
        event["received_at"] = received_at
        record("webhook_received", at=received_at, event_type=event["type"], entity_id=event["id"],
               **timestamps(event))
        try:
            enqueue(event)
        except Exception:
            # Return failure so Linear retries; no uncommitted success response.
            logging.error("Unable to queue verified Linear event")
            return func.HttpResponse("queue unavailable", status_code=503)
    return func.HttpResponse("queued" if event else "ignored", status_code=200)


@app.queue_trigger(arg_name="message", queue_name=QUEUE, connection="AzureWebJobsStorage")
@observed("azure/route_event")
def route_event(message: func.QueueMessage):
    event = json.loads(message.get_body())
    event["processing_at"] = time.time()
    record("event_processing", at=event["processing_at"], event_type=event["type"], entity_id=event["id"],
           **timestamps(event))
    if event.get("type") == "DeploymentResume":
        deployment_recovery(event)
        return
    results = dispatcher().process(event)
    # No issue descriptions, webhook bodies, API responses, or credentials in logs.
    for result in results:
        logging.info("Clusterfork routing: %s", json.dumps(result))


def deployment_recovery(event):
    service = worker_service()
    state, _ = service.queue.control.state()
    if state.get("owner") != event.get("id") or state.get("revision") != event.get("revision"):
        return  # A newer deployment owns recovery; old wake-ups cannot resume it.
    if state.get("paused") is not False:
        # The notification is durable before explicit resume. Azure retries this
        # small race using the existing queue policy; it never admits work.
        raise WorkerError("Deployment has not explicitly resumed", 503, reason_code="deployment_paused")
    pending = service.queue.readiness.reconcile(deployment_only=True)
    service.queue.reconcile()
    if pending or service.queue.deployment_pending():
        # Run discovery can lag, or a prior attempt can still be stopping. Keep
        # that wait off GitHub runners and independent of the 15-minute timer.
        enqueue(event, visibility_timeout=15)


@app.timer_trigger(arg_name="timer", schedule="0 */15 * * * *", run_on_startup=False,
                   use_monitor=True)
@observed("azure/reconcile")
def reconcile(timer: func.TimerRequest):
    # One shared reconciliation timer catches missed webhooks and newly enrolled
    # projects. Durable receipts make repeat scans harmless for this pilot.
    if missing_config():
        return
    worker_service().queue.readiness.reconcile()
    worker_service().sync.reconcile()
    worker_service().queue.reconcile()
    worker_service().steering.reconcile()
    for project_id in registry().routes:
        enqueue({"type": "Project", "id": project_id})
