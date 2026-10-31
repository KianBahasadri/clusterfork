"""Causality for signed readiness submissions and controller status projection.

History can veto a projection or validate a signed action. It never supplies
submission authority: the original authenticated webhook is required as well.
"""

import json
import math

from .model import Rejected, identity


def intent(db, issue_id):
    row = db.execute('SELECT * FROM linear_intents WHERE issue_id=?', (issue_id,)).fetchone()
    return {**dict(row), 'transition': json.loads(row['transition'])} if row else None


def record_intent(db, event):
    """Called in the inbox commit, before HTTP acknowledgement; never from scans."""
    transition = event.get('submission')
    if event.get('type') != 'Issue' or not transition:
        return False
    if (set(transition) != {'actor_id', 'from_state', 'to_state', 'changed_at'}
            or type(transition['changed_at']) not in (int, float)
            or not math.isfinite(transition['changed_at'])):
        raise Rejected('Invalid human transition receipt')
    previous = intent(db, event['id'])
    key = identity(event['id'], transition)
    if previous and previous['changed_at'] > transition['changed_at']:
        return False
    if previous and previous['changed_at'] == transition['changed_at']:
        if previous['action_id'] != key:
            db.execute('UPDATE linear_intents SET conflicted=1 WHERE issue_id=?', (event['id'],))
        return False
    db.execute('INSERT INTO linear_intents VALUES (?,?,?,?,0,0) ON CONFLICT(issue_id) DO UPDATE SET '
               'action_id=excluded.action_id,changed_at=excluded.changed_at,transition=excluded.transition,conflicted=0,withdrawn=0',
               (event['id'], key, transition['changed_at'], json.dumps(transition, sort_keys=True)))
    return True


def workflow(db, task_id):
    row = db.execute('SELECT policy FROM linear_workflows WHERE task_id=?', (task_id,)).fetchone()
    return json.loads(row[0]) if row else None


def human_withdrawal(db, task):
    latest, policy = intent(db, task['issue_id']), workflow(db, task['task_id'])
    return bool(latest and policy and latest['changed_at'] >= task['review_submitted_at'] and (
        latest['conflicted'] or latest['withdrawn'] or (
            latest['transition']['to_state'] not in policy['ready']
            and latest['transition']['to_state'] != policy.get('in_progress'))))


def observes_workflow(known, policy):
    return bool(known and known.phase not in {'completed', 'canceled', 'skipped'}
                or policy['state'] in policy['ready'] and policy['type'] not in {'completed', 'canceled'})


def observed_eligibility(db, task, observation):
    """Structural enrollment is an input; workflow interpretation belongs here."""
    policy = observation.workflow
    if not policy:
        return observation.eligible
    required = {'state', 'type', 'ready', 'in_progress', 'blocked', 'done'}
    if (set(policy) != required or not isinstance(policy['ready'], (list, tuple)) or not policy['ready']
            or any(not isinstance(policy[key], str) for key in required - {'ready'})):
        raise Rejected('Invalid Linear workflow observation')
    if task:
        db.execute('INSERT INTO linear_workflows VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET policy=excluded.policy',
                   (task['task_id'], json.dumps(policy)))
    if not observation.eligible or policy['type'] in {'canceled', 'completed'}:
        return False
    if task and human_withdrawal(db, task):
        return False
    if policy['state'] in policy['ready']:
        return True
    if not task or policy['state'] != policy['in_progress']:
        return False
    if not db.execute('SELECT 1 FROM grants WHERE task_id=?', (task['task_id'],)).fetchone():
        return False
    desired = desired_state(db, task)
    for row in db.execute("SELECT effect_id,payload FROM effects WHERE task_id=? AND kind='linear_state'", (task['task_id'],)):
        projection = json.loads(row['payload'])
        if (desired and row['effect_id'] == desired[0] and projection['state'] == policy['state']
                and projection.get('outcome') in {'claimed', 'uncertain', 'confirmed'}):
            return True
    # Preserve live pre-v11 execution already reported by this controller.
    return bool(task['phase'] in {'executing', 'published'} and db.execute(
        'SELECT 1 FROM comment_signatures WHERE key=?', ('task:' + task['task_id'],)).fetchone())


def desired_state(db, task):
    policy = workflow(db, task['task_id'])
    if not policy or not policy['in_progress']:
        return None
    latest = intent(db, task['issue_id'])
    if latest and human_withdrawal(db, task):
        if latest['conflicted'] or latest['withdrawn']:
            return None
        # An in-flight controller write may have landed after a human pause or
        # cancellation. Reconcile to the exact signed command, never guess it
        # from our task phase or from delivery order.
        state = latest['transition']['to_state']
        return identity(task['task_id'], 'human_state', latest['action_id']), state
    if task['phase'] in {'canceled', 'skipped'}:
        return None
    attempt = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (task['current_attempt'],)).fetchone()
    attempt = dict(attempt) if attempt else {}
    state = (policy['done'] if task['phase'] == 'completed' else
             policy['blocked'] if task['phase'] == 'held' and not attempt.get('retry_kind') else
             policy['in_progress'] if task['phase'] in {'executing', 'published'} or (
                 attempt.get('attempt_kind') == 'repair' and attempt.get('retry_kind') != 'capacity') else policy['ready'][0])
    epoch = identity(task['task_id'], 'linear_state', task['review_cycle'], task['current_attempt'], state,
                     task['review_attention_at'] if state == policy['blocked'] else 0)
    return epoch, state


