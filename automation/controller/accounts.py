"""Controller-owned credential records; all worker mutations share task transactions."""

import json
import re
import time
from uuid import uuid4

from common.accounts import (LEASE_SECONDS,WorkerError,account_identity,account_provider,valid_account_id,
                             login_fingerprints,login_id)


def validate_aliases(values):
    if (not isinstance(values,(list,tuple)) or len(values)>256
            or any(not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,120}',name) for name in values)):
        raise WorkerError('Invalid account display aliases',400)
    return sorted(set(values))


class AccountPool:
    def __init__(self,database,provider,clock=time.time):
        self.database,self.provider,self.clock=database,provider,clock

    def get(self,identity):
        with self.database.transaction() as db:
            row=db.execute('SELECT * FROM accounts WHERE provider=? AND account_id=?',(self.provider,identity)).fetchone()
            if row is None:
                raise WorkerError('Account is not enrolled',404)
            value={**json.loads(row['metadata']),**dict(row)}
            value.pop('metadata')
            return value,row['version']

    def records(self):
        with self.database.transaction() as db:
            return [self.get(row[0])[0] for row in db.execute('SELECT account_id FROM accounts WHERE provider=?',(self.provider,)).fetchall()]

    def list(self):
        return [row['account_id'] for row in self.records() if row['state']=='active']

    def enroll(self,auth,aliases=()):
        identity=account_identity(auth,self.provider)
        labels=validate_aliases(aliases)
        metadata={'aliases':labels,'cloud_login':{'session_id':login_id(auth,self.provider),
                                               'fingerprints':login_fingerprints(auth,self.provider)}}
        with self.database.transaction() as db:
            if not db.execute('INSERT OR IGNORE INTO accounts(provider,account_id,auth,state,metadata) VALUES (?,?,?,?,?)',
                              (self.provider,identity,json.dumps(auth),'active',json.dumps(metadata))).rowcount:
                raise WorkerError('Account is already enrolled; refreshed credentials are preserved')
        return identity

    def _metadata(self,db,identity):
        row=db.execute('SELECT auth,metadata,lease_until FROM accounts WHERE provider=? AND account_id=?',
                       (self.provider,identity)).fetchone()
        if row is None:
            raise WorkerError('Account is not enrolled',404)
        metadata=json.loads(row['metadata'])
        auth=json.loads(row['auth'])
        cloud=metadata.setdefault('cloud_login',{'session_id':login_id(auth,self.provider),'fingerprints':[]})
        cloud['fingerprints']=sorted(set(cloud['fingerprints']) | set(login_fingerprints(auth,self.provider)))
        return row,metadata

    def describe(self,identity,*,aliases=()):
        labels=validate_aliases(aliases)
        with self.database.transaction() as db:
            _,metadata=self._metadata(db,identity)
            metadata['aliases']=sorted(set(metadata.get('aliases',[])) | set(labels))
            db.execute('UPDATE accounts SET metadata=? WHERE provider=? AND account_id=?',
                       (json.dumps(metadata),self.provider,identity))
            return {'account_id':identity,'session_id':metadata['cloud_login']['session_id']}

    def register_local(self,identity,session_id,fingerprints):
        if (not isinstance(session_id,str) or not re.fullmatch('[0-9a-f]{64}',session_id)
                or not isinstance(fingerprints,list) or not 2<=len(fingerprints)<=4096
                or any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) for value in fingerprints)):
            raise WorkerError('Invalid local login registration',400)
        with self.database.transaction() as db:
            row,metadata=self._metadata(db,identity)
            if row['lease_until']>self.clock():
                raise WorkerError('Account is busy; retry local registration after its lease ends',reason_code='account_busy')
            if set(fingerprints).intersection(metadata['cloud_login']['fingerprints']):
                raise WorkerError('Local login reuses a known cloud token or provider session',reason_code='login_not_independent')
            local=metadata.setdefault('local_logins',{})
            local[session_id]=sorted(set(local.get(session_id,[])) | set(fingerprints))
            db.execute('UPDATE accounts SET metadata=? WHERE provider=? AND account_id=?',
                       (json.dumps(metadata),self.provider,identity))
            return {'session_id':session_id,'cloud_fingerprints':metadata['cloud_login']['fingerprints']}

    def claim(self,identity,owner,*,allow_staged=False,reuse=False):
        with self.database.transaction() as db:
            row,_=self.get(identity)
            if row['state']!='active' and not (allow_staged and row['state']=='staged'):
                raise WorkerError('Account is not enrolled for execution',403)
            try:
                auth=json.loads(row['auth'])
                if account_identity(auth,self.provider)!=identity:
                    raise ValueError()
            except (ValueError,TypeError,KeyError,WorkerError):
                raise WorkerError('Stored account credentials are invalid') from None
            live=row['lease_until']>self.clock()
            if live and (not reuse or row['owner']!=owner):
                raise WorkerError('Account is busy')
            fence=row['fence'] if live else str(uuid4())
            db.execute('UPDATE accounts SET owner=?,fence=?,lease_until=?,version=version+1 WHERE provider=? AND account_id=?',
                       (owner,fence,self.clock()+LEASE_SECONDS,self.provider,identity))
            return {'auth':auth,'lease':fence}

    def save(self,identity,owner,fence,auth=None,release=False):
        with self.database.transaction() as db:
            if auth is not None and account_identity(auth,self.provider)!=identity:
                raise WorkerError('Refreshed credentials belong to another account',400)
            args=[0 if release else self.clock()+LEASE_SECONDS,'' if release else owner,'' if release else fence]
            statement='UPDATE accounts SET lease_until=?,owner=?,fence=?,version=version+1'
            if auth is not None:
                _,metadata=self._metadata(db,identity)
                fingerprints=set(login_fingerprints(auth,self.provider))
                if any(fingerprints.intersection(values) for values in metadata.get('local_logins',{}).values()):
                    raise WorkerError('Refusing to replace the cloud login with an independent local login',reason_code='session_ownership_conflict')
                metadata['cloud_login']['fingerprints']=sorted(set(metadata['cloud_login']['fingerprints']) | fingerprints)
                statement+=',auth=?,metadata=?'
                args.extend([json.dumps(auth),json.dumps(metadata)])
            args.extend([self.provider,identity,owner,fence,self.clock()])
            if not db.execute(statement+' WHERE provider=? AND account_id=? AND owner=? AND fence=? AND lease_until>?',args).rowcount:
                raise WorkerError('Account lease was lost')

    def delete(self,identity,fence,*,owner='local-reclaim'):
        with self.database.transaction() as db:
            if not db.execute('DELETE FROM accounts WHERE provider=? AND account_id=? AND owner=? AND fence=? AND lease_until>?',
                       (self.provider,identity,owner,fence,self.clock())).rowcount:
                raise WorkerError('Reclaim lease was lost')


class Accounts:
    def __init__(self,database,*,clock=time.time):
        self.database=database
        self.pools={provider:AccountPool(database,provider,clock) for provider in ('codex','cursor')}

    def pool(self,identity):
        if not valid_account_id(identity):
            raise WorkerError('Invalid account identity',400)
        return self.pools[account_provider(identity)],identity.rsplit(':',1)[-1]

    def get(self,identity):
        pool,key=self.pool(identity)
        return pool.get(key)

    def list(self):
        return [row['account_id'] if provider=='codex' else 'cursor:'+row['account_id']
                for provider,pool in self.pools.items() for row in pool.records() if row['state']=='active']

    def claim(self,identity,owner):
        pool,key=self.pool(identity)
        return pool.claim(key,owner,reuse=True)

    def save(self,identity,owner,fence,auth=None,release=False):
        pool,key=self.pool(identity)
        return pool.save(key,owner,fence,auth,release)

    def idle(self):
        with self.database.transaction() as db:
            return not any(db.execute('SELECT 1 FROM accounts WHERE provider=? AND lease_until>? LIMIT 1',
                                      (provider,pool.clock())).fetchone() for provider,pool in self.pools.items())
