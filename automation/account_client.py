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
        request=Request(ENDPOINT,data=json.dumps({'operation':operation,'provider':self.provider,
                                                  'transfer_id':self.transfer_id,**fields}).encode(),
                        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=25) as response:
                return json.load(response)
        except HTTPError as error:
            status=error.code
            error.close()
            raise WorkerError('Controller account transfer was rejected',status) from None
        except (URLError,OSError,ValueError):
            raise WorkerError('Controller account transfer is unavailable',503) from None

    def enroll(self,auth):
        return self.call('enroll',auth=auth)['account_id']

    def get(self,identity):
        result=self.call('get',account_id=identity)
        return result['row'],result['version']

    def claim(self,identity,owner,*,allow_staged=False):
        if owner!='local-reclaim' or not allow_staged:
            raise WorkerError('Local account access is limited to fenced reclaim',403)
        return self.call('claim',account_id=identity)

    def delete(self,identity,fence):
        return self.call('delete',account_id=identity,lease=fence)
