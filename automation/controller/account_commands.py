"""Atomic account access and assignment behind the controller command interface."""

from .model import Assign,ExhaustAccount,Rejected,Role,SessionUpdated
from common.accounts import account_provider,valid_account_id


def execute(controller,db,view,role,owner,action,data,now):
    def apply(command):
        return controller.apply(command,now=now)
    def update(**fields):
        task=controller._task(db,view.task_id)
        controller._session(db,task,controller._attempt(db,task,view.attempt['attempt_id']),
                            SessionUpdated(view.task_id,view.attempt['attempt_id'],role.phase,fields))
    session=view.session(role.phase)
    if session is None:
        raise Rejected('Execution phase is unavailable')
    assigned,metadata=session['account_id'],session['data']
    if action=='heartbeat':
        if assigned and metadata.get('account_reserved'):
            account,_=controller.accounts.get(assigned)
            controller.accounts.save(assigned,owner,account['fence'])
        update(heartbeat_until=now+120)
        return {'running':True}
    if action=='accounts':
        if assigned:
            return {'accounts':[assigned]}
        if role.selector:
            return {'accounts':controller.accounts.list()}
        if role==Role.CODING and metadata.get('account_failover'):
            return {'accounts':[i for i in controller.accounts.list() if account_provider(i)=='codex'
                                and i not in metadata.get('exhausted_account_ids',[])]}
        raise Rejected('Agent job has no assigned account')
    identity=data.get('account_id')
    if not valid_account_id(identity):
        raise Rejected('Invalid provider account identity')
    if action=='save':
        if identity not in {assigned,metadata.get('probe_account_id','')}:
            raise Rejected('This phase cannot save another account')
        controller.accounts.save(identity,owner,data.get('lease'),data.get('auth'),release=data.get('release') is True)
        if data.get('release') is True:
            update(released_account_id=identity,probe_account_id='',account_reserved=False)
        return {'saved':True}
    if action=='exhaust_account':
        if role!=Role.CODING:
            raise Rejected('Only the coding agent may exhaust its account')
        account,_=controller.accounts.get(identity)
        if account['owner']==owner and account['lease_until']>now:
            raise Rejected('Release the exhausted account before failover')
        updated=apply(ExhaustAccount(view.task_id,view.attempt['attempt_id'],identity,data.get('thread_id')))
        return {'exhausted_account_ids':updated.session('coding')['data']['exhausted_account_ids']}
    replacement=role==Role.CODING and metadata.get('account_failover')
    if replacement and (account_provider(identity)!='codex' or identity in metadata.get('exhausted_account_ids',[])):
        raise Rejected('Cross-provider or exhausted account replacement is forbidden')
    if (assigned or not role.selector and not replacement) and identity!=assigned:
        raise Rejected('This phase cannot access another account')
    if action=='bind_account':
        if assigned==identity:
            return {'account_id':identity}
        if metadata.get('probe_account_id'):
            raise Rejected('Release the quota probe before binding')
        # Reserving the lease and closing initial selection are one transaction.
        # The next trusted step reuses this lease rather than competing for it.
        controller.accounts.claim(identity,owner)
        apply(Assign(view.task_id,view.attempt['attempt_id'],role,identity))
        update(heartbeat_until=now+120,released_account_id='',account_reserved=True)
        return {'account_id':identity}
    if action=='claim':
        probe=metadata.get('probe_account_id')
        if probe and probe!=identity:
            raise Rejected('Release the previous quota probe before claiming another')
        result=controller.accounts.claim(identity,owner)
        if not assigned:
            update(probe_account_id=identity)
        else:
            update(account_reserved=False)
        return result
    raise Rejected('Invalid account operation')
