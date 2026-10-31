"""Causality for signed readiness submissions and controller status projection.

History can veto a projection or validate a signed action. It never supplies
submission authority: the original authenticated webhook is required as well.
"""

from .model import Rejected


def state_change(entry):
    return bool(entry.get('toState')) and (entry.get('fromState') or {}).get('id') != entry['toState']['id']


def eligibility_change(entry):
    return (entry.get('archived') is not None or entry.get('trashed') is not None
            or bool(entry.get('fromProjectId') or entry.get('toProjectId')))


def owned_attention(entry, blocked):
    actor = entry.get('actor') or {}
    return (actor.get('isMe') is True and actor.get('app') is True
            and state_change(entry) and (entry.get('toState') or {}).get('id') == blocked
            and not eligibility_change(entry))


def changes(snapshot):
    entries = snapshot['history']
    states = [entry for entry in entries if state_change(entry)]
    if states:
        latest = max(entry['changed_at'] for entry in states)
        if not any(entry['changed_at'] == latest and entry['toState']['id'] == snapshot['state']['id']
                   for entry in states):
            raise Rejected('Linear state history has not caught up with the current state')
    return [entry for entry in entries if state_change(entry) or eligibility_change(entry)]


def submission_snapshot(snapshot, submission, blocked):
    """Resolve only our own attention projection over an exact signed transition.

    Any subsequent foreign workflow/eligibility action withdraws the intent,
    including a bot moving away and back to Todo. Missing causal evidence defers
    the inbox item instead of acknowledging it as an ineligible submission.
    """
    recent = [entry for entry in changes(snapshot) if entry['changed_at'] >= submission['changed_at']
              and not owned_attention(entry, blocked)]
    matching = [entry for entry in recent if entry['changed_at'] == submission['changed_at']
                and (entry.get('actor') or {}).get('id') == submission['actor_id']
                and (entry.get('actor') or {}).get('app') is False and not entry.get('botActor')
                and (entry.get('fromState') or {}).get('id') == submission['from_state']
                and (entry.get('toState') or {}).get('id') == submission['to_state']]
    if not matching:
        raise Rejected('Signed human transition is not yet present in Linear history')
    latest = max(entry['changed_at'] for entry in recent)
    current = [entry for entry in recent if entry['changed_at'] == latest]
    valid = len(current) == 1 and current[0] in matching
    # Keep cancellation authoritative even if an in-flight controller request
    # landed after it. Current project/archive/enrollment checks still apply.
    states = [entry for entry in recent if state_change(entry)]
    if states:
        last = max(entry['changed_at'] for entry in states)
        states = [entry for entry in states if entry['changed_at'] == last]
        if len(states) != 1:
            raise Rejected('Ambiguous workflow action order')
        snapshot = {**snapshot, 'state': states[0]['toState']}
    return snapshot, valid


def may_project_attention(snapshot, attention_at, blocked):
    return not any(entry['changed_at'] > attention_at and not owned_attention(entry, blocked)
                   for entry in changes(snapshot))
