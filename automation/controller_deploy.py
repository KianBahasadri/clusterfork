#!/usr/bin/env python3
"""Deploy one tested controller release at a verified idle boundary."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import urlopen

sys.path.insert(0,str(Path(__file__).parent))
from controller.release import local_manifest,tree_digest
from controller.workflows import ENGINE
import infrastructure

ROOT=Path(__file__).resolve().parents[1]
GROUP='rg-clusterfork-automation'
VM='clusterfork-controller-7604cac329'
HOLD='deployment:rollout'


def command(args,*,cwd=ROOT):
    result=subprocess.run(args,cwd=cwd,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} operation failed; raw output withheld')
    return result.stdout


def github(path):
    return json.loads(command(['gh','api',f'repos/{ENGINE}/{path}']))


def still_required(manifest):
    head=github('commits/main')
    tree=github('git/trees/'+head['commit']['tree']['sha'])
    if tree.get('truncated'):
        raise RuntimeError('Incomplete required-runtime evidence')
    return tree_digest(tree['tree'])==manifest['digest']


def ci_state(revision):
    runs=github(f'actions/workflows/test.yml/runs?head_sha={revision}&event=push&per_page=100')['workflow_runs']
    matches=[r for r in runs if r['head_sha']==revision and r['head_branch']=='main' and r['event']=='push']
    if not matches:
        return 'pending'
    latest=max(matches,key=lambda r:(r['id'],r['run_attempt']))
    return latest.get('conclusion') if latest['status']=='completed' else 'pending'


def run_host(script):
    result=json.loads(command(['az','vm','run-command','invoke','--resource-group',GROUP,'--name',VM,
                               '--command-id','RunShellScript','--scripts',script,'--output','json','--only-show-errors']))
    return '\n'.join(item.get('message','') for item in result.get('value',[]))


def operator(operation,**fields):
    if operation not in {'deployment-status','pause','resume','verify-release','reconcile'}:
        raise ValueError('Invalid deployment operation')
    payload=base64.b64encode(json.dumps({'operation':operation,**fields}).encode()).decode()
    script="""#!/bin/sh
python3 - <<'PY'
import base64,json,socket
with socket.socket(socket.AF_UNIX) as client:
    client.connect('/run/clusterfork/operator.sock')
    client.sendall(base64.b64decode('PAYLOAD')+b'\\n')
    response=client.makefile('rb').readline()
print('CF_RESULT:'+base64.b64encode(response).decode())
PY
""".replace('PAYLOAD',payload)
    result=run_host(script)
    matches=re.findall(r'CF_RESULT:([A-Za-z0-9+/=]+)',result)
    if len(matches)!=1:
        raise RuntimeError('Controller operator response unavailable')
    response=json.loads(base64.b64decode(matches[0]))
    if response.get('ok') is not True:
        raise RuntimeError('Controller operator operation rejected')
    return response['result']


def install_script(config,manifest):
    script=(ROOT/'automation/controller/host/install.sh').read_text()
    for placeholder,value in [('REVISION',manifest['revision']),
                              ('CONFIG_B64',base64.b64encode(json.dumps(config).encode()).decode()),
                              ('MANIFEST_B64',base64.b64encode(json.dumps(manifest).encode()).decode())]:
        script=script.replace('__'+placeholder+'__',value)
    # The child shell owns errexit and logging. Its success marker is outside the
    # child's redirected log; no command output or credentials are relayed.
    return "#!/bin/sh\nif /bin/bash <<'CF_INSTALL_SCRIPT'\n"+script+"\nCF_INSTALL_SCRIPT\nthen\n echo CF_INSTALL_OK\nelse\n echo CF_INSTALL_FAILED\n exit 1\nfi\n"




def deploy(revision,*,initialize=False):
    manifest=local_manifest(ROOT,revision)
    deadline=time.monotonic()+330*60
    while ci_state(revision)=='pending':
        if not still_required(manifest):
            print('A newer runtime superseded this release.')
            return
        if time.monotonic()>=deadline:
            raise RuntimeError('Timed out waiting for the exact revision CI')
        print('Waiting for the exact revision CI.',flush=True)
        time.sleep(15)
    if ci_state(revision)!='success':
        raise RuntimeError('The exact runtime revision did not pass CI')
    if not still_required(manifest):
        print('A newer runtime superseded this release.')
        return
    if initialize:
        marker=run_host('#!/bin/sh\nif test ! -e /opt/clusterfork/current && ! systemctl is-active --quiet clusterfork-controller; then echo CF_FIRST_INSTALL; fi\n')
        if 'CF_FIRST_INSTALL' not in marker:
            raise RuntimeError('Initial installation cannot bypass an existing controller drain')
    else:
        operator('pause',owner=HOLD,reason='Automatic deployment is waiting for existing work to drain.')
        while not operator('deployment-status')['drained']:
            if not still_required(manifest):
                print('A newer runtime retains the deployment hold.')
                return
            if time.monotonic()>=deadline:
                raise RuntimeError('Workers did not drain; deployment hold retained')
            print('Waiting for task attempts, publication, and account leases to drain.',flush=True)
            time.sleep(15)
    # This is the same Terraform owner/plan gate used by operator reconciliation.
    infrastructure.init(ROOT/'infra/azure')
    infrastructure.plan(ROOT/'infra/azure',apply=True)
    config=json.loads(command(['terraform','output','-json','controller_config'],cwd=ROOT/'infra/azure'))
    if os.environ.get('GITHUB_RUN_ID'):
        config['deployment_run_id']=os.environ['GITHUB_RUN_ID']
    if not still_required(manifest):
        print('A newer runtime superseded this release before installation.')
        return
    output=run_host(install_script(config,manifest))
    if 'CF_INSTALL_OK' not in output or 'CF_INSTALL_FAILED' in output:
        raise RuntimeError('Controller install failed; inspect its private host log')
    deadline=time.monotonic()+300
    while True:
        try:
            with urlopen(f"https://{config['hostname']}/api/health",timeout=15) as response:
                health=json.load(response)
            if health.get('mode')=='single-controller' and health.get('deployment')==manifest:
                break
        except Exception:
            pass
        if time.monotonic()>=deadline:
            raise RuntimeError('The expected HTTPS controller release did not become healthy')
        print('Waiting for the expected HTTPS controller release.',flush=True)
        time.sleep(5)
    if still_required(manifest):
        operator('verify-release',revision=revision)
        operator('resume',owner=HOLD)
        operator('reconcile')
        print(f'Verified controller {revision}; deployment hold cleared. Operator holds are preserved.')
        return True
    else:
        print('Verified release; a newer runtime keeps new admission held.')
        return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--initialize',action='store_true',help='First installation only; starts with an operator hold.')
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.revision):
        parser.error('A full source revision is required')
    os.umask(0o077)
    verified=deploy(args.revision,initialize=args.initialize)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:
            output.write('proceed='+str(bool(verified)).lower()+'\n')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Controller deployment failed ('+type(exc).__name__+'). Admission holds are retained.',file=sys.stderr)
        raise SystemExit(1)
