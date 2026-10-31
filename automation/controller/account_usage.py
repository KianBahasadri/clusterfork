"""Private, leased quota collection and an allowlisted read-only public projection."""

import json
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

import codex_runtime
import cursor_runtime
from common.accounts import WorkerError
from common.reporting import usage_report, validate_usage
from common.selection import finite_number

MAX_AGE_SECONDS = 300
RETRY_SECONDS = 60


def measurement(data):
    value=validate_usage(data)
    # Only provider-normalized names cross this boundary, never arbitrary text
    # from credentials, provider error messages or worker-supplied labels.
    if any(window['name'] not in {'cursor/api','cursor/included'}
           and not re.fullmatch(r'(?:codex|gpt-[A-Za-z0-9.-]{1,80})/(?:primary|secondary)',window['name'])
           for window in value['windows']):
        raise ValueError('Unknown quota window')
    return value


def snapshot(accounts,provider,*,now=None):
    """Read cache columns only. This path cannot claim, refresh or inspect auth."""
    now=time.time() if now is None else now
    with accounts.database.transaction() as db:
        rows=db.execute('SELECT a.account_id,a.state,a.metadata,u.measurement,u.attempted_at,u.error '
                        'FROM accounts a LEFT JOIN account_usage u ON a.provider=u.provider AND a.account_id=u.account_id '
                        'WHERE a.provider=? ORDER BY a.account_id',(provider,)).fetchall()
    result=[]
    for row in rows:
        if not re.fullmatch('[0-9a-f]{64}',row['account_id']):
            continue
        metadata=json.loads(row['metadata'])
        reading=None
        if row['measurement']:
            try:
                reading=measurement(json.loads(row['measurement']))
            except (ValueError,TypeError):
                pass
        observed=reading['observed_at'] if reading else None
        age=max(0,now-observed) if observed is not None else None
        fresh=(observed is not None and 0<=now-observed<=MAX_AGE_SECONDS
               and all(window['resets_at'] is None or window['resets_at']>now for window in reading['windows']))
        error=row['error'] if row['error'] in {'busy','inspection_failed','lease_lost'} else None
        labels=metadata.get('aliases',[])
        labels=(sorted({name for name in labels if isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9._-]{1,120}',name)})
                if isinstance(labels,list) else [])
        result.append({'provider':provider,'account_id':row['account_id'],
                       'aliases':labels, 'cloud_available':True,
                       'cloud_state':row['state'] if row['state'] in {'active','staged'} else 'unknown',
                       'usage':{'observed_at':observed,'attempted_at':row['attempted_at'],
                                'age_seconds':age,'max_age_seconds':MAX_AGE_SECONDS,
                                'freshness':'fresh' if fresh else ('stale' if reading else 'unknown'),
                                'error':error,'windows':reading['windows'] if reading else []}})
    return {'schema_version':1,'generated_at':now,'provider':provider,'accounts':result}


class UsageCollector:
    def __init__(self,accounts,registry,*,clock=time.time,probe=None,enabled=lambda:True):
        self.accounts,self.registry,self.clock=accounts,registry,clock
        self.probe=probe or self._probe
        self.enabled=enabled

    def _probe(self,provider,identity,home):
        runtime=cursor_runtime if provider=='cursor' else codex_runtime
        limits=runtime.read_usage(home) if provider=='cursor' else runtime.read_usage(home,timeout=15)
        return usage_report(runtime.quota_snapshot(identity,self.registry.agent_profiles[provider].model,
                                                   limits,allow_exhausted=True))

    def _record(self,pool,identity,reading,error):
        with pool.database.transaction() as db:
            db.execute('INSERT INTO account_usage(provider,account_id,measurement,attempted_at,error) VALUES (?,?,?,?,?) '
                       'ON CONFLICT(provider,account_id) DO UPDATE SET '
                       'measurement=COALESCE(excluded.measurement,account_usage.measurement),'
                       'attempted_at=excluded.attempted_at,error=excluded.error',
                       (pool.provider,identity,json.dumps(reading) if reading else None,self.clock(),error))

    def collect(self,pool,identity):
        owner='quota:'+str(uuid4())
        try:
            lease=pool.claim(identity,owner,allow_staged=True)
        except WorkerError:
            self._record(pool,identity,None,'busy')
            return
        reading,error=None,None
        with tempfile.TemporaryDirectory(prefix='clusterfork-quota-') as directory:
            home=Path(directory)
            path=cursor_runtime.auth_path(home) if pool.provider=='cursor' else home/'auth.json'
            codex_runtime.write_private(path,lease['auth'])
            try:
                reading=measurement(self.probe(pool.provider,identity,home))
                if not finite_number(reading['observed_at']) or reading['observed_at']>self.clock()+5:
                    raise ValueError('Invalid quota timestamp')
            except Exception:
                reading=None
                error='inspection_failed'
            finally:
                # Even failed inspection may have refreshed the cloud token.
                # Persist it under the original fence before publishing a reading.
                try:
                    auth=json.loads(path.read_text()) if path.exists() else None
                    with pool.database.transaction():
                        pool.save(identity,owner,lease['lease'],auth,release=True)
                        self._record(pool,identity,reading,error)
                except Exception:
                    self._record(pool,identity,None,'lease_lost')

    def tick(self,stop=None):
        for pool in self.accounts.pools.values():
            with pool.database.transaction() as db:
                identities=[row[0] for row in db.execute(
                    'SELECT a.account_id FROM accounts a LEFT JOIN account_usage u '
                    'ON a.provider=u.provider AND a.account_id=u.account_id WHERE a.provider=? '
                    'AND (u.attempted_at IS NULL OR u.attempted_at<=?)',
                    (pool.provider,self.clock()-RETRY_SECONDS))]
            for identity in identities:
                if not self.enabled() or stop is not None and stop.is_set():
                    return
                self.collect(pool,identity)

    def run(self,stop):
        while not stop.is_set():
            try:
                self.tick(stop)
            except Exception:
                pass  # Unknown/stale remains explicit; never log provider errors.
            stop.wait(RETRY_SECONDS)
