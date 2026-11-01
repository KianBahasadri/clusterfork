"""Controller-owned PR and merge effects; executors never mutate PRs."""

import json
import logging
from types import SimpleNamespace

from .context import Context
from .model import EffectUpdated, PublicationResolved, Rejected, EffectFailed
from common.context import ContextError
from common.clients import RequestError
from common.images import image_digest
from common.publication import merge_ready
from common.routing import DispatchError


def validate(driver, view):
    driver.refresh(view)
    view = driver.controller.publication_authority(view.task_id, view.attempt['attempt_id'])
    route = driver.route(view)
    issue = driver.linear.issue(view.issue_id)
    if image_digest(driver.images.snapshot(issue, revalidate=True)) != view.spec.images_sha:
        raise Rejected('Publication images changed')
    context = Context(driver.controller.database, driver.linear, driver.github)
    try:
        context.revalidate(view, {r.repository.lower() for r in driver.registry.routes.values()},
                           lambda: driver.controller.publication_authority(view.task_id, view.attempt['attempt_id']), approved=True)
    except (RequestError, ContextError) as exc:
        driver.controller.context_failure(view.task_id, view.attempt['attempt_id'], exc, now=driver.clock())
    driver.controller.publication_authority(view.task_id, view.attempt['attempt_id'])
    return route


def perform(driver, view, effect, operation):
    driver.apply(EffectUpdated(view.task_id, effect['effect_id'], 'claimed'))
    try:
        return operation()
    except DispatchError as exc:
        driver.apply(EffectFailed(view.task_id, view.attempt['attempt_id'], effect['effect_id'], not exc.uncertain))
        raise
    except RequestError:
        driver.apply(EffectFailed(view.task_id, view.attempt['attempt_id'], effect['effect_id'], False))
        raise


def reconcile(driver):
    for kind in ('publication', 'merge'):
        for effect in driver.controller.effects(kind):
            try:
                view = driver.controller.inspect(effect['task_id'])
                payload = json.loads(effect['payload'])
                if payload['attempt_id'] != view.attempt['attempt_id']:
                    continue
                if not view.active:
                    if view.attempt['publication_checked'] and view.attempt['pull_state'] in {'merged', 'closed', 'absent'}:
                        driver.apply(EffectUpdated(view.task_id, effect['effect_id'], 'done'))
                    continue
                if payload.get('retry_at', 0) > driver.clock():
                    continue
                route = validate(driver, view)
                plan = view.session('coding')['data']['publication']
                receipt = {'identifier': view.identifier, 'target_dispatch_key': view.task_id, 'target_branch': plan['branch']}
                pull = driver.github.worker_pull(route, receipt, view.task_id, view.attempt['pull_request'])
                if kind == 'publication':
                    if pull is None:
                        if effect['state'] != 'pending':
                            continue  # Discover an ambiguous create; never create blindly again.
                        head = driver.github.branch_sha(SimpleNamespace(repository=route.repository, ref=plan['branch']))
                        if head != plan['commit_sha']:
                            raise Rejected('Prepared publication branch changed')
                        driver.controller.publication_authority(view.task_id, view.attempt['attempt_id'])
                        created = perform(driver, view, effect, lambda: driver.github.create_worker_pull(route, plan))
                        pull = driver.github.worker_pull(route, receipt, view.task_id, created['html_url'])
                    if not pull or pull.get('headRefOid') != plan['commit_sha']:
                        raise Rejected('Published PR does not match the validated commit')
                    state = 'merged' if pull.get('merged') else pull['state'].lower()
                    driver.apply(PublicationResolved(view.task_id, view.attempt['attempt_id'], effect['effect_id'], pull['url'], state))
                elif pull and pull.get('merged'):
                    driver.apply(EffectUpdated(view.task_id, effect['effect_id'], 'done'))
                elif (effect['state'] == 'pending' and pull and pull.get('headRefOid') == payload['commit_sha']
                      and merge_ready(pull, route.required_checks)):
                    driver.controller.publication_authority(view.task_id, view.attempt['attempt_id'])
                    perform(driver, view, effect, lambda: driver.github.merge_worker_pull(route, pull['url'], payload['commit_sha']))
                    driver.apply(EffectUpdated(view.task_id, effect['effect_id'], 'done'))
            except Rejected:
                continue
            except DispatchError:
                logging.warning('Controller %s deferred for %s', kind, effect['task_id'][:12])
            except RequestError:
                # Read failures do not prove an outcome. A claimed mutation is
                # retained for discovery, including when its response was lost.
                logging.warning('Controller %s deferred for %s', kind, effect['task_id'][:12])
            except Exception as exc:
                logging.warning('Controller %s deferred for %s (%s)', kind, effect['task_id'][:12], type(exc).__name__)
