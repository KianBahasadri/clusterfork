"""Durable automatic-execution policy, independent of workflow display states."""

import json

KEY = 'execution:autonomous-v1'
RETIRED = 'execution:retired-attention-v1'


def enabled(db):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (KEY,)).fetchone()
    return bool(row and row[0] == '1')


def retired_states(db):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (RETIRED,)).fetchone()
    return set(json.loads(row[0])) if row else set()


def remember_retired_states(db):
    """Keep historical attention intent harmless after its workflow mapping goes."""
    policies = [json.loads(row[0]) for row in db.execute('SELECT policy FROM linear_workflows')]
    retired = retired_states(db) | {p['blocked'] for p in policies if p.get('blocked')}
    active = {value for p in policies for value in (*p['ready'], p['in_progress'], p['done'])}
    # Recover first-rollout history even if observation already removed the old
    # mapping. Human pause/restoration effects are never migration evidence.
    for row in db.execute("SELECT payload FROM effects WHERE kind='linear_state'"):
        effect = json.loads(row[0])
        if effect.get('attention_at') and not effect.get('human') and effect['state'] not in active:
            retired.add(effect['state'])
    db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (RETIRED, json.dumps(sorted(retired))))


def retry_delay(ordinal):
    """Persisted attempt history slows repeated failures without a human gate."""
    return min(3600, 60 * 2 ** min(max(ordinal - 1, 0), 6))
