"""Pure projections preserve existing attempt usage and single-repair feedback."""

import json

from common.diagnostics import failure_summary
from common.policy import redact_review_text
from common.accounts import account_provider
from common.reporting import CLARIFICATION_LABEL, QUEUED_LABEL, SELECTION_LABEL, STAGE_LABELS, attempt_comment

from .workflows import ENGINE


def execution_status(view, reviews):
    """Project the single visible indicator without changing execution authority."""
    if view.phase in {'completed', 'canceled', 'skipped'}:
        return None
    # Resubmission and infrastructure retries cannot resolve earlier feedback.
    for review in reversed(reviews):
        verdict = review['result']['verdict']
        if verdict == 'needs_clarification':
            return CLARIFICATION_LABEL
        if verdict == 'ready':
            break
    if view.phase == 'executing':
        return STAGE_LABELS[view.attempt['stage']]
    if view.phase == 'published':
        return STAGE_LABELS['review']
    if view.phase == 'held' and view.attempt.get('retry_kind') == 'capacity':
        return STAGE_LABELS['waiting_for_quota']
    review = json.loads(view.attempt.get('review_result') or 'null')
    denied = review and review.get('verdict') in {'rejected', 'needs_clarification'}
    if view.active and not denied:
        return SELECTION_LABEL
    if view.phase == 'queued' and view.eligible:
        return QUEUED_LABEL
    return None


def review_report(review,registry):
    result=review['result']
    verdict=result['verdict']
    body='Clusterfork readiness review: '+{'ready':'passed','retry':'waiting for retry'}.get(verdict,verdict.replace('_',' '))+'.\n\n'
    body+=result['summary']
    if verdict=='ready':
        body+='\n\nThis was a read-only assessment. Tests run during development.'
    if verdict in {'rejected','needs_clarification'}:
        for blocker in result.get('blockers',[]):
            body+=f"\n\n{blocker['decision']}\nConsequence: {blocker['consequence']}\nQuestion: {blocker['question']}"
        body+='\n\nAddress the feedback, then move the issue back to Todo for a new review.'
        if registry.notify_user_url:
            body+='\n\n'+registry.notify_user_url
    if review['run_id']:
        body+=f"\n\n[Readiness run](https://github.com/{ENGINE}/actions/runs/{review['run_id']}/attempts/{review['run_attempt']})"
    return redact_review_text(body)


def attempt_reports(execution,registry):
    attempt,session=execution['attempt'],execution['session']
    data=session['data']
    if not data.get('usage_before'):
        return ()
    segments=data.get('usage_segments',[])
    if segments and data.get('usage_after') and 'usage_after' not in segments[-1]:
        segments=[*segments[:-1],{**segments[-1],'usage_after':data['usage_after']}]
    first=segments[0] if segments else {'account_id':data['original_account_id']}
    if len(segments)<=1:
        first={'account_email':data.get('account_email',''),'account_plan':data.get('account_plan',''),**first}
    provider=data.get('provider') or account_provider(first['account_id'])
    profile=registry.agent_profiles[provider]
    finished=bool(session['finished'] or attempt['stopped'])
    run={**first,'provider':provider,'model':data.get('model',profile.model),'effort':data.get('effort',profile.effort),
         'usage_before':json.dumps(data['usage_before']),'usage_after':json.dumps(data['usage_after']) if data.get('usage_after') else '',
         'usage_segments':json.dumps(segments),'exhausted_account_ids':json.dumps(data.get('exhausted_account_ids',[])),
         'started_at':data.get('agent_started_at',data.get('steering_started_at')),'finished_at':data.get('finished_at'),
         'status':attempt['agent_outcome'] or ('failed' if attempt['stopped'] else 'running'),
         'owner':f"{attempt['run_id']}:{attempt['run_attempt']}",
         'failure':json.dumps(data['failure']) if data.get('failure') else ''}
    result=[('start',attempt_comment(run,ENGINE))]
    if finished:
        result.append(('finish',attempt_comment(run,ENGINE,finished=True)))
    return tuple(result)


def repair_terminal(view):
    return (view.attempt.get('attempt_kind')=='repair' and view.attempt.get('stopped')
            and view.attempt.get('all_stopped') and view.attempt.get('publication_checked')
            and (view.phase=='completed' or view.phase=='held' and not view.attempt.get('retry_kind')))


def repair_report(view,parent,registry):
    attempt=view.attempt
    body=(registry.notify_user_url+'\n\n' if registry.notify_user_url else '')+'Clusterfork Auto-Repair Report\n\n'
    body+='Outcome: '+('completed; repository queue can advance.' if view.phase=='completed' else 'held; operator review is required.')
    if parent:
        body+=f"\n\n[Original run](https://github.com/{ENGINE}/actions/runs/{parent['run_id']}/attempts/{parent['run_attempt']})"
        if parent['data'].get('failure'):
            body+='\n\n'+failure_summary(parent['data']['failure'])
    body+=f"\n\n[Repair run](https://github.com/{ENGINE}/actions/runs/{attempt['run_id']}/attempts/{attempt['run_attempt']})"
    if attempt.get('pull_request'):
        body+='\n\n[Pull request]('+attempt['pull_request']+')'
    session=view.session('coding')
    result=session['data'].get('repair_result') if session else None
    if result:
        body+=f"\n\nPrepared commit: `{result['commit_sha']}`. Changed files: {result['changed_files']}. Configured validation passed."
    body+='\n\n'+redact_review_text(view.reason)
    return body
