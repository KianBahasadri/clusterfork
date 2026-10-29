"""Durable retry markers for external writes that Azure ETags cannot fence."""

import json
import time
from uuid import uuid4


# Longer than the bounded Linear requests in a report, shorter than the timer.
# A lost process cannot acknowledge its writes; the next timer can retire them.
WRITE_SECONDS = 300


class PendingWrites:
    def __init__(self, prefix, clock=time.time):
        self.pending = prefix + "_pending"
        self.inflight = prefix + "_inflight"
        self.clock = clock

    def live(self, row):
        now = self.clock()
        return {token: until for token, until in json.loads(row.get(self.inflight, "{}")).items()
                if until > now}

    def begin(self, row):
        """Mutate a row to persist BEFORE starting an external write."""
        token = str(uuid4())
        writes = self.live(row)
        writes[token] = self.clock() + WRITE_SECONDS
        row.update({self.pending: True, self.inflight: json.dumps(writes)})
        return token

    def finish(self, row, token, *, retry=False):
        """Acknowledge only this writer; concurrent writers retain retry intent."""
        writes = self.live(row)
        writes.pop(token, None)
        row[self.pending] = retry or bool(writes)
        if writes:
            row[self.inflight] = json.dumps(writes)
        else:
            row.pop(self.inflight, None)
