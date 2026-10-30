#!/usr/bin/env python3
"""Package/verify the stateless ingress at the existing Linear webhook URL."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen
import zipfile

ROOT=Path(__file__).resolve().parents[2]
FILES=('function_app.py','host.json','requirements.txt')
HEALTH='https://clusterfork-dispatch-7604cac329.azurewebsites.net/api/health'
APP='clusterfork-dispatch-7604cac329'
GROUP='rg-clusterfork-automation'
LEGACY_FUNCTIONS=('reconcile','route_event','linear_webhook','worker')


def package(revision,destination):
    if not re.fullmatch('[0-9a-f]{40}',revision):
        raise ValueError('A full source revision is required')
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        def write(name,source):
            member=zipfile.ZipInfo(name)
            member.create_system=3
            member.external_attr=0o100644 << 16
            member.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(member,source)
        for name in FILES:
            source=subprocess.run(['git','show',f'{revision}:automation/webhook_relay/{name}'],cwd=ROOT,capture_output=True,check=True).stdout
            write(name,source)
        write('deployment.json',json.dumps({'revision':revision}))


def prepare():
    result=subprocess.run(['az','functionapp','config','appsettings','list','--resource-group',GROUP,'--name',APP,
                           '--query',"[?starts_with(name, 'AzureWebJobs.')].{name:name,value:value}",
                           '--output','json','--only-show-errors'],capture_output=True,text=True,check=True)
    flags={row['name']:row['value'] for row in json.loads(result.stdout)}
    if any(str(flags.get(f'AzureWebJobs.{name}.Disabled','')).lower()!='true' for name in LEGACY_FUNCTIONS):
        raise RuntimeError('Legacy lifecycle triggers must be disabled before activating retained ingress')
    subprocess.run(['az','functionapp','start','--resource-group',GROUP,'--name',APP,'--only-show-errors'],
                   capture_output=True,text=True,check=True)
    print('Legacy lifecycle triggers are fenced; retained ingress host is active.')


def verify(revision):
    deadline=time.monotonic()+300
    while True:
        try:
            with urlopen(HEALTH,timeout=15) as response:
                observed=json.load(response)
            if observed.get('mode')=='webhook-relay' and observed.get('deployment')=={'revision':revision}:
                print('Verified stateless forwarding at the existing Linear webhook URL.')
                return
        except Exception:
            pass
        if time.monotonic()>=deadline:
            raise RuntimeError('The expected stateless ingress did not become healthy')
        print('Waiting for the stateless webhook ingress.',flush=True)
        time.sleep(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('package','prepare','verify'))
    parser.add_argument('--revision',required=True)
    parser.add_argument('--output',type=Path,default=Path('/tmp/clusterfork-webhook.zip'))
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.revision):
        parser.error('A full source revision is required')
    if args.action=='package':
        package(args.revision,args.output)
    elif args.action=='prepare':
        prepare()
    else:
        verify(args.revision)
