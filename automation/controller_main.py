#!/usr/bin/env python3
"""Run the single controller, or use its private local operator socket."""

import argparse
import json
import logging
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from urllib.request import Request,urlopen

sys.path.insert(0,str(Path(__file__).parent))
from controller.broker import Broker
from controller.core import Controller
from controller.driver import Driver
from controller.instructions import Instructions
from controller.model import Initialize
from controller.release import RequiredRuntime
from controller.server import Application,HTTPServer,OperatorServer,http_handler
from controller.setup import evidence
from controller.store import Database
from controller.telemetry import Telemetry
from common.clients import GitHub,Linear
from common.routing import Registry
from common.policy import PROTOCOL,digest
from common.identity import verify_operator


def secret(credential,vault,name):
    token=credential.get_token("https://vault.azure.net/.default").token
    request=Request(f"https://{vault}.vault.azure.net/secrets/{name}?api-version=7.4",
                    headers={"Authorization":"Bearer "+token})
    with urlopen(request,timeout=20) as response:
        return json.load(response)["value"]


def verify_host(expected):
    request=Request("http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01",
                    headers={"Metadata":"true"})
    with urlopen(request,timeout=5) as response:
        observed=json.load(response)["resourceId"]
    if not isinstance(expected,str) or observed.lower()!=expected.lower():
        raise ValueError("Controller host identity does not match configuration")


def build(config,manifest,database,credential):
    registry=Registry.load(Path(__file__).parent/"controller/projects.json")
    vault=config["key_vault"]
    linear=(Linear(None,client_id=config["linear_oauth_client_id"],
                   client_secret=secret(credential,vault,"linear-oauth-client-secret"))
            if config.get("linear_oauth_client_id") else Linear(secret(credential,vault,"linear-api-key")))
    github=GitHub(config["github_app_id"],secret(credential,vault,"github-app-private-key"))
    controller=Controller(database)
    controller.apply(Initialize(),now=time.time())
    instructions=Instructions(controller,registry,linear,clock=time.time)
    required=RequiredRuntime(github,manifest,deployment_run_id=config.get('deployment_run_id'))
    driver=Driver(controller,registry,linear,github,manifest["revision"],
                  runtime_ready=required,runtime_source_ready=required.source_matches,steering=instructions)
    broker=Broker(controller,registry,linear,github,refresh=driver.refresh,steering=instructions)
    telemetry=None
    if config.get('telemetry'):
        try:
            telemetry=Telemetry(database,credential,config['telemetry'],manifest['revision'])
        except Exception:
            logging.warning('Controller telemetry initialization deferred')
    return Application(controller,broker,driver,webhook_secret=secret(credential,vault,"linear-webhook-secret"),telemetry=telemetry,
                       metadata=manifest,resources_idle=controller.accounts.idle,
                       capabilities={'protocol':PROTOCOL,'registry_digest':digest(json.loads((Path(__file__).parent/'controller/projects.json').read_text())),
                                     'missing':[],'resource_backend':'sqlite'},setup=lambda project:evidence(registry,github,manifest,project),
                       account_operator=lambda authorization:verify_operator(authorization,config.get('azure_tenant_id'),config.get('account_operators',[])))


def serve(args):
    from azure.identity import ManagedIdentityCredential
    config=json.loads(args.config.read_text())
    manifest=json.loads((Path(__file__).resolve().parents[1]/"release.json").read_text())
    verify_host(config["vm_resource_id"])
    stop=threading.Event()
    with Database(args.database) as database:
        application=build(config,manifest,database,ManagedIdentityCredential())
        http=HTTPServer(("127.0.0.1",args.port),http_handler(application),telemetry=application.telemetry)
        operator=OperatorServer(args.socket,application)
        def loop():
            last_scan=0
            while not stop.is_set():
                try:
                    scan=time.monotonic()-last_scan>=120
                    application.driver.tick(scan=scan)
                    if scan:
                        last_scan=time.monotonic()
                except Exception as exc:
                    logging.warning("Controller observation cycle deferred (%s)",type(exc).__name__)
                application.wakeup.wait(30)
                application.wakeup.clear()
        threads=[threading.Thread(target=target,daemon=True) for target in (http.serve_forever,operator.serve_forever,loop)]
        if application.telemetry is not None:
            threads.insert(0,threading.Thread(target=application.telemetry.run,args=(stop,),daemon=True))
        for thread in threads:
            thread.start()
        def stopping(*_):
            stop.set()
            application.wakeup.set()
        signal.signal(signal.SIGTERM,stopping)
        signal.signal(signal.SIGINT,stopping)
        stop.wait()
        http.shutdown()
        operator.shutdown()
        http.server_close()
        operator.server_close()
        threads[-1].join(timeout=30)
        if application.telemetry is not None:
            threads[0].join(timeout=15)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("serve","inspect","pause","resume","reconcile","deployment-status","verify-release"))
    parser.add_argument("--config",type=Path,default=Path("/etc/clusterfork/controller.json"))
    parser.add_argument("--database",type=Path,default=Path("/var/lib/clusterfork/tasks.db"))
    parser.add_argument("--socket",type=Path,default=Path("/run/clusterfork/operator.sock"))
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--owner",default="operator:rebuild")
    parser.add_argument("--reason",default="")
    parser.add_argument("--task-id")
    parser.add_argument("--revision")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    if args.action=="serve":
        serve(args)
        return
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(str(args.socket))
        client.sendall(json.dumps({"operation":args.action,"owner":args.owner,"reason":args.reason,
                                   "task_id":args.task_id,"revision":args.revision}).encode()+b"\n")
        response=json.loads(client.makefile("rb").readline())
    if not response.get("ok"):
        raise RuntimeError("Operator command was rejected")
    print(json.dumps(response["result"],indent=2))


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print("Controller operation failed ("+type(exc).__name__+").",file=sys.stderr)
        raise SystemExit(1)
