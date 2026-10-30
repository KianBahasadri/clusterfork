"""Scoped API/storage budgets; fixture setup never consumes the measured budget."""

from collections import Counter
from unittest.mock import Mock


def record_api_calls(client):
    """Give a stateful fake the same call history as a Mock(spec=Linear/GitHub)."""
    client.api_calls = Mock()
    for name in dir(type(client)):
        method = getattr(client, name)
        if not name.startswith("_") and callable(method):
            recorded = Mock(wraps=method)
            client.api_calls.attach_mock(recorded, name)
            setattr(client, name, recorded)
    return client


class CallBudget:
    """Assert exact/upper-bound deltas; every unlisted operation must cost zero.

    Targets are service mocks, recorded stateful fakes, or MemoryTables. Counts
    include failed attempts. Budgets use names such as ``linear.issue`` or
    ``queue.replace`` and do not reset mock behavior or earlier call histories.
    """

    def __init__(self, case, *, exact=None, at_most=None, **targets):
        self.case, self.targets = case, targets
        self.exact, self.at_most = exact or {}, at_most or {}
        if self.exact.keys() & self.at_most.keys():
            raise ValueError("An operation needs either an exact count or an upper bound")

    def counts(self):
        counts = Counter()
        for name, target in self.targets.items():
            if isinstance(target, Mock):
                operations = Counter(call[0] for call in target.mock_calls)
            elif hasattr(target, "api_calls"):
                operations = Counter(call[0] for call in target.api_calls.mock_calls)
            else:
                operations = target.operations
            counts.update({f"{name}.{operation}": count for operation, count in operations.items()})
        return counts

    def __enter__(self):
        self.before = self.counts()
        self.mutation_starts = {name: len(target.mutations) for name, target in self.targets.items()
                                if not isinstance(target, Mock) and hasattr(target, "mutations")}
        return self

    def mutated_rows(self, name):
        """Row keys touched by attempted mutations in this scope, including batches."""
        return {key for _, keys in self.targets[name].mutations[self.mutation_starts[name]:] for key in keys}

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            return
        after = self.counts()
        counts = {name: after[name] - self.before[name] for name in after.keys() | self.before.keys()}
        names = ({name for name, count in counts.items() if count} | self.exact.keys()) - self.at_most.keys()
        self.case.assertEqual({name: counts.get(name, 0) for name in names},
                              {name: self.exact.get(name, 0) for name in names},
                              "API/storage operation budget (unlisted calls must be zero)")
        for name, maximum in self.at_most.items():
            self.case.assertLessEqual(counts.get(name, 0), maximum, f"{name} operation budget")
