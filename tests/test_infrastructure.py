"""Safety gates around real Terraform plans and private diagnostic output."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from infrastructure import InfrastructureError, check_plan, diagnostics


class InfrastructureTests(unittest.TestCase):
    def test_review_rejects_replacements_and_deletions_before_apply(self):
        for actions in (["delete"], ["create", "delete"], ["delete", "create"]):
            with self.subTest(actions=actions), self.assertRaises(InfrastructureError):
                check_plan({"resource_changes": [{"address": "azurerm_storage_account.runtime",
                                                 "change": {"actions": actions}}]})

    def test_review_allows_imports_noops_and_updates_without_rendering_sensitive_values(self):
        changes = [
            {"address": "old", "change": {"actions": ["no-op"], "importing": {"id": "existing"}}},
            {"address": "new", "change": {"actions": ["create"], "after": {"secret": "private"}}},
            {"address": "changed", "change": {"actions": ["update"]}},
        ]
        self.assertEqual(check_plan({"resource_changes": changes}), {"create": 1, "update": 1})

    def test_forgetting_a_read_only_data_source_is_not_a_resource_deletion(self):
        self.assertEqual(check_plan({"resource_changes": [
            {"address": "data.azurerm_linux_function_app.old", "mode": "data", "change": {"actions": ["delete"]}}
        ]}), {})

    def test_diagnostic_summary_excludes_values_context_and_raw_cli_output(self):
        message = {"type": "diagnostic", "diagnostic": {
            "severity": "error", "summary": "Invalid argument", "detail": "SECRET",
            "expression_values": [{"value": "SECRET"}],
            "range": {"filename": "main.tf", "start": {"line": 7}},
        }}
        output = diagnostics("raw SECRET\n" + json.dumps(message))
        self.assertEqual(output, [{"severity": "error", "summary": "Invalid argument", "file": "main.tf", "line": 7}])
        self.assertNotIn("SECRET", json.dumps(output))


if __name__ == "__main__":
    unittest.main()
