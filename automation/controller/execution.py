"""Durable executor commands, private to the task controller's transaction."""

import json
import re

from .model import Rejected, identity
from common.policy import MAX_CORRECTION_TURNS
from common.reporting import worker_branch
from common.diagnostics import failure_summary, validate_failure


def current(db, attempt_id):
    row = db.execute('SELECT * FROM execution_steps WHERE attempt_id=? ORDER BY ordinal DESC LIMIT 1',
                     (attempt_id,)).fetchone()
    return {**dict(row), 'inputs': json.loads(row['inputs'])} if row else None


def issue(db, attempt_id, kind, inputs=None):
    ordinal = db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM execution_steps WHERE attempt_id=?',
                          (attempt_id,)).fetchone()[0]
    db.execute('INSERT INTO execution_steps(step_id,attempt_id,ordinal,kind,inputs) VALUES (?,?,?,?,?)',
               (identity(attempt_id, 'step', ordinal), attempt_id, ordinal, kind, json.dumps(inputs or {})))
    return current(db, attempt_id)


def result(controller, db, task, attempt, command, now):
    receipt = json.dumps({'outcome': command.outcome, 'result': command.result}, sort_keys=True)
    row = db.execute('SELECT * FROM execution_steps WHERE step_id=? AND attempt_id=?',
                     (command.step_id, attempt['attempt_id'])).fetchone()
    if not row or command.outcome not in {'completed', 'failed'} or len(receipt.encode()) > 96000:
        raise Rejected('Invalid executor result')
    if row['result']:
        if row['result'] != receipt:
            raise Rejected('Executor result conflicts with its committed receipt')
        return
    step = current(db, attempt['attempt_id'])
    if step['step_id'] != command.step_id or step['kind'] == 'finished':
        raise Rejected('Executor command is no longer current')
    values = command.result
    if command.outcome == 'failed':
        diagnostic = validate_failure(values.get('failure', {}))
        corrections = db.execute("SELECT COUNT(*) FROM execution_steps WHERE attempt_id=? AND kind='coding'",
                                  (attempt['attempt_id'],)).fetchone()[0] - 1
        session = db.execute("SELECT * FROM sessions WHERE attempt_id=? AND phase='coding'", (attempt['attempt_id'],)).fetchone()
        if step['kind'] == 'testing' and corrections < MAX_CORRECTION_TURNS and diagnostic['kind'] != 'lease_lost' and now < session['deadline']:
            check = values.get('check', 'Repository validation')
            if not isinstance(check, str) or len(check) > 200:
                raise Rejected('Invalid validation check')
            prompt = (f'Worker check failed: {check} (correction {corrections+1}/{MAX_CORRECTION_TURNS}).\n'
                      f'{failure_summary(diagnostic)}\nScrubbed stdout/stderr tail:\n'
                      f"{diagnostic.get('failure_output', '(no subprocess output)')}\n\n"
                      'Fix the cause in the existing workspace, retaining the task and authorized follow-ups. '
                      'Do not commit, push, open a PR, or change authentication. '
                      'The controller will request validation again.')
            following = issue(db, attempt['attempt_id'], 'coding', {'prompt': prompt})
        else:
            following = issue(db, attempt['attempt_id'], 'preserving')
    elif step['kind'] == 'coding':
        following = issue(db, attempt['attempt_id'], 'testing')
    elif step['kind'] == 'testing':
        following = issue(db, attempt['attempt_id'], 'publishing')
        session = db.execute("SELECT data FROM sessions WHERE attempt_id=? AND phase='coding'", (attempt['attempt_id'],)).fetchone()
        data = {**json.loads(session[0]), 'steering_closed': True, 'repair_validation_passed': True}
        db.execute("UPDATE sessions SET data=? WHERE attempt_id=? AND phase='coding'", (json.dumps(data), attempt['attempt_id']))
    elif step['kind'] == 'publishing':
        branch = worker_branch(task['identifier'], task['task_id'])
        if (set(values) != {'branch', 'commit_sha', 'title', 'body', 'has_changes', 'changed_files'}
                or values['branch'] != branch or not isinstance(values['commit_sha'], str)
                or not re.fullmatch('[0-9a-f]{40}', values['commit_sha']) or type(values['has_changes']) is not bool
                or type(values['changed_files']) is not int or not 0 <= values['changed_files'] <= 100000
                or any(not isinstance(values[key], str) or not 0 < len(values[key].encode()) <= limit
                       for key, limit in (('title', 1000), ('body', 64000)))
                or f"<!-- clusterfork-dispatch:{task['task_id']} -->" not in values['body']):
            raise Rejected('Invalid prepared publication')
        session = db.execute("SELECT data FROM sessions WHERE attempt_id=? AND phase='coding'", (attempt['attempt_id'],)).fetchone()
        data = {**json.loads(session[0]), 'publication': values,
                'repair_result': {'commit_sha': values['commit_sha'], 'changed_files': values['changed_files']}}
        db.execute("UPDATE sessions SET data=? WHERE attempt_id=? AND phase='coding'", (json.dumps(data), attempt['attempt_id']))
        if values['has_changes']:
            db.execute('INSERT INTO effects(effect_id,task_id,kind,payload) VALUES (?,?,?,?)',
                       (identity(attempt['attempt_id'], 'publication'), task['task_id'], 'publication',
                        json.dumps({'attempt_id': attempt['attempt_id']})))
        following = issue(db, attempt['attempt_id'], 'finished')
    else:
        raise Rejected('Executor command cannot advance')
    db.execute('UPDATE execution_steps SET result=? WHERE step_id=?', (receipt, command.step_id))
    stage = following['kind'] if following['kind'] in {'coding', 'testing', 'publishing'} else attempt['stage']
    db.execute('UPDATE attempts SET stage=?,coding_started=1 WHERE attempt_id=?', (stage, attempt['attempt_id']))
    controller._update(db, task, phase='executing', reason='Controller stage: ' + following['kind'])
