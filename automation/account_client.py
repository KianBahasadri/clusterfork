"""Account transfer over HTTPS using the operator's existing Azure CLI login."""

import json
import subprocess
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
from uuid import uuid4

from common.accounts import WorkerError

ENDPOINT='https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/accounts'


class AccountClient:
    def __init__(self,provider):
        self.provider,self.transfer_id,self.token=provider,str(uuid4()),None

    def call(self,operation,**fields):
        if self.token is None:
            result=subprocess.run(['az','account','get-access-token','--resource','https://management.azure.com/',
                                   '--query','accessToken','--output','tsv','--only-show-errors'],capture_output=True,text=True)
            if result.returncode or not result.stdout.strip():
                raise WorkerError('Use the existing Azure operator login before transferring accounts',401)
            self.token=result.stdout.strip()
        if operation=='snapshot':
            request=Request(ENDPOINT+'/snapshot?provider='+self.provider,
                            headers={'Authorization':'Bearer '+self.token},method='GET')
        else:
            request=Request(ENDPOINT,data=json.dumps({'operation':operation,'provider':self.provider,
                                                      'transfer_id':self.transfer_id,**fields}).encode(),
                            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=25) as response:
                return json.load(response)
        except HTTPError as error:
            status=error.code
            try:
                code=json.loads(error.read(4000)).get('reason_code')
            except (ValueError,AttributeError):
                code=None
            error.close()
            reasons={'account_busy':'Account is busy; retry after its cloud lease ends',
                     'login_not_independent':'Local login reuses a known cloud token or provider session',
                     'session_ownership_conflict':'Refusing to overwrite an independent local or cloud login'}
            raise WorkerError(reasons.get(code,'Controller account operation was rejected'),status) from None
        except (URLError,OSError,ValueError):
            raise WorkerError('Controller account transfer is unavailable',503) from None

    def enroll(self,auth,*,aliases=()):
        return self.call('enroll',auth=auth,aliases=list(aliases))['account_id']

    def describe(self,identity,*,aliases=()):
        return self.call('describe',account_id=identity,aliases=list(aliases))

    def register_local(self,identity,session_id,fingerprints):
        return self.call('register_local',account_id=identity,session_id=session_id,
                         fingerprints=fingerprints,fresh_login=True)

    def snapshot(self):
        return self.call('snapshot')

    def get(self,identity):
        result=self.call('get',account_id=identity)
        return result['row'],result['version']

    def claim(self,identity,owner,*,allow_staged=False):
        if owner!='local-reclaim' or not allow_staged:
            raise WorkerError('Local account access is limited to fenced reclaim',403)
        return self.call('claim',account_id=identity)

    def delete(self,identity,fence):
        return self.call('delete',account_id=identity,lease=fence)
