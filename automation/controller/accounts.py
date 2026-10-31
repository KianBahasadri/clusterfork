"""Controller-owned credential records; all worker mutations share task transactions."""

import json
import time
from uuid import uuid4

from common.accounts import LEASE_SECONDS,WorkerError,account_identity,account_provider,valid_account_id


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

    def enroll(self,auth):
        identity=account_identity(auth,self.provider)
        with self.database.transaction() as db:
            if not db.execute('INSERT OR IGNORE INTO accounts(provider,account_id,auth,state) VALUES (?,?,?,?)',
                              (self.provider,identity,json.dumps(auth),'active')).rowcount:
                raise WorkerError('Account is already enrolled; refreshed credentials are preserved')
        return identity

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
                statement+=',auth=?'
                args.append(json.dumps(auth))
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
