"""Verify the running release and create its exact immutable dispatch reference."""

from .authorization import release_ref
from .model import Rejected
from .workflows import ENGINE
from common.clients import RequestError


def verify_release(application,revision):
    if revision!=application.metadata.get('revision') or not application.driver.runtime_source_ready():
        raise Rejected('The requested runtime is not the verified required release')
    ref=release_ref(revision)
    github=application.broker.github
    token=github.installation_token(ENGINE,permissions={'contents':'write'})
    url=f'https://api.github.com/repos/{ENGINE}/git/ref/'+ref.removeprefix('refs/')
    try:
        observed=github.request(url,token=f'Bearer {token}')
    except RequestError as exc:
        if exc.status!=404:
            raise
        try:
            github.request(f'https://api.github.com/repos/{ENGINE}/git/refs',token=f'Bearer {token}',
                           method='POST',data={'ref':ref,'sha':revision})
        except RequestError:
            pass  # Ref creation is unique/idempotent; verify even after a lost reply.
        observed=github.request(url,token=f'Bearer {token}')
    if (observed.get('ref')!=ref or observed.get('object',{}).get('sha')!=revision
            or observed.get('object',{}).get('type')!='commit'):
        raise Rejected('The runtime reference does not identify the verified commit')
    return {'revision':revision,'ref':ref,'verified':True}
