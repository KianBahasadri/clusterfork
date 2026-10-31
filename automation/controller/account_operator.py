"""Existing Azure operators can enroll/reclaim without direct database access."""

from uuid import UUID

from .model import Rejected


def execute(controller,operator,data):
    provider=data.get('provider')
    if provider not in controller.accounts.pools:
        raise Rejected('Unsupported account provider')
    pool=controller.accounts.pools[provider]
    action=data.get('operation')
    owner=f"local-reclaim:{operator}:{UUID(data['transfer_id'])}"
    with controller.database.transaction():
        if action=='enroll':
            return {'account_id':pool.enroll(data.get('auth'))}
        identity=data.get('account_id','')
        if not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):
            raise Rejected('Invalid account identity')
        if action=='get':
            row,version=pool.get(identity)
            return {'row':row,'version':version}
        if action=='claim':
            return pool.claim(identity,owner,allow_staged=True,reuse=True)
        if action=='delete':
            pool.delete(identity,data.get('lease'),owner=owner)
            return {'deleted':True}
        raise Rejected('Unsupported account operation')
