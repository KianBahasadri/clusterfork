"""Durable automatic-execution policy, independent of workflow display states."""


KEY = 'execution:autonomous-v1'


def enabled(db):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (KEY,)).fetchone()
    return bool(row and row[0] == '1')


def retry_delay(ordinal):
    """Persisted attempt history slows repeated failures without a human gate."""
    return min(3600, 60 * 2 ** min(max(ordinal - 1, 0), 6))
