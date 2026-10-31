"""Read-only enrollment evidence for central execution and a mapped target."""

import base64
import hashlib

from .authorization import release_ref
from .workflows import ENGINE,WORKFLOW,render
from common.routing import uuid_string


def evidence(registry,github,manifest,project_id):
    route=registry.routes[uuid_string(project_id)]
    result={'project_id':route.project_id,'repository':route.repository,'ref':route.ref,
            'workflow_repository':ENGINE,'workflow':WORKFLOW,'runtime_sha':manifest['revision'],'app_access':False}
    _,installation=github._installation(route.repository)
    result['permissions']={key:installation.get('permissions',{}).get(key,'none')
                           for key in ('actions','contents','pull_requests','workflows')}
    result['branch_sha']=github.branch_sha(route)
    result['app_access']=True
    token=github.installation_token(ENGINE,permissions={'contents':'read','actions':'read'})
    ref=release_ref(manifest['revision'])
    tag=github.request(f"https://api.github.com/repos/{ENGINE}/git/ref/{ref.removeprefix('refs/')}",token=f'Bearer {token}')
    result['runtime_tag_verified']=(tag.get('ref')==ref and tag.get('object',{}).get('sha')==manifest['revision']
                                    and tag.get('object',{}).get('type')=='commit')
    workflow=github.request(f'https://api.github.com/repos/{ENGINE}/actions/workflows/{WORKFLOW}',token=f'Bearer {token}')
    result['workflow_active']=workflow.get('state')=='active'
    source=github.request(f"https://api.github.com/repos/{ENGINE}/contents/.github/workflows/{WORKFLOW}?ref={manifest['revision']}",
                          token=f'Bearer {token}')
    observed=base64.b64decode(source['content'])
    result['workflow_digest']=hashlib.sha256(observed).hexdigest()
    result['workflow_verified']=observed==render()[WORKFLOW].encode()
    return result