def ensure_projection(db, task):
    desired = desired_state(db, task)
    if not desired:
        return
    key, state = desired
    latest = intent(db, task['issue_id'])
    payload = {'state': state, 'attention_at': task['review_attention_at'],
               'intent': latest['action_id'] if latest else '', 'outcome': 'pending',
               'human': bool(latest and human_withdrawal(db, task))}
    policy = workflow(db, task['task_id'])
    if (state == policy['state'] == policy['in_progress'] and task['phase'] in {'executing', 'published'}
            and db.execute('SELECT 1 FROM comment_signatures WHERE key=?', ('task:' + task['task_id'],)).fetchone()
            and not db.execute("SELECT 1 FROM effects WHERE task_id=? AND kind='linear_state'", (task['task_id'],)).fetchone()):
        payload['outcome'] = 'confirmed'
    # Adopt PR #58's independent attention acknowledgement on upgrade.
    if state == policy['blocked']:
        acknowledged = db.execute('SELECT signature FROM comment_signatures WHERE key=?',
                                  (f"review-state:{task['task_id']}:{task['review_cycle']}",)).fetchone()
        if acknowledged and acknowledged[0] == identity(task['review_attention_at'], state):
            payload['outcome'] = 'confirmed'
    db.execute('INSERT OR IGNORE INTO effects VALUES (?,?,?,?,?)',
               (key, task['task_id'], 'linear_state', json.dumps(payload),
                'done' if payload['outcome'] == 'confirmed' else 'pending'))


def update_projection(db, task, command):
    if command.outcome not in {'claimed', 'confirmed', 'uncertain', 'superseded'}:
        raise Rejected('Invalid projection acknowledgement')
    row = db.execute("SELECT * FROM effects WHERE effect_id=? AND task_id=? AND kind='linear_state'",
                     (command.effect_id, task['task_id'])).fetchone()
    if not row:
        raise Rejected('Unknown Linear state effect')
    payload = json.loads(row['payload'])
    if row['state'] == 'done':
        return payload.get('outcome') == 'confirmed'
    if command.outcome == 'claimed':
        latest = intent(db, task['issue_id'])
        desired = desired_state(db, task)
        if (not desired or desired[0] != command.effect_id or latest and (
                latest['conflicted'] or latest['withdrawn'] or latest['action_id'] != payload['intent'])):
            payload['outcome'] = 'superseded'
            db.execute("UPDATE effects SET state='done',payload=? WHERE effect_id=?", (json.dumps(payload), command.effect_id))
            return False
        observation, policy = command.observation, workflow(db, task['task_id'])
        if observation.get('state') == payload['state']:
            return True  # Observation alone can acknowledge an already applied effect.
        if (observation.get('structural') is not True or observation.get('type') in {'completed', 'canceled'}
                or observation.get('state') not in {*policy['ready'], policy['in_progress'], policy['blocked'], payload['state']}):
            return False
        payload['issued'] = True
    payload['outcome'] = command.outcome
    db.execute('UPDATE effects SET state=?,payload=? WHERE effect_id=?',
               ('done' if command.outcome in {'confirmed', 'superseded'} else 'claimed', json.dumps(payload), command.effect_id))
    return True


def state_change(entry):
    return bool(entry.get('toState')) and (entry.get('fromState') or {}).get('id') != entry['toState']['id']


def eligibility_change(entry):
    return (entry.get('archived') is not None or entry.get('trashed') is not None
            or bool(entry.get('fromProjectId') or entry.get('toProjectId')))


def owned_attention(entry, blocked, owned_states=()):
    actor = entry.get('actor') or {}
    return (actor.get('isMe') is True and actor.get('app') is True
            and state_change(entry) and (entry.get('toState') or {}).get('id') in {blocked, *owned_states}
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


def submission_snapshot(snapshot, submission, blocked, *, owned_states=(), tolerance_seconds=300):
    """Resolve only our own attention projection over an exact signed transition.

    Any subsequent foreign workflow/eligibility action withdraws the intent,
    including a bot moving away and back to Todo. Missing causal evidence defers
    the inbox item instead of acknowledging it as an ineligible submission.
    """
    candidates = [entry for entry in changes(snapshot)
                  if abs(entry['changed_at'] - submission['changed_at']) <= tolerance_seconds
                  and (entry.get('actor') or {}).get('id') == submission['actor_id']
                  and (entry.get('actor') or {}).get('app') is False and not entry.get('botActor')
                  and (entry.get('fromState') or {}).get('id') == submission['from_state']
                  and (entry.get('toState') or {}).get('id') == submission['to_state']]
    if not candidates:
        raise Rejected('Signed human transition is not yet present in Linear history')
    matched = min(candidates, key=lambda e: abs(e['changed_at'] - submission['changed_at']))
    recent = [entry for entry in changes(snapshot) if entry['changed_at'] >= matched['changed_at']
              and not owned_attention(entry, blocked, owned_states)]
    latest = max(entry['changed_at'] for entry in recent)
    current = [entry for entry in recent if entry['changed_at'] == latest]
    valid = len(current) == 1 and current[0] == matched
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


def may_project_attention(snapshot, attention_at, blocked, *, owned_states=()):
    return not any(entry['changed_at'] > attention_at and not owned_attention(entry, blocked, owned_states)
                   for entry in changes(snapshot))
