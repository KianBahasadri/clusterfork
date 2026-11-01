"""Execution policy private to Controller; adapters supply identities and facts."""

import json

from .model import Rejected, Role, dependency_admission_applies


OPEN_STAGES = {'initializing', 'selecting_account', 'coding', 'testing'}
ACTIONS = {'execute', 'start', 'validate_context', 'context', 'checkout', 'github', 'command', 'step_result',
           'heartbeat', 'accounts', 'claim', 'bind_account', 'exhaust_account', 'review',
           'progress', 'steering_open', 'steering_close', 'instructions', 'instruction_result'}


def admission(db, task, *, allow_hold=False):
    from .linear_state import human_withdrawal
    if not db.execute('SELECT 1 FROM grants WHERE repository=? AND task_id=?',
                      (task['repository'], task['task_id'])).fetchone():
        raise Rejected('Task does not own repository admission')
    if (task['canceled'] or not task['eligible'] or human_withdrawal(db, task)
            or task['phase'] in {'completed', 'canceled', 'skipped'}
            or task['phase'] == 'held' and not allow_hold):
        raise Rejected('Task authorization is held')
    attempt = db.execute('SELECT coding_started FROM attempts WHERE attempt_id=?',
                         (task['current_attempt'],)).fetchone()
    if not task['dependencies_ready'] and dependency_admission_applies(attempt):
        raise Rejected('Prerequisites are unresolved')


def authorize(db, task, attempt, role, action, now, accounts):
    if not isinstance(role, Role) or action not in ACTIONS:
        raise Rejected('Unsupported execution authority')
    admission(db, task)
    if attempt['stopped']:
        raise Rejected('This attempt already stopped')
    if role.phase != attempt['execution_phase']:
        raise Rejected('The controller has not dispatched this execution phase')
    session = db.execute('SELECT * FROM sessions WHERE attempt_id=? AND phase=?',
                         (attempt['attempt_id'], role.phase)).fetchone()
    # These queries run before Start creates the phase session. Start itself
    # still verifies the exact successful GitHub review job in Controller.
    if action in {'start', 'validate_context'}:
        if session and (session['finished'] or session['deadline'] <= now):
            raise Rejected('Execution phase is finished or expired')
        if role.phase == 'coding' and json.loads(attempt['review_result'] or '{}').get('verdict') != 'ready':
            raise Rejected('A valid successful review job is required before coding')
    elif (not session or session['finished'] or session['deadline'] <= now
          or not role.selector and not session['agent_started']
          or role.selector and session['agent_started']):
        raise Rejected('Execution phase is not active')
    if role.selector and action not in {'start', 'validate_context', 'execute', 'heartbeat', 'accounts', 'claim', 'bind_account'}:
        raise Rejected('Initial account selection must finish before execution access')
    if action == 'github' and (role != Role.CODING or task['phase'] not in {'executing', 'published'}):
        raise Rejected('Publication is not authorized in this task phase')
    if action == 'github':
        from .execution import current
        step = current(db, attempt['attempt_id'])
        if not step or step['kind'] not in {'publishing', 'preserving'}:
            raise Rejected('The controller has not authorized a push')
    if action == 'review' and role != Role.REVIEW:
        raise Rejected('Only the review phase may assess readiness')
    if action in {'progress', 'exhaust_account'} and role != Role.CODING:
        raise Rejected('Only the coding phase may advance development')
    data = json.loads(session['data']) if session else {}
    if action in {'steering_open', 'steering_close', 'instructions', 'instruction_result'}:
        stages = OPEN_STAGES if action == 'instructions' else {'coding', 'testing'} if action == 'steering_close' else {'coding'}
        if (role != Role.CODING or task['phase'] != 'executing' or attempt['stage'] not in stages
                or data.get('steering_closed') or data.get('heartbeat_until', 0) <= now):
            raise Rejected('The agent is no longer accepting instructions')
    # Reading context always needs a live execution lease. Revalidation during
    # trusted WIP preservation can follow an already fenced credential release.
    lease_required = action == 'context'
    if lease_required or action in {'validate_context', 'github'} and role.phase == 'coding' and attempt['coding_started']:
        if (not session or not session['account_id'] or lease_required and data.get('account_reserved')):
            raise Rejected('Related context requires an active execution account lease')
        if lease_required or data.get('released_account_id') != session['account_id']:
            account, _ = accounts.get(session['account_id'])
            owner = f"{attempt['attempt_id']}:{attempt['run_id']}:{attempt['run_attempt']}:{role.phase}"
            if account.get('owner') != owner or account.get('lease_until', 0) <= now:
                raise Rejected('Related context requires an active execution account lease')
