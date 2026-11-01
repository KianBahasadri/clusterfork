"""HTTP and local operator transports. Only validated commands cross into tasks."""

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import socketserver
import threading
import time
from urllib.parse import parse_qs,urlsplit

from .model import Hold, InboxQueued, Rejected, identity
from .deployment import verify_release
from common.routing import WebhookError, webhook_event
from common.accounts import WorkerError
from common.identity import verify_oidc
from common.diagnostics import context,operation
from .telemetry import HTTP_CONTEXT,http_operation,http_failure


class Application:
    def __init__(self, controller, broker, driver, *, webhook_secret, metadata, verify=verify_oidc, clock=time.time,
                 resources_idle=lambda: True,capabilities=None,setup=None,account_operator=None,telemetry=None):
        self.controller, self.broker, self.driver = controller, broker, driver
        self.webhook_secret, self.metadata = webhook_secret, metadata
        self.verify, self.clock = verify, clock
        self.resources_idle = resources_idle
        self.capabilities,self.setup=capabilities or {},setup
        self.account_operator=account_operator
        self.telemetry=telemetry
        self.wakeup = threading.Event()

    def drained(self):
        active = self.controller.active()
        active_ids = {view.task_id for view in active}
        if any(effect['task_id'] in active_ids for kind in ('publication', 'merge') for effect in self.controller.effects(kind)):
            return False
        for view in active:
            attempt=view.attempt
            if (not attempt.get("stopped") or not attempt.get("all_stopped")
                    or not attempt.get("publication_checked") or attempt.get("pull_state") not in {"absent","closed"}):
                return False
        return bool(self.resources_idle())

    def handle(self, method, path, headers, body):
        if method == 'GET' and urlsplit(path).path == '/api/accounts/snapshot':
            if self.account_operator is None:
                raise WorkerError('Account operator access is unavailable',401)
            self.account_operator(headers.get('Authorization',''))
            query=parse_qs(urlsplit(path).query,keep_blank_values=True)
            if set(query)!={'provider'} or len(query['provider'])!=1 or query['provider'][0] not in {'codex','cursor'}:
                return 400,{'error':'Invalid account snapshot query'}
            from .account_usage import snapshot
            return 200,snapshot(self.controller.accounts,query['provider'][0],now=self.clock())
        if method == "GET" and urlsplit(path).path == "/api/health":
            result={**self.capabilities,"mode":"single-controller","deployment":self.metadata,
                        "holds":list(self.controller.holds()),"active_tasks":len(self.controller.active()),
                        "deployment_paused":bool(self.controller.holds()),"serial_admission":True,"phase_isolation":True}
            query=parse_qs(urlsplit(path).query,keep_blank_values=True)
            if query:
                if set(query)!={'setup_project_id'} or len(query['setup_project_id'])!=1 or self.setup is None:
                    return 400,{'error':'Invalid setup query'}
                result['repository_setup']=self.setup(query['setup_project_id'][0])
            return 200,result
        if method != "POST":
            return 404,{"error":"Unknown operation"}
        if path=='/api/accounts':
            if self.account_operator is None:
                raise WorkerError('Account operator access is unavailable',401)
            operator=self.account_operator(headers.get('Authorization',''))
            from .account_operator import execute
            data=json.loads(body)
            if not isinstance(data,dict):
                return 400,{'error':'Invalid account request'}
            result=execute(self.controller,operator,data)
            self.wakeup.set()
            return 200,result
        if path == "/api/linear":
            event = webhook_event(body, headers, self.webhook_secret, now=self.clock())
            if event:
                self.controller.apply(InboxQueued(identity(event), event),now=self.clock())
                self.wakeup.set()
            return 200,{"status":"queued" if event else "ignored"}
        if path.startswith("/api/worker/"):
            if len(body)>128000:
                return 413,{"error":"Worker request is too large"}
            claims = self.verify(headers.get("Authorization", ""))
            data = json.loads(body)
            if not isinstance(data,dict):
                return 400,{"error":"Invalid worker request"}
            action=path.removeprefix('/api/worker/')
            with context(dispatch_key=data.get('dispatch_key'),run_id=str(claims.get('run_id','')),
                         run_attempt=str(claims.get('run_attempt','')),request_id=data.get('request_id')):
                with operation('azure/worker',quiet=action in {'heartbeat','save','instructions'}):
                    result = self.broker.handle(action,claims,data)
            self.wakeup.set()
            return 200,result
        return 404,{"error":"Unknown operation"}


