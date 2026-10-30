"""HTTP and local operator transports. Only validated commands cross into tasks."""

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import socketserver
import threading
import time

from .model import Hold, InboxQueued, Rejected, identity
from dispatch_core import WebhookError, webhook_event
from worker_core import WorkerError
from worker_store import verify_oidc


class Application:
    def __init__(self, controller, broker, driver, *, webhook_secret, metadata, verify=verify_oidc, clock=time.time):
        self.controller, self.broker, self.driver = controller, broker, driver
        self.webhook_secret, self.metadata = webhook_secret, metadata
        self.verify, self.clock = verify, clock
        self.wakeup = threading.Event()

    def handle(self, method, path, headers, body):
        if method == "GET" and path == "/api/health":
            return 200,{"mode":"single-controller","deployment":self.metadata,
                        "holds":list(self.controller.holds()),"active_tasks":len(self.controller.active()),
                        "serial_admission":True,"phase_isolation":True}
        if method != "POST":
            return 404,{"error":"Unknown operation"}
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
            result = self.broker.handle(path.removeprefix("/api/worker/"),claims,data)
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

        def dispatch(self):
            self.connection.settimeout(25)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0<=length<=1024*1024:
                    status,result=413,{"error":"Request is too large"}
                else:
                    body=self.rfile.read(length)
                    status,result=application.handle(self.command,self.path,self.headers,body)
            except WebhookError as exc:
                status,result=exc.status,{"error":str(exc)}
            except WorkerError as exc:
                status,result=exc.status,{"error":str(exc)}
            except Rejected as exc:
                status,result=403,{"error":str(exc)}
            except (ValueError,TypeError,KeyError):
                status,result=400,{"error":"Invalid request"}
            except Exception as exc:
                logging.warning("Controller request deferred (%s)",type(exc).__name__)
                status,result=503,{"error":"Controller unavailable"}
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

    def __init__(self,address,handler,*,max_workers=32):
        self.slots=threading.BoundedSemaphore(max_workers)
        super().__init__(address,handler)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
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
                            "holds":application.controller.holds(),"tasks":[asdict(v) for v in application.controller.active()]}
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
