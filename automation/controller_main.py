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

sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parent/"azure")]
from controller.broker import Broker
from controller.core import Controller
from controller.driver import Driver
from controller.instructions import Instructions
from controller.model import Initialize
from controller.release import RequiredRuntime
from controller.server import Application,HTTPServer,OperatorServer,http_handler
from controller.store import Database
from dispatch_clients import GitHub,Linear
from dispatch_core import Registry
from worker_core import Accounts,ProviderAccounts
from worker_store import AzureTable


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


def build(config,manifest,database,credential,*,table_factory=None):
    if table_factory is None:
        from azure.data.tables import TableClient
        table_factory=TableClient
    registry=Registry.load(Path(__file__).parent/"azure/projects.json")
    vault=config["key_vault"]
    linear=(Linear(None,client_id=config["linear_oauth_client_id"],
                   client_secret=secret(credential,vault,"linear-oauth-client-secret"))
            if config.get("linear_oauth_client_id") else Linear(secret(credential,vault,"linear-api-key")))
    github=GitHub(config["github_app_id"],secret(credential,vault,"github-app-private-key"))
    def table(name,partition):
        client=table_factory(f"https://{config['storage_account']}.table.core.windows.net",name,credential=credential)
        return AzureTable.with_client(client,partition)
    accounts=ProviderAccounts({provider:Accounts(table("ClusterforkAccounts",provider),provider=provider)
                               for provider in ("codex","cursor")})
    controller=Controller(database)
    controller.apply(Initialize(),now=time.time())
    instructions=Instructions(controller,registry,table("ClusterforkInstructions","instructions"),linear,clock=time.time)
    driver=Driver(controller,registry,linear,github,manifest["revision"],
                  runtime_ready=RequiredRuntime(github,manifest),steering=instructions)
    broker=Broker(controller,registry,accounts,linear,github,refresh=driver.refresh,steering=instructions)
    return Application(controller,broker,driver,webhook_secret=secret(credential,vault,"linear-webhook-secret"),metadata=manifest)


def serve(args):
    from azure.identity import ManagedIdentityCredential
    config=json.loads(args.config.read_text())
    manifest=json.loads((Path(__file__).resolve().parents[1]/"release.json").read_text())
    verify_host(config["vm_resource_id"])
    stop=threading.Event()
    with Database(args.database) as database:
        application=build(config,manifest,database,ManagedIdentityCredential())
        http=HTTPServer(("127.0.0.1",args.port),http_handler(application))
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("serve","inspect","pause","resume","reconcile"))
    parser.add_argument("--config",type=Path,default=Path("/etc/clusterfork/controller.json"))
    parser.add_argument("--database",type=Path,default=Path("/var/lib/clusterfork/tasks.db"))
    parser.add_argument("--socket",type=Path,default=Path("/run/clusterfork/operator.sock"))
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--owner",default="operator:rebuild")
    parser.add_argument("--reason",default="")
    parser.add_argument("--task-id")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    if args.action=="serve":
        serve(args)
        return
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(str(args.socket))
        client.sendall(json.dumps({"operation":args.action,"owner":args.owner,"reason":args.reason,"task_id":args.task_id}).encode()+b"\n")
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
