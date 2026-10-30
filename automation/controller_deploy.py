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

sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parent/'azure')]
from controller.release import local_manifest,tree_digest
from controller.migration import validate,collect
from controller.model import LegacyImported
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


def install_script(config,manifest,history=None):
    script=(ROOT/'automation/controller/host/install.sh').read_text()
    for placeholder,value in [('REVISION',manifest['revision']),
                              ('CONFIG_B64',base64.b64encode(json.dumps(config).encode()).decode()),
                              ('MANIFEST_B64',base64.b64encode(json.dumps(manifest).encode()).decode()),
                              ('MIGRATION_B64',base64.b64encode(json.dumps(history).encode()).decode() if history is not None else '')]:
        script=script.replace('__'+placeholder+'__',value)
    # The child shell owns errexit and logging. Its success marker is outside the
    # child's redirected log; no command output or credentials are relayed.
    return "#!/bin/sh\nif /bin/bash <<'CF_INSTALL_SCRIPT'\n"+script+"\nCF_INSTALL_SCRIPT\nthen\n echo CF_INSTALL_OK\nelse\n echo CF_INSTALL_FAILED\n exit 1\nfi\n"


def initial_boundary(history):
    # Initial migration is an operator operation. Shared-key access remains in
    # memory, as in the existing account tooling; it never enters the release.
    from accounts import connect
    from azure.data.tables import TableClient
    from deployment_control import DeploymentControl
    from dispatch_core import Registry
    from worker_store import AzureTable
    base=connect().table
    endpoint=base.url.removesuffix('/ClusterforkAccounts')
    def table(name):
        return TableClient(endpoint,name,credential=base.credential)
    control=DeploymentControl(AzureTable.with_client(table('ClusterforkOperations'),'control'))
    state=control.state()[0]
    if state.get('paused') is not True:
        raise RuntimeError('The legacy operator halt is required')
    if state.get('owner','').startswith('operator:') and state['owner'] not in dict(history.get('operator_holds',())):
        raise RuntimeError('The legacy operator halt must be preserved by migration')
    if any(row.get('lease_until',0)>time.time() for row in base.list_entities()):
        raise RuntimeError('A legacy credential lease is still active')
    current=collect({'ClusterforkRepositoryQueue':list(table('ClusterforkRepositoryQueue').list_entities())},
                    Registry.load(ROOT/'automation/azure/projects.json'),
                    legacy_generations=tuple({row['generation'] for row in history['records']}),
                    operator_holds=history.get('operator_holds',()))
    if current['source_digest']!=history['source_digest']:
        raise RuntimeError('The idle migration history has changed; refresh the private snapshot')
    for workflow in ('clusterfork-worker.yml','clusterfork-cursor-smoke.yml','clusterfork-task.yml'):
        page=1
        while True:
            data=github(f'actions/workflows/{workflow}/runs?per_page=100&page={page}')
            runs=data['workflow_runs']
            if any(run['status']!='completed' for run in runs):
                raise RuntimeError('A GitHub worker attempt has not stopped')
            if len(runs)<100:
                if (page-1)*100+len(runs)<data['total_count']:
                    raise RuntimeError('GitHub attempt listing is incomplete')
                break
            page+=1


def deploy(revision,*,initialize=False,history=None):
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
        if history is None:
            raise ValueError('Initial installation requires verified idle migration history')
        validate(LegacyImported(history['generation'],tuple(history['records']),history['source_digest'],
                                tuple(tuple(h) for h in history.get('operator_holds',()))))
        marker=run_host('#!/bin/sh\nif test ! -e /opt/clusterfork/current && ! systemctl is-active --quiet clusterfork-controller; then echo CF_FIRST_INSTALL; fi\n')
        if 'CF_FIRST_INSTALL' not in marker:
            raise RuntimeError('Initial installation cannot bypass an existing controller drain')
        initial_boundary(history)
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
    if not still_required(manifest):
        print('A newer runtime superseded this release before installation.')
        return
    if initialize:
        command(['az','functionapp','stop','--resource-group',GROUP,'--name','clusterfork-dispatch-7604cac329','--only-show-errors'])
        state=command(['az','functionapp','show','--resource-group',GROUP,'--name','clusterfork-dispatch-7604cac329',
                       '--query','state','--output','tsv','--only-show-errors']).strip()
        if state!='Stopped':
            raise RuntimeError('Legacy lifecycle writers have not stopped')
    output=run_host(install_script(config,manifest,history))
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
    else:
        print('Verified release; a newer runtime keeps new admission held.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--initialize',action='store_true',help='First installation only; starts with an operator hold.')
    parser.add_argument('--migration-file',type=Path)
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.revision):
        parser.error('A full source revision is required')
    os.umask(0o077)
    if args.initialize!=bool(args.migration_file):
        parser.error('Initial installation requires --migration-file; routine deployment preserves existing history')
    deploy(args.revision,initialize=args.initialize,
           history=json.loads(args.migration_file.read_text()) if args.migration_file else None)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Controller deployment failed ('+type(exc).__name__+'). Admission holds are retained.',file=sys.stderr)
        raise SystemExit(1)
