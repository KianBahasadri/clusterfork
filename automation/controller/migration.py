"""Once-only import of a verified idle legacy queue; credentials are never inputs."""

import hashlib
import json
import math
import re
from uuid import UUID

from .model import LegacyImported,Rejected,identity

FIELDS={'legacy_key','generation','issue_id','project_id','repository','identifier','status','priority','sort_order',
        'sequence','enqueued_at','target_branch','target_pull_request','target_dispatch_key','repair_key','repair_parent_key'}


def collect(snapshot,registry,*,legacy_generations=(),operator_holds=()):
    records=[]
    for row in snapshot['ClusterforkRepositoryQueue']:
        if row['RowKey']=='slot':
            if row.get('active_key') or row.get('owner'):
                raise Rejected('The legacy repository slot has not drained')
            continue
        route=registry.routes.get(row.get('project_id'))
        if route is None or route.repository.lower()!=row.get('repository','').lower():
            raise Rejected('A legacy queue reference is no longer mapped')
        generation=next((name for name in (registry.generation,*legacy_generations) if row['RowKey']==hashlib.sha256(
            f"{name}:{route.project_id}:{row['issue_id']}".encode()).hexdigest()),None)
        if generation is None:
            raise Rejected('The legacy task generation cannot be proven')
        record={key:value for key,value in row.items() if key in FIELDS}
        record['legacy_key']=row['RowKey']
        record['generation']=generation
        records.append(record)
    records.sort(key=lambda row:(row.get('sequence',0),row.get('enqueued_at',0),row['legacy_key']))
    holds=tuple(sorted(tuple(hold) for hold in operator_holds))
    command=LegacyImported(registry.generation,tuple(records),identity(registry.generation,records,holds),holds)
    validate(command)
    return {'generation':command.generation,'records':records,'source_digest':command.source_digest,'operator_holds':holds}


def validate(command):
    if command.source_digest!=identity(command.generation,list(command.records),command.operator_holds):
        raise Rejected('Legacy import digest does not match its records')
    for owner,reason in command.operator_holds:
        if not re.fullmatch(r'operator:[\w:./-]{1,180}',owner) or not isinstance(reason,str) or len(reason)>2000:
            raise Rejected('Invalid legacy operator hold')
    keys=set()
    for row in command.records:
        if (set(row)-FIELDS or row['status'] not in {'completed','skipped','abandoned'}
                or not re.fullmatch('[0-9a-f]{64}',row['legacy_key']) or row['legacy_key'] in keys
                or not re.fullmatch(r'[\w.-]{1,64}',row['generation'])
                or row['legacy_key']!=hashlib.sha256(f"{row['generation']}:{row['project_id']}:{row['issue_id']}".encode()).hexdigest()
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',row['repository'])
                or type(row.get('priority',0)) is not int or row.get('priority',0) not in range(5)
                or not math.isfinite(row.get('sort_order',0)) or len(json.dumps(row))>4000):
            raise Rejected('Unresolved or invalid legacy work cannot be imported as idle history')
        UUID(row['issue_id'])
        UUID(row['project_id'])
        keys.add(row['legacy_key'])


def import_history(db,command):
    validate(command)
    marker='legacy-import:'+command.generation
    previous=db.execute('SELECT value FROM metadata WHERE key=?',(marker,)).fetchone()
    if previous:
        if previous[0]!=command.source_digest:
            raise Rejected('Legacy history was already imported from a different snapshot')
        return
    if (db.execute('SELECT 1 FROM grants LIMIT 1').fetchone()
            or db.execute('SELECT 1 FROM attempts LIMIT 1').fetchone()
            or not db.execute("SELECT 1 FROM holds WHERE owner='operator:rebuild'").fetchone()):
        raise Rejected('Legacy import requires the initial operator halt and no new attempts')
    for row in command.records:
        key=identity(row['generation'],row['project_id'],row['issue_id'])
        phase='completed' if row['status']=='completed' else 'skipped'
        db.execute("INSERT INTO tasks(task_id,issue_id,project_id,generation,repository,identifier,phase,eligible,"
                   "dependencies_ready,priority,sort_order,content_revision,reason) VALUES(?,?,?,?,?,?,?,0,1,?,?,?,'Imported legacy history.') "
                   "ON CONFLICT(task_id) DO UPDATE SET phase=excluded.phase,eligible=0",
                   (key,row['issue_id'],row['project_id'],row['generation'],row['repository'].lower(),row['identifier'],
                    phase,row.get('priority',0),row.get('sort_order',0),''))
        db.execute('INSERT INTO legacy_history VALUES(?,?,?)',(row['legacy_key'],key,json.dumps(row,sort_keys=True)))
    for owner,reason in command.operator_holds:
        db.execute('INSERT OR IGNORE INTO holds(owner,reason) VALUES(?,?)',(owner,reason))
    db.execute('INSERT INTO metadata VALUES(?,?)',(marker,command.source_digest))
