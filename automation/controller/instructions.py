"""Durable steering against real task views, with one transaction per handoff."""

from datetime import datetime
import hashlib
import json
import logging
import re
from uuid import UUID,uuid4

from .model import InstructionRequest,ReportConfirmed,Rejected
from .workflows import ENGINE
from common.publication import comment_id
from common.routing import uuid_string

OPEN_STAGES={'initializing','selecting_account','coding','testing'}
DELIVERY_SECONDS=90


def accepting(view,now):
    session=view.session('coding')
    data=session['data'] if session else {}
    return bool(view.active and view.eligible and view.phase=='executing' and session and session['agent_started']
                and not session['finished'] and not view.attempt['stopped'] and session['deadline']>now
                and data.get('heartbeat_until',0)>now and not data.get('steering_closed')
                and view.attempt['stage'] in OPEN_STAGES)


def binding(view):
    return {'dispatch_key':view.attempt['attempt_id'],
            'owner':f"{view.attempt['run_id']}:{view.attempt['run_attempt']}",
            'attempt_id':str(UUID(hex=view.attempt['attempt_id'][:32],version=4))}


def bound(data,view):
    return all(data.get(key)==value for key,value in binding(view).items())


def rows(db):
    return [{**json.loads(row['data']),'comment_id':row['comment_id'],'issue_id':row['issue_id'],
             'sequence':row['sequence'],'state':row['state']}
            for row in db.execute('SELECT * FROM instructions ORDER BY issue_id,sequence')]


def write(db,row):
    db.execute('UPDATE instructions SET state=?,data=? WHERE comment_id=?',
               (row['state'],json.dumps(row),row['comment_id']))


def repair(controller,db,now):
    for row in rows(db):
        if row['state'] not in {'queued','delivering'}:
            continue
        try:
            view=controller.for_attempt(row['dispatch_key'])
            data=(view.session('coding') or {}).get('data',{})
            live=accepting(view,now) and bound(row,view)
            if row['state']=='delivering':
                live=live and all(row.get(key)==data.get(key) for key in ('thread_id','turn_id'))
        except Rejected:
            live=False
        expired=row['state']=='delivering' and row.get('delivery_until',0)<=now
        if live and not expired:
            continue
        delivered=row['state']=='delivering'
        row.update(state='uncertain' if delivered else 'failed',reason=(
            'Delivery was handed off, but agent acceptance could not be confirmed. It will not be replayed automatically.'
            if delivered else 'The bound agent attempt stopped accepting instructions before delivery. It will not be sent to another attempt.'))
        write(db,row)


def apply_request(controller,db,command,now):
    action,data=command.operation,command.data
    if action=='observe':
        issue_id=uuid_string(data['issue_id'])
        active=[view for view in controller.active() if view.issue_id==issue_id and accepting(view,now)]
        view=active[0] if len(active)==1 and data['available'] else None
        for comment in sorted(data['comments'],key=lambda item:(item['createdAt'],item['id'])):
            match=re.match(r'^@clusterfork(?:\s+|$)',comment.get('body',''),flags=re.IGNORECASE)
            if not match or comment.get('botActor'):
                continue
            key=uuid_string(comment['id'])
            if db.execute('SELECT 1 FROM instructions WHERE comment_id=?',(key,)).fetchone():
                continue
            text=comment['body'][match.end():].strip()
            author=(comment.get('user') or {}).get('id')
            state,reason='queued',''
            if author not in data['trusted_user_ids']:
                state,reason='rejected','This Linear user is not authorized to steer Clusterfork.'
            elif not text or len(text.encode())>16_000 or '\x00' in text:
                state,reason='rejected','Use a nonempty instruction of at most 16,000 UTF-8 bytes.'
            elif view is None:
                state,reason='failed','No active development agent is accepting instructions on this issue. No workflow was started.'
            elif datetime.fromisoformat(comment['createdAt'].replace('Z','+00:00')).timestamp() < view.session('coding')['data'].get('steering_started_at',view.attempt['started_at']):
                state,reason='failed','This comment predates the current attempt; post a new comment.'
            sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM instructions WHERE issue_id=?',(issue_id,)).fetchone()[0]
            record={'reason':reason,'text':text if state=='queued' else '', 'author_id':author or '',
                    'created_at':comment['createdAt'],'ack_id':comment_id(key,'steering-ack'),
                    **(binding(view) if view else {})}
            db.execute('INSERT INTO instructions VALUES (?,?,?,?,?)',(key,issue_id,sequence,state,json.dumps(record)))
        repair(controller,db,now)
        return None
    if action=='reconcile':
        repair(controller,db,now)
        return None
    view=controller.for_attempt(data['dispatch_key'])
    if action=='poll':
        repair(controller,db,now)
        if not accepting(view,now):
            return {'instruction':None}
        candidates=[row for row in rows(db) if row['issue_id']==view.issue_id and bound(row,view)]
        if any(row['state']=='delivering' for row in candidates):
            return {'instruction':None}
        row=next((row for row in candidates if row['state']=='queued'),None)
        if row is None:
            return {'instruction':None}
        session=view.session('coding')['data']
        if not all(session.get(key) for key in ('thread_id','turn_id')):
            raise Rejected('Agent session has not registered')
        row.update(state='delivering',delivery=str(uuid4()),delivery_until=now+DELIVERY_SECONDS,
                   thread_id=session['thread_id'],turn_id=session['turn_id'])
        write(db,row)
        return {'instruction':{key:row[key] for key in ('issue_id','dispatch_key','owner','attempt_id','thread_id','turn_id','text','delivery','sequence','comment_id')}}
    if action=='result':
        key=uuid_string(data['comment_id'])
        row=next((row for row in rows(db) if row['comment_id']==key),None)
        if (not row or not bound(row,view) or row['issue_id']!=view.issue_id
                or any(row.get(key)!=data.get(key) for key in ('delivery','thread_id','turn_id'))):
            raise Rejected('Instruction belongs to a different attempt or session')
        status=data['status']
        if status not in {'accepted','failed','uncertain'}:
            raise Rejected('Invalid instruction result')
        if row['state']!=status:
            if row['state'] not in {'delivering','uncertain'}:
                raise Rejected('Instruction already has a different result')
            row.update(state=status,reason={'accepted':'','failed':'The agent did not accept this instruction; post a new comment while it is coding.',
                                          'uncertain':'Agent acceptance is unknown. This instruction will not be replayed automatically.'}[status])
            write(db,row)
        return {'status':status}
    raise Rejected('Unsupported instruction operation')


