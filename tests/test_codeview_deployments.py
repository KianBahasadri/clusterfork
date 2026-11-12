"""Terraform inventory and pricing without cloud credentials or network access."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / ".codeview/modules/deployments.py"


def load_module():
    spec = importlib.util.spec_from_file_location("deployments_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resource(kind, name, attrs, **instance):
    return {"mode": "managed", "type": kind, "name": name,
            "instances": [{"attributes": attrs, **instance}]}


def meter(rate, unit, **fields):
    return {"type": "Consumption", "currencyCode": "USD", "retailPrice": rate,
            "unitOfMeasure": unit, "effectiveStartDate": "2026-01-01T00:00:00Z",
            "productName": "Linux", "skuName": "Regular", "meterName": "test",
            "isPrimaryMeterRegion": True, **fields}


class DeploymentsTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.root = self.repo / "infra/azure"
        self.root.mkdir(parents=True)

    def test_configuration_fallback_is_unverified_and_excludes_downloaded_modules(self):
        (self.root / "main.tf").write_text('''/*
resource "ignored" "comment" {}
*/
resource "azurerm_resource_group" "main" { name = "# safe // string" }
''')
        ignored = self.root / ".terraform/modules/dependency"
        ignored.mkdir(parents=True)
        (ignored / "main.tf").write_text('resource "ignored" "download" {}')
        with patch.object(self.mod, "read_state", return_value=(None, "Unavailable")), \
             patch.object(self.mod, "retail_prices") as prices:
            data = self.mod.scan(self.repo)
        self.assertEqual(data["recorded"], 0)
        self.assertEqual(len(data["stacks"]), 1)
        self.assertEqual(len(data["resources"]), 1)
        self.assertEqual(data["resources"][0]["status"], "Unverified")
        self.assertEqual(data["resources"][0]["source"], "infra/azure/main.tf:4")
        self.assertIsNone(data["resources"][0]["monthly"])
        prices.assert_not_called()

    def test_state_instances_and_config_only_resources_are_distinct(self):
        (self.root / "main.tf").write_text('''resource "azurerm_resource_group" "main" {}
resource "azurerm_resource_group" "future" {}
''')
        raw = resource("azurerm_resource_group", "main", {"name": "one"}, index_key="one")
        raw["instances"].append({"index_key": "two", "attributes": {"name": "two"}})
        child = resource("azurerm_resource_group", "legacy", {"name": "old"}, deposed="abc")
        child["module"] = "module.child[0]"
        data_source = {"mode": "data", "type": "azurerm_client_config", "name": "current", "instances": []}
        snapshot = {"version": 4, "serial": 7, "resources": [raw, child, data_source]}
        with patch.object(self.mod, "read_state", return_value=(snapshot, "Read")):
            data = self.mod.scan(self.repo)
        self.assertEqual(data["recorded"], 3)
        by_address = {row["address"]: row for row in data["resources"]}
        self.assertEqual(by_address['azurerm_resource_group.main["one"]']["status"], "Recorded")
        self.assertEqual(by_address['azurerm_resource_group.main["two"]']["name"], "two")
        self.assertEqual(by_address["module.child[0].azurerm_resource_group.legacy"]["status"], "Deposed")
        self.assertEqual(by_address["azurerm_resource_group.future"]["status"], "Declared")
        self.assertEqual(data["stacks"][0]["serial"], 7)

    def test_projection_never_returns_secrets_even_when_unmarked(self):
        instance = {"attributes": {
            "name": "vault", "location": "eastus", "value": "SECRET-UNMARKED",
            "primary_access_key": "SECRET-KEY", "connection_string": "SECRET-CONNECTION",
            "body": {"password": "SECRET-BODY"}, "size": "SECRET-MARKED",
            "tags": {"application": "safe", "credential": "SECRET-TAG"},
            "os_disk": [{"disk_size_gb": 32, "storage_account_type": "StandardSSD_LRS", "password": "SECRET-NESTED"}],
        }, "sensitive_attributes": [[{"type": "get_attr", "value": "size"}]]}
        safe = self.mod.safe_attributes(instance)
        self.assertNotIn("SECRET", json.dumps(safe))
        self.assertNotIn("size", safe)
        self.assertEqual(safe["tags"], {"application": "safe"})
        self.assertEqual(safe["os_disk"][0]["disk_size_gb"], 32)

    def cost_rows(self):
        return self.mod.state_resources({"resources": [
            resource("azurerm_linux_virtual_machine", "controller", {
                "name": "vm", "location": "mexicocentral", "size": "Standard_B2ats_v2",
                "os_disk": [{"disk_size_gb": 32, "storage_account_type": "StandardSSD_LRS"}]}),
            resource("azurerm_managed_disk", "data", {
                "name": "disk", "location": "mexicocentral", "disk_size_gb": 32,
                "storage_account_type": "StandardSSD_LRS"}),
            resource("azurerm_public_ip", "ip", {"name": "ip", "location": "mexicocentral", "sku": "Standard"}),
            resource("azurerm_virtual_machine_data_disk_attachment", "data", {"name": "attachment"}),
        ]}, {}, "infra/azure")

    @staticmethod
    def prices(query, _refresh=False):
        if "Virtual Machines" in query:
            items = [meter(0.0103, "1 Hour"),
                     meter(0.00571, "1 Hour", productName="Windows", skuName="Spot"),
                     meter(0.0195, "1 Hour", productName="Windows"),
                     meter(0.003016, "1 Hour", skuName="Spot"),
                     meter(0.001, "1 Hour", type="Reservation")]
        elif "Storage" in query:
            items = [meter(2.64, "1/Month")]
        elif "Virtual Network" in query:
            items = [meter(0.005, "1 Hour")]
        else:
            items = []
        return {"items": items, "url": "https://prices.azure.com/api/retail/prices?source=test", "checked_at": "2026-11-12T12:00:00+00:00"}

    def test_estimate_includes_os_and_data_disks_without_attachment_double_count(self):
        rows = self.cost_rows()
        with patch.object(self.mod, "retail_prices", side_effect=self.prices) as prices:
            self.mod.add_costs(rows)
        self.assertAlmostEqual(sum(row["monthly"] or 0 for row in rows), 16.449)
        self.assertAlmostEqual(rows[0]["monthly"], 10.159)
        self.assertAlmostEqual(rows[0]["hourly"], 0.0103)
        self.assertEqual(len(rows[0]["charges"]), 2)
        self.assertEqual(rows[3]["cost_status"], "Included")
        self.assertEqual(prices.call_count, 3)  # The OS and data disk share a meter.

    def test_missing_disk_price_stays_partial_and_spot_is_not_priced_as_regular(self):
        rows = self.cost_rows()
        def available(query, refresh=False):
            return {"items": []} if "Storage" in query else self.prices(query, refresh)
        with patch.object(self.mod, "retail_prices", side_effect=available):
            self.mod.add_costs(rows)
        self.assertAlmostEqual(rows[0]["monthly"], 7.519)
        self.assertEqual(rows[0]["cost_status"], "Partial estimate")
        self.assertIsNone(rows[1]["monthly"])
        self.assertEqual(rows[1]["cost_status"], "Unpriced")
        rows[0]["details"]["priority"] = "Spot"
        self.assertEqual(self.mod.charge_specs(rows[0]), [])

    def test_ingestion_uses_paid_rate_and_does_not_invent_usage(self):
        rows = self.mod.state_resources({"resources": [resource("azurerm_log_analytics_workspace", "logs", {
            "location": "eastus", "sku": "PerGB2018"})]}, {}, "infra/azure")
        catalog = {"items": [meter(0, "1 GB", tierMinimumUnits=0), meter(2.3, "1 GB", tierMinimumUnits=5)]}
        with patch.object(self.mod, "retail_prices", return_value=catalog):
            self.mod.add_costs(rows)
        self.assertIsNone(rows[0]["monthly"])
        self.assertEqual(rows[0]["charges"][0]["rate"], 2.3)
        self.assertEqual(rows[0]["cost_status"], "Usage based")

    def test_future_wrong_currency_and_ambiguous_rates_are_rejected(self):
        spec = ("Linux compute", "query", "1 Hour", "vm_hours")
        items = [meter(1, "1 Hour", effectiveStartDate="2999-01-01T00:00:00Z"),
                 meter(1, "1 Hour", currencyCode="EUR"), meter(-1, "1 Hour")]
        self.assertIsNone(self.mod.choose_rate({"items": items}, spec))
        self.assertIsNone(self.mod.choose_rate({"items": [meter(1, "1 Hour"), meter(2, "1 Hour")]}, spec))
        regional = meter(0.005, "1 Hour", isPrimaryMeterRegion=False)
        self.assertEqual(self.mod.choose_rate({"items": [regional]}, spec)["retailPrice"], 0.005)

    def test_pagination_and_price_cache(self):
        pages = [{"Items": [meter(1, "1 Hour")], "NextPageLink": "https://prices.azure.com/api/retail/prices?page=2"},
                 {"Items": [meter(2, "1 Hour")], "NextPageLink": None}]
        with patch.object(self.mod, "urlopen", side_effect=[io.BytesIO(json.dumps(page).encode()) for page in pages]) as request:
            first = self.mod.retail_prices("test")
            again = self.mod.retail_prices("test")
        self.assertEqual(len(first["items"]), 2)
        self.assertIs(first, again)
        self.assertEqual(request.call_count, 2)
        page = {"Items": [meter(1, "1 Hour")], "NextPageLink": "https://other.example/private"}
        with patch.object(self.mod, "urlopen", return_value=io.BytesIO(json.dumps(page).encode())) as request:
            result = self.mod.retail_prices("unsafe")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["items"], [])
        self.assertIn("error", result)

    def test_state_read_is_bounded_read_only_and_withholds_raw_diagnostics(self):
        proc = SimpleNamespace(returncode=1, stdout="SECRET-OUT", stderr="SECRET-ERR")
        with patch.object(self.mod.subprocess, "run", return_value=proc) as run, \
             patch.dict(self.mod.os.environ, {"TF_CLI_ARGS": "unexpected"}):
            data, message = self.mod.read_state(self.root)
        self.assertIsNone(data)
        self.assertNotIn("SECRET", message)
        self.assertEqual(run.call_args.args[0], ["terraform", f"-chdir={self.root}", "state", "pull"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertNotIn("TF_CLI_ARGS", run.call_args.kwargs["env"])
        with patch.object(self.mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("terraform", 20)):
            self.assertIsNone(self.mod.read_state(self.root)[0])

    def test_registered_page_uses_cache_refresh_and_escapes_resource_metadata(self):
        (self.root / "main.tf").write_text('resource "azurerm_resource_group" "main" {}')
        snapshot = {"resources": [resource("azurerm_resource_group", "main", {"name": '<script>alert("x")</script>'})], "serial": 1}
        with patch.object(self.mod, "read_state", return_value=(snapshot, "Read")):
            data = self.mod.scan(self.repo)
        routes = {}
        self.mod.register(SimpleNamespace(add_route=lambda method, path, handler: routes.setdefault((method, path), handler)))
        handler = routes[("GET", "")]
        with patch.object(self.mod, "scan", return_value=data) as scan:
            status, body, ctype = handler(SimpleNamespace())
            handler(SimpleNamespace(query={}))
            handler(SimpleNamespace(query={"refresh": "1"}))
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(scan.call_args.kwargs, {"refresh": True})
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("Deployment resources", body)
        self.assertIn("deployments-board", body)
        self.assertIn("deployments-filter", body)
        self.assertNotIn('<script>alert("x")</script>', body)
        self.assertIn("&lt;script&gt;", body)


if __name__ == "__main__":
    unittest.main()
