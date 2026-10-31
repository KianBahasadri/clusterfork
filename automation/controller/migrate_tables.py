"""Once-only, idle transfer of existing account and instruction resources."""

import hashlib
import json
import time
from uuid import uuid4

from .model import Rejected

FENCE_OWNER='migration:controller-sqlite'


def migrate(controller,config,credential,*,client_factory=None):
    with controller.database.transaction() as db:
        if db.execute("SELECT 1 FROM metadata WHERE key='resources:sqlite-v1'").fetchone():
            return
    if 'storage_account' not in config:
        with controller.database.transaction() as db:
            db.execute("INSERT INTO metadata VALUES ('resources:sqlite-v1','fresh')")
        return
    if controller.active():
        raise Rejected('Resource migration requires a verified idle controller')
    if client_factory is None:
        from azure.data.tables import TableClient
        client_factory=TableClient
    endpoint=f"https://{config['storage_account']}.table.core.windows.net"
    accounts=client_factory(endpoint,'ClusterforkAccounts',credential=credential)
    instructions=client_factory(endpoint,'ClusterforkInstructions',credential=credential)
    current=list(accounts.list_entities())
    if any(row.get('lease_until',0)>time.time() and row.get('owner')!=FENCE_OWNER for row in current):
        raise Rejected('A credential lease still prevents resource migration')
    backup=controller.database.path.parent/'migrations'/'azure-resources.json'
    backup.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if backup.exists():
        bundle=json.loads(backup.read_text())
        saved={(row['PartitionKey'],row['RowKey']):row for row in bundle['accounts']}
        if ({(row['PartitionKey'],row['RowKey']) for row in current}!=set(saved)
                or any(row['auth']!=saved[(row['PartitionKey'],row['RowKey'])]['auth'] for row in current)):
            raise Rejected('Cloud account ownership changed during the paused migration')
    else:
        bundle={'accounts':[dict(row) for row in current],'instructions':[dict(row) for row in instructions.list_entities()]}
        from codex_runtime import write_private
        write_private(backup,bundle)
    # Existing CLI versions check this lease before reclaiming. Fence them before
    # importing so they cannot later reclaim stale copies behind the new owner.
    from azure.core import MatchConditions
    from azure.data.tables import UpdateMode,EntityProperty,EdmType
    for row in current:
        if row.get('owner')==FENCE_OWNER:
            continue
        fenced={**dict(row),'owner':FENCE_OWNER,'fence':str(uuid4()),'lease_until':EntityProperty(4102444800,EdmType.INT64)}
        accounts.update_entity(fenced,mode=UpdateMode.REPLACE,etag=row.metadata['etag'],
                               match_condition=MatchConditions.IfNotModified)
    digest=hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest()
    with controller.database.transaction() as db:
        if db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():
            raise Rejected('Local accounts already exist without a verified migration receipt')
        for row in bundle['accounts']:
            provider=row['PartitionKey']
            if provider not in {'codex','cursor'}:
                raise Rejected('Unknown account provider in migration')
            json.loads(row['auth'])
            metadata={key:value for key,value in row.items() if key not in {
                'PartitionKey','RowKey','auth','state','owner','fence','lease_until','provider'}}
            db.execute('INSERT INTO accounts(provider,account_id,auth,state,metadata) VALUES (?,?,?,?,?)',
                       (provider,row['RowKey'],row['auth'],row.get('state','active'),json.dumps(metadata)))
        for row in bundle['instructions']:
            if row['RowKey'].startswith('control:'):
                continue
            data={key:value for key,value in row.items() if key not in {'PartitionKey','RowKey'}}
            db.execute('INSERT INTO instructions VALUES (?,?,?,?,?)',
                       (row['RowKey'],row['issue_id'],row['sequence'],row['state'],json.dumps(data)))
        db.execute("INSERT INTO metadata VALUES ('resources:sqlite-v1',?)",(digest,))