def http_handler(application):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def log_message(self, format, *args):
            pass  # Request paths/headers may contain bearer material; emit bounded operations only.

        def send_error(self, code, message=None, explain=None):
            started=time.monotonic()
            try:
                super().send_error(code,message,explain)
            finally:
                if application.telemetry is not None:
                    try:
                        application.telemetry.request('controller/unknown',code,(time.monotonic()-started)*1000)
                    except Exception:
                        pass

        def dispatch(self):
            started=time.monotonic()
            ids={}
            token=HTTP_CONTEXT.set(ids)
            self.telemetry_status,self.telemetry_failure=503,None
            try:
                self.respond()
            finally:
                HTTP_CONTEXT.reset(token)
                if application.telemetry is not None:
                    try:
                        application.telemetry.request(http_operation(self.command,self.path),self.telemetry_status,
                                                      (time.monotonic()-started)*1000,ids,self.telemetry_failure)
                    except Exception:
                        pass  # Diagnostics cannot change a worker response.

        def respond(self):
            self.connection.settimeout(25)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0<=length<=1024*1024:
                    status,result=413,{"error":"Request is too large"}
                else:
                    body=self.rfile.read(length)
                    status,result=application.handle(self.command,self.path,self.headers,body)
            except WebhookError as exc:
                self.telemetry_failure=http_failure(exc,http_operation(self.command,self.path))
                status,result=exc.status,{"error":str(exc)}
            except WorkerError as exc:
                self.telemetry_failure=http_failure(exc,http_operation(self.command,self.path))
                status,result=exc.status,{"error":str(exc)}
                if exc.reason_code:
                    result['reason_code']=exc.reason_code
            except Rejected as exc:
                self.telemetry_failure=http_failure(exc,http_operation(self.command,self.path))
                status,result=403,{"error":str(exc)}
            except (ValueError,TypeError,KeyError) as exc:
                self.telemetry_failure=http_failure(exc,http_operation(self.command,self.path))
                status,result=400,{"error":"Invalid request"}
            except Exception as exc:
                self.telemetry_failure=http_failure(exc,http_operation(self.command,self.path))
                logging.warning("Controller request deferred (%s)",type(exc).__name__)
                status,result=503,{"error":"Controller unavailable"}
            self.telemetry_status=status
            data=json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(data)
    return Handler


class HTTPServer(ThreadingHTTPServer):
    daemon_threads=True

    def __init__(self,address,handler,*,max_workers=32,telemetry=None):
        self.slots=threading.BoundedSemaphore(max_workers)
        self.telemetry=telemetry
        super().__init__(address,handler)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            if self.telemetry is not None:
                try:
                    self.telemetry.request('controller/overload',503,0)
                except Exception:
                    pass
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self,request,address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()


class OperatorServer(socketserver.ThreadingUnixStreamServer):
    """The root/controller-user-only socket is never exposed through public HTTP."""
    daemon_threads=True

    def __init__(self,path,application):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if path.exists():
            path.unlink()  # Database owner lock has already excluded another controller.
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    command=json.loads(self.rfile.readline(16001))
                    operation=command.get("operation")
                    if operation in {"pause","resume"}:
                        owner=command.get("owner", "operator:local")
                        if not owner.startswith(("operator:","deployment:")):
                            raise Rejected("Invalid operator hold owner")
                        application.controller.apply(Hold(owner,command.get("reason", ""),release=operation=="resume"),now=application.clock())
                        result={"holds":application.controller.holds()}
                    elif operation=="inspect":
                        result=asdict(application.controller.inspect(command["task_id"])) if command.get("task_id") else {
                            "holds":application.controller.holds(),"tasks":[asdict(v) for v in application.controller.active()],
                            "drained":application.drained(),"deployment":application.metadata}
                    elif operation=="deployment-status":
                        result={"drained":application.drained(),"deployment":application.metadata,
                                "active_tasks":len(application.controller.active()),"holds":application.controller.holds()}
                    elif operation=="verify-release":
                        result=verify_release(application,command.get("revision"))
                    elif operation=="reconcile":
                        application.wakeup.set()
                        result={"queued":True}
                    else:
                        raise Rejected("Unknown operator operation")
                    self.wfile.write(json.dumps({"ok":True,"result":result}).encode()+b"\n")
                except Exception as exc:
                    self.wfile.write(json.dumps({"ok":False,"error":type(exc).__name__}).encode()+b"\n")
        super().__init__(str(path),Handler)
        path.chmod(0o600)
