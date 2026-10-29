"""Small, credential-free timing records for webhook-to-selection diagnosis."""

import json
import logging
import math
import time


TIMESTAMPS = ("source_updated_at", "webhook_at", "received_at", "enqueued_at", "processing_at",
              "queued_at", "selected_at")


def timestamps(values):
    return {name: float(values[name]) for name in TIMESTAMPS
            if type(values.get(name)) in (int, float) and math.isfinite(values[name]) and values[name] > 0}


def record(phase, *, at=None, **fields):
    # Callers supply only identifiers, timestamps, versions, and outcomes.
    logging.info("Clusterfork timing: %s", json.dumps(
        {"phase": phase, "at": time.time() if at is None else at, **fields}, sort_keys=True))
