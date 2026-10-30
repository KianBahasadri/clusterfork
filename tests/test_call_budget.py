"""Budgets catch attempted I/O even when fake state does not change."""

import unittest
from unittest.mock import Mock

from call_budget import CallBudget, record_api_calls
from test_remote_worker import MemoryTable
from worker_core import WorkerError


class CallBudgetTests(unittest.TestCase):
    def test_scope_excludes_setup_and_supports_exact_and_bounded_mock_calls(self):
        client = Mock()
        client.issue.return_value = {"id": "active"}
        client.issue("setup")
        with CallBudget(self, linear=client, exact={"linear.issue": 1}, at_most={"linear.status_comment": 1}):
            self.assertEqual(client.issue("active"), {"id": "active"})
            client.status_comment("active", "ack", "accepted")
        self.assertEqual(client.issue.call_count, 2)
        with CallBudget(self, linear=client):
            pass

    def test_unlisted_and_over_budget_calls_fail_for_mocks_and_stateful_fakes(self):
        class Client:
            def issue(self, identifier):
                return identifier
        for client in (Mock(), record_api_calls(Client())):
            for limits in ({}, {"exact": {"linear.issue": 1}}, {"at_most": {"linear.issue": 1}}):
                with self.subTest(client=type(client).__name__, limits=limits), self.assertRaises(AssertionError):
                    with CallBudget(self, linear=client, **limits):
                        client.issue("one")
                        client.issue("two")
            with CallBudget(self, linear=client, exact={"linear.issue": 1}):
                client.issue("one")

    def test_counts_failed_creates_replacements_and_transactions_and_their_row_keys(self):
        table = MemoryTable()
        row = {"RowKey": "history", "value": "unchanged"}
        table.create(row)
        with CallBudget(self, table=table, exact={"table.create": 1, "table.replace": 2,
                                                "table.transaction": 2}) as budget:
            self.assertFalse(table.create(row))
            table.replace(row, 1)
            with self.assertRaises(WorkerError):
                table.replace(row, 1)
            with self.assertRaises(WorkerError):
                table.transaction([(row, 1), ({"RowKey": "new"}, None)])
            self.assertNotIn("new", table.rows)
            table.transaction([(row, 2), ({"RowKey": "new"}, None)])
        self.assertEqual(budget.mutated_rows("table"), {"history", "new"})
        # Replacing an unchanged row is still an externally billed operation.
        with self.assertRaises(AssertionError):
            with CallBudget(self, table=table):
                table.replace(row, 3)
