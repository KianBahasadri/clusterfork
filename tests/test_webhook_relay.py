"""The retained ingress acknowledges only controller acceptance and has no workers."""

from contextlib import nullcontext
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError,URLError
import zipfile

import azure.functions as func

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
relay=load('controller_webhook_ingress',ROOT/'automation/webhook_relay/function_app.py')
release=load('controller_webhook_ingress_release',ROOT/'automation/webhook_relay/release.py')


class WebhookRelayTests(unittest.TestCase):
    def request(self,body=b'{"original":"bytes"}'):
        return func.HttpRequest('POST','https://existing.example/api/linear',body=body,headers={'Linear-Signature':'a'*64})

    def test_forwarding_preserves_raw_signed_bytes_and_waits_for_acceptance(self):
        request=self.request()
        with patch.object(relay,'urlopen',return_value=nullcontext(SimpleNamespace(status=200))) as send:
            response=relay.linear_relay(request)
        self.assertEqual(response.status_code,200)
        forwarded=send.call_args.args[0]
        self.assertEqual(forwarded.full_url,relay.TARGET)
        self.assertEqual(forwarded.data,request.get_body())
        self.assertEqual(forwarded.get_header('Linear-signature'),'a'*64)
        self.assertEqual(send.call_args.kwargs['timeout'],20)

    def test_rejections_and_unavailable_controller_are_never_acknowledged(self):
        for error,status in [(URLError('private-transport-detail'),503),
                             (HTTPError(relay.TARGET,401,'private-detail',{},io.BytesIO(b'private-response')),401),
                             (HTTPError(relay.TARGET,503,'private-detail',{},io.BytesIO(b'private-response')),503)]:
            with patch.object(relay,'urlopen',side_effect=error):
                response=relay.linear_relay(self.request())
            self.assertEqual(response.status_code,status)
            self.assertNotIn(b'private-',response.get_body())
        with patch.object(relay,'urlopen') as send:
            self.assertEqual(relay.linear_relay(self.request(b'x'*(1024*1024+1))).status_code,413)
        send.assert_not_called()

    def test_only_stateless_http_ingress_and_metadata_health_are_registered(self):
        self.assertEqual({f.get_function_name() for f in relay.app.get_functions()},{'linear_relay','health'})
        metadata=json.loads(relay.health(func.HttpRequest('GET','https://existing.example/api/health',body=b'')).get_body())
        self.assertEqual(metadata['mode'],'webhook-relay')
        self.assertEqual(metadata['target'],relay.TARGET)

    def test_activating_retained_host_requires_every_legacy_lifecycle_trigger_to_be_disabled(self):
        flags=[{'name':f'AzureWebJobs.{name}.Disabled','value':'true'} for name in release.LEGACY_FUNCTIONS]
        with patch.object(release.subprocess,'run',side_effect=[SimpleNamespace(stdout=json.dumps(flags)),SimpleNamespace()]) as run:
            release.prepare()
        self.assertEqual(run.call_args_list[-1].args[0][1:3],['functionapp','start'])
        for omitted in release.LEGACY_FUNCTIONS:
            missing=[row for row in flags if row['name']!=f'AzureWebJobs.{omitted}.Disabled']
            with patch.object(release.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(missing))) as run:
                with self.assertRaises(RuntimeError):
                    release.prepare()
            self.assertEqual(run.call_count,1)

    def test_package_contains_exact_committed_ingress_and_no_legacy_controller_or_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def git(*args):
                return subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
            git('init','-q')
            git('config','user.email','fixture@example.test')
            git('config','user.name','Fixture')
            source=root/'automation/webhook_relay'
            source.mkdir(parents=True)
            for name in release.FILES:
                (source/name).write_text('committed '+name)
            git('add','.')
            git('commit','-qm','Fixture')
            revision=git('rev-parse','HEAD')
            (source/'.env').write_text('private-fixture-secret')
            (source/'function_app.py').write_text('uncommitted replacement')
            destination=root/'relay.zip'
            with patch.object(release,'ROOT',root):
                release.package(revision,destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()),set(release.FILES)|{'deployment.json'})
                self.assertEqual(archive.read('function_app.py'),b'committed function_app.py')
                self.assertEqual(json.loads(archive.read('deployment.json')),{'revision':revision})
