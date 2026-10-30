"""HTTP authentication and durable intake on real local sockets."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"automation"),str(ROOT/"automation/azure")]
from controller.core import Controller
from controller.server import Application,HTTPServer,OperatorServer,http_handler
from controller.store import Database
from worker_core import WorkerError
from diagnostics import CONTEXT


class ControllerServerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)
        database=Database(self.path/"tasks.db")
        self.addCleanup(database.close)
        self.controller=Controller(database)
        self.broker,self.driver=Mock(),Mock()
        self.verify=Mock(side_effect=WorkerError("Invalid GitHub workflow identity",401))
        self.app=Application(self.controller,self.broker,self.driver,webhook_secret="fixture-secret",
                             metadata={"revision":"a"*40},verify=self.verify,clock=lambda:1000)
        self.server=HTTPServer(("127.0.0.1",0),http_handler(self.app))
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url=f"http://127.0.0.1:{self.server.server_port}"

    def request(self,path,data=None,headers=None):
        request=Request(self.url+path,data=data,headers=headers or {})
        return urlopen(request,timeout=5)

    def test_signed_intake_commits_before_ack_and_deduplicates_delivery(self):
        body=json.dumps({"type":"Issue","action":"update","webhookTimestamp":1000000,
                         "data":{"id":"66547614-8ed4-4928-834e-aac76f01afd2"}}).encode()
        signature=hmac.new(b"fixture-secret",body,hashlib.sha256).hexdigest()
        for _ in range(2):
            with self.request("/api/linear",body,{"Linear-Signature":signature}) as response:
                self.assertEqual(response.status,200)
                self.assertEqual(json.load(response),{"status":"queued"})
            self.assertEqual(len(self.controller.inbox()),1)
        self.assertTrue(self.app.wakeup.is_set())
        self.broker.handle.assert_not_called()

    def test_invalid_webhook_does_not_enter_queue(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/linear",b"{}",{"Linear-Signature":"0"*64})
        self.assertEqual(caught.exception.code,401)
        caught.exception.close()
        self.assertEqual(self.controller.inbox(),())

    def test_worker_identity_is_verified_before_any_broker_operation(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/worker/claim",b'{"dispatch_key":"fake"}')
        self.assertEqual(caught.exception.code,401)
        caught.exception.close()
        self.broker.handle.assert_not_called()
        self.verify.side_effect=None
        self.verify.return_value={"verified":"fixture"}
        self.broker.handle.return_value={"running":True}
        with self.request("/api/worker/heartbeat",b'{"dispatch_key":"fake"}',{"Authorization":"Bearer fixture"}) as response:
            self.assertEqual(json.load(response),{"running":True})
            self.assertEqual(response.headers["Cache-Control"],"no-store")
        self.broker.handle.assert_called_once_with("heartbeat",{"verified":"fixture"},{"dispatch_key":"fake"})

    def test_setup_query_is_bounded_and_does_not_change_task_state(self):
        self.app.setup=Mock(return_value={'app_access':True,'workflow_repository':'Owner/Engine'})
        with self.request('/api/health?setup_project_id=fixture') as response:
            self.assertTrue(json.load(response)['repository_setup']['app_access'])
        self.app.setup.assert_called_once_with('fixture')
        self.assertEqual(self.controller.tasks(),())
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/health?setup_project_id=one&setup_project_id=two')
        self.assertEqual(caught.exception.code,400)
        caught.exception.close()

    def test_worker_log_context_uses_verified_run_identity_and_is_cleared(self):
        previous=dict(CONTEXT.get())
        self.verify.side_effect=None
        self.verify.return_value={'run_id':'123','run_attempt':'1'}
        self.broker.handle.side_effect=lambda *_args:dict(CONTEXT.get())
        status,result=self.app.handle('POST','/api/worker/heartbeat',{},json.dumps({
            'dispatch_key':'a'*64,'run_id':'999','run_attempt':'2',
            'request_id':'12345678-1234-1234-1234-123456789abc'}).encode())
        self.assertEqual(status,200)
        self.assertEqual(result['run_id'],'123')
        self.assertEqual(result['run_attempt'],'1')
        self.assertEqual(CONTEXT.get(),previous)

    def test_operator_commands_use_private_socket_and_cannot_clear_another_hold(self):
        path=self.path/"operator.sock"
        operator=OperatorServer(path,self.app)
        threading.Thread(target=operator.serve_forever,daemon=True).start()
        self.addCleanup(operator.server_close)
        self.addCleanup(operator.shutdown)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        def command(operation,owner):
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(path))
                client.sendall(json.dumps({"operation":operation,"owner":owner,"reason":"fixture"}).encode()+b"\n")
                return json.loads(client.makefile("rb").readline())
        self.assertTrue(command("pause","operator:rebuild")["ok"])
        self.assertTrue(command("pause","deployment:123")["ok"])
        result=command("resume","deployment:123")
        self.assertEqual(result["result"]["holds"],{"operator:rebuild":"fixture"})
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/operator/resume",b"{}")
        self.assertEqual(caught.exception.code,404)
        caught.exception.close()


if __name__=="__main__":
    unittest.main()