class Instructions:
    def __init__(self,controller,registry,linear,*,clock):
        self.controller,self.registry,self.linear,self.clock=controller,registry,linear,clock

    def apply(self,operation,**data):
        return self.controller.apply(InstructionRequest(operation,data),now=self.clock())

    def observe(self,issue_id):
        issue=self.linear.issue(issue_id)
        if not issue:
            return
        project=issue.get('project') or {}
        route=self.registry.routes.get(project.get('id'))
        available=bool(route and not issue.get('archivedAt') and not project.get('archivedAt')
                       and issue.get('state',{}).get('type') not in {'completed','canceled'}
                       and any(label['name']==self.registry.enrollment_label for label in project.get('labels',{}).get('nodes',[])))
        self.apply('observe',issue_id=issue_id,comments=self.linear.issue_comments(issue_id),available=available,
                   trusted_user_ids=[uuid_string(self.linear.viewer_id()),*self.registry.steering_user_ids])

    def handle(self,view,action,data):
        if action=='instructions':
            self.observe(view.issue_id)
            result=self.apply('poll',dispatch_key=view.attempt['attempt_id'])
        elif action=='instruction_result':
            result=self.apply('result',dispatch_key=view.attempt['attempt_id'],**{key:data[key] for key in ('comment_id','delivery','status','thread_id','turn_id')})
        else:
            raise Rejected('Unsupported instruction transport operation')
        self.report()
        return result

    def comment(self,key):
        comment=self.linear.comment(key)
        if comment and comment.get('issue'):
            self.observe(uuid_string(comment['issue']['id']))
        self.report()

    def report(self):
        with self.controller.database.transaction() as db:
            pending=rows(db)
        for row in pending:
            run=(f"[worker run](https://github.com/{ENGINE}/actions/runs/{row['owner'].replace(':','/attempts/')})"
                 if row.get('owner') else 'this issue')
            source=f"[comment](https://linear.app/issue/{row['issue_id']}#comment-{row['comment_id']})"
            if row['state'] in {'queued','delivering'}:
                message=f'Queued instruction from {source} for {run}. The agent has not confirmed acceptance yet.'
            elif row['state']=='accepted':
                message=f'Agent accepted instruction from {source} in {run}.'
            else:
                message=f"Instruction from {source} for {run}: {row['state']}. {row['reason']}"
            body='Clusterfork steering: '+message
            signature=hashlib.sha256(body.encode()).hexdigest()
            key='instruction:'+row['comment_id']
            if self.controller.report_signature(key)==signature:
                continue
            try:
                if self.linear.status_comment(row['issue_id'],row['ack_id'],body) is False:
                    raise RuntimeError('Instruction acknowledgement was not confirmed')
                self.controller.apply(ReportConfirmed(key,signature),now=self.clock())
            except Exception:
                logging.warning('Instruction reporting deferred for %s',row['comment_id'])

    def reconcile(self):
        issues={view.issue_id for view in self.controller.active() if accepting(view,self.clock())}
        with self.controller.database.transaction() as db:
            issues.update(row['issue_id'] for row in rows(db) if row['state'] in {'queued','delivering'})
        for issue in sorted(issues):
            try:
                self.observe(issue)
            except Exception:
                logging.warning('Instruction observation deferred for %s',issue)
        self.apply('reconcile')
        self.report()
