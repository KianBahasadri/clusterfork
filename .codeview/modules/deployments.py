"""Codeview tab: Terraform state inventory and sourced Azure retail estimates."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import urlopen

NAME = "deployments"
DESCRIPTION = "Terraform resources, configuration details, and monthly cost estimates"
REPO = Path(__file__).resolve().parents[2]
PRICES_URL = "https://prices.azure.com/api/retail/prices"
PRICE_DOC = "https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices"
HOURS = 730
PRICE_CACHE: dict = {}
PRICE_LOCK = threading.Lock()
RESOURCE = re.compile(r'(?m)^[ \t]*resource\s+"([\w-]+)"\s+"([\w-]+)"\s*\{')
COMMENTS = re.compile(r'("(?:\\.|[^"\\])*")|(/\*.*?\*/|\#[^\n]*|//[^\n]*)', re.S)
SAFE_FIELDS = {
    "id", "name", "location", "resource_group_name", "size", "sku", "sku_name",
    "disk_size_gb", "storage_account_type", "account_tier", "account_kind",
    "account_replication_type", "access_tier", "retention_in_days", "daily_quota_gb",
    "allocation_method", "ip_version", "ip_address", "fqdn", "domain_name_label",
    "address_space", "address_prefixes", "virtual_network_name", "subnet_id",
    "network_interface_id", "network_interface_ids", "network_security_group_id",
    "managed_disk_id", "virtual_machine_id", "lun", "caching", "scope",
    "role_definition_name", "principal_id", "client_id", "tenant_id", "parent_id",
    "key_vault_id", "storage_account_id", "storage_account_name", "workspace_id",
    "source_id", "user_assigned_identity_id", "public_network_access_enabled",
    "network_access_policy", "purge_protection_enabled", "soft_delete_retention_days",
    "application_type", "display_name", "kind", "priority", "eviction_policy",
}
NESTED_FIELDS = {
    "os_disk": {"name", "disk_size_gb", "storage_account_type", "caching"},
    "source_image_reference": {"publisher", "offer", "sku", "version"},
    "identity": {"type", "principal_id", "tenant_id", "identity_ids"},
    "ip_configuration": {"name", "private_ip_address", "subnet_id", "public_ip_address_id"},
    "tags": {"application", "environment", "project", "service", "owner", "purpose", "managed_by"},
}
INCLUDED = {
    "azurerm_resource_group": "Resource grouping has no standalone charge.",
    "azurerm_role_assignment": "Access assignment has no standalone charge.",
    "azurerm_user_assigned_identity": "Managed identity has no standalone charge.",
    "azurerm_federated_identity_credential": "Identity federation has no standalone charge.",
    "azurerm_virtual_network": "No base VNet charge; traffic and optional network services are excluded.",
    "azurerm_subnet": "Included in the virtual network; traffic is excluded.",
    "azurerm_network_interface": "Included in the VM network; traffic is excluded.",
    "azurerm_network_security_group": "No standalone network security group charge.",
    "azurerm_network_interface_security_group_association": "Association has no standalone charge.",
    "azurerm_virtual_machine_data_disk_attachment": "Disk cost is counted on the managed disk.",
    "azurerm_key_vault_secret": "Operations are billed through the vault, not per stored secret.",
    "azurerm_storage_container": "Capacity and operations are billed through the storage account.",
    "azurerm_application_insights": "Workspace ingestion and retention are billed through Log Analytics.",
    "azurerm_application_insights_workbook": "Workbook queries use the associated workspace.",
    "azurerm_monitor_data_collection_rule": "Ingestion and processing depend on workspace usage.",
}


def register(reg):
    snapshot: dict = {}
    lock = threading.Lock()

    def page(req):
        refresh = getattr(req, "query", {}).get("refresh") == "1"
        with lock:
            if refresh or time.monotonic() - snapshot.get("at", -math.inf) > 60:
                snapshot["data"] = scan(REPO, refresh=refresh)
                snapshot["at"] = time.monotonic()
            return 200, render(snapshot["data"]), "text/html; charset=utf-8"

    reg.add_route("GET", "", page)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def declarations(repo: Path) -> dict[Path, dict]:
    """Find local root modules, excluding downloaded provider/module trees."""
    files = []
    for directory, subdirs, names in os.walk(repo):
        subdirs[:] = [name for name in subdirs if name not in {
            ".git", ".terraform", ".codeview", ".venv", "venv", "node_modules", "vendor"}]
        files.extend(Path(directory) / name for name in names
                     if name.endswith((".tf", ".tf.json")))
    stacks: dict[Path, dict] = {}
    for path in sorted(files):
        if path.is_symlink():
            continue
        declared = stacks.setdefault(path.parent, {})
        try:
            text = path.read_text(encoding="utf-8")
            if path.name.endswith(".tf.json"):
                for kind, resources in json.loads(text).get("resource", {}).items():
                    for name in resources:
                        declared[f"{kind}.{name}"] = {"source": str(path.relative_to(repo)), "type": kind}
                continue
            clean = COMMENTS.sub(lambda m: m[1] if m[1] else "\n" * m[0].count("\n"), text)
            for match in RESOURCE.finditer(clean):
                kind, name = match.groups()
                declared[f"{kind}.{name}"] = {
                    "source": f"{path.relative_to(repo)}:{clean.count(chr(10), 0, match.start()) + 1}",
                    "type": kind,
                }
        except (OSError, UnicodeError, ValueError, AttributeError):
            continue
    return stacks


def read_state(directory: Path) -> tuple[dict | None, str]:
    """Pull the configured backend without refreshing, planning, or writing state."""
    env = dict(os.environ, TF_IN_AUTOMATION="true", TF_INPUT="0", CHECKPOINT_DISABLE="1")
    for key in list(env):
        if key.startswith("TF_CLI_ARGS"):
            del env[key]
    try:
        proc = subprocess.run(["terraform", f"-chdir={directory}", "state", "pull"],
                              capture_output=True, text=True, timeout=20, env=env)
        if proc.returncode:
            return None, f"State unavailable (Terraform exit {proc.returncode}); check local backend access."
        data = json.loads(proc.stdout)
        if not isinstance(data, dict) or not isinstance(data.get("resources"), list):
            return None, "No readable resource snapshot in the configured backend."
        return data, "Backend state read successfully"
    except FileNotFoundError:
        return None, "Terraform is not installed on the dashboard host."
    except subprocess.TimeoutExpired:
        return None, "State read timed out; check local backend access."
    except (OSError, ValueError):
        return None, "Terraform state could not be read."


def safe_attributes(instance: dict) -> dict:
    """Project allowlisted metadata; never serialize raw attributes or outputs."""
    blocked = {path[0].get("value") for path in instance.get("sensitive_attributes", [])
               if isinstance(path, list) and path and isinstance(path[0], dict)}
    attrs = instance.get("attributes") or {}
    result = {key: value for key, value in attrs.items()
              if key in SAFE_FIELDS and key not in blocked
              and isinstance(value, (str, int, float, bool, type(None), list))}
    # Lists on the scalar allowlist may contain only scalars, not arbitrary objects.
    result = {key: value for key, value in result.items()
              if not isinstance(value, list) or all(isinstance(v, (str, int, float, bool)) for v in value)}
    for key, fields in NESTED_FIELDS.items():
        if key in blocked or key not in attrs:
            continue
        values = attrs[key] if isinstance(attrs[key], list) else [attrs[key]]
        projected = [{k: v for k, v in value.items() if k in fields
                      and isinstance(v, (str, int, float, bool, type(None)))}
                     for value in values if isinstance(value, dict)]
        if projected:
            result[key] = projected if isinstance(attrs[key], list) else projected[0]
    return result


def state_resources(state: dict, declared: dict, stack: str) -> list[dict]:
    rows = []
    for resource in state.get("resources", []):
        if resource.get("mode") != "managed":
            continue
        base = ".".join(filter(None, (resource.get("module"), resource["type"], resource["name"])))
        for instance in resource.get("instances", []):
            suffix = f'[{json.dumps(instance["index_key"], ensure_ascii=False)}]' if "index_key" in instance else ""
            address = base + suffix
            deposed = instance.get("deposed")
            attrs = safe_attributes(instance)
            rows.append({"address": address, "base": base, "type": resource["type"],
                         "name": attrs.get("name") or address, "stack": stack,
                         "status": "Deposed" if deposed else "Recorded" if base in declared else "State only",
                         "source": declared.get(base, {}).get("source", "Not declared in this root module"),
                         "details": attrs, "dependencies": instance.get("dependencies", []),
                         "deposed": deposed})
    return rows


def price_query(**filters) -> str:
    return " and ".join(f"{key} eq '{str(value).replace(chr(39), chr(39) * 2)}'"
                        for key, value in dict(filters, priceType="Consumption").items())


def retail_prices(query: str, refresh: bool = False) -> dict:
    with PRICE_LOCK:
        cached = PRICE_CACHE.get(query)
        if cached and not refresh and time.monotonic() - cached[0] < (300 if cached[1].get("error") else 86400):
            return cached[1]
    url = PRICES_URL + "?" + urlencode({"$filter": query, "currencyCode": "USD",
                                       "api-version": "2023-01-01-preview"})
    result = {"items": [], "url": url, "checked_at": utc_now()}
    page = url
    try:
        seen = set()
        while page:
            if page in seen or len(seen) >= 10 or urlsplit(page).hostname != "prices.azure.com" or urlsplit(page).scheme != "https":
                raise ValueError("Invalid or incomplete pricing pagination")
            seen.add(page)
            with urlopen(page, timeout=12) as response:
                data = json.load(response)
            if not isinstance(data, dict) or not isinstance(data.get("Items"), list):
                raise ValueError("Invalid pricing response")
            if not all(isinstance(item, dict) for item in data["Items"]):
                raise ValueError("Invalid pricing item")
            result["items"].extend(data.get("Items", []))
            page = data.get("NextPageLink")
    except (OSError, ValueError, TimeoutError):
        result["items"] = []
        result["error"] = "Retail pricing is unavailable; no substitute rate is assumed."
    with PRICE_LOCK:
        PRICE_CACHE[query] = time.monotonic(), result
    return result


def disk_spec(attrs: dict, region: str, label: str) -> tuple | None:
    size = attrs.get("disk_size_gb")
    storage = attrs.get("storage_account_type", "")
    if not isinstance(size, (int, float)) or not 0 < size <= 32768:
        return None
    if storage not in {"StandardSSD_LRS", "StandardSSD_ZRS", "Premium_LRS", "Premium_ZRS", "Standard_LRS"}:
        return None
    tiers = [(4, 1), (8, 2), (16, 3), (32, 4), (64, 6), (128, 10), (256, 15),
             (512, 20), (1024, 30), (2048, 40), (4096, 50), (8192, 60), (16384, 70), (32768, 80)]
    prefix = "E" if storage.startswith("StandardSSD") else "P" if storage.startswith("Premium") else "S"
    if prefix == "S":
        tiers = [(32, 4), *tiers[4:]]
    _, tier = next(pair for pair in tiers if pair[0] >= size)
    sku = f"{prefix}{tier} {storage.rsplit('_', 1)[1]}"
    product = {"E": "Standard SSD Managed Disks", "P": "Premium SSD Managed Disks", "S": "Standard HDD Managed Disks"}[prefix]
    return (label, price_query(serviceName="Storage", armRegionName=region,
                              productName=product, skuName=sku, meterName=f"{sku} Disk"), "1/Month", "month")


def charge_specs(row: dict) -> list[tuple]:
    attrs, kind = row["details"], row["type"]
    region = attrs.get("location", "")
    if not region or row["status"] not in {"Recorded", "State only", "Deposed"}:
        return []
    if kind == "azurerm_linux_virtual_machine":
        if attrs.get("priority", "Regular") != "Regular":
            return []  # Never substitute regular pricing for a Spot instance.
        specs = [("Linux compute", price_query(serviceName="Virtual Machines", armRegionName=region,
                  armSkuName=attrs.get("size", "")), "1 Hour", "vm_hours")]
        disk = disk_spec((attrs.get("os_disk") or [{}])[0], region, "OS disk")
        return specs + ([disk] if disk else [("OS disk", "", "1/Month", "month")])
    if kind == "azurerm_managed_disk":
        spec = disk_spec(attrs, region, "Managed disk")
        return [spec] if spec else [("Managed disk", "", "1/Month", "month")]
    if kind == "azurerm_public_ip" and attrs.get("sku") == "Standard" and attrs.get("ip_version", "IPv4") == "IPv4":
        return [("Allocated IPv4", price_query(serviceName="Virtual Network", armRegionName=region,
                 productName="IP Addresses", skuName="Standard", meterName="Standard IPv4 Static Public IP"), "1 Hour", "allocated_hours")]
    if kind == "azurerm_log_analytics_workspace" and attrs.get("sku") == "PerGB2018":
        return [("Log ingestion", price_query(serviceName="Log Analytics", armRegionName=region,
                 productName="Log Analytics", skuName="Analytics Logs", meterName="Analytics Logs Data Ingestion"), "1 GB", "usage")]
    if kind == "azurerm_key_vault" and attrs.get("sku_name") == "standard":
        return [("Vault operations", price_query(serviceName="Key Vault", armRegionName=region,
                 productName="Key Vault", skuName="Standard", meterName="Operations"), "10K", "usage")]
    if kind == "azurerm_storage_account" and attrs.get("account_tier") == "Standard" and attrs.get("account_kind") == "StorageV2":
        sku = f"{attrs.get('access_tier', 'Hot')} {attrs.get('account_replication_type', '')}"
        return [("Blob capacity", price_query(serviceName="Storage", armRegionName=region,
                 productName="General Block Blob v2", skuName=sku, meterName=f"{sku} Data Stored"), "1 GB/Month", "usage")]
    return []


def choose_rate(catalog: dict, spec: tuple) -> dict | None:
    label, _query, unit, basis = spec
    now = utc_now()
    valid = [item for item in catalog.get("items", [])
             if item.get("type") == "Consumption" and item.get("currencyCode") == "USD"
             and item.get("unitOfMeasure") == unit
             and isinstance(item.get("retailPrice"), (int, float))
             and math.isfinite(item["retailPrice"]) and item["retailPrice"] >= 0
             and not item.get("reservationTerm")
             and item.get("effectiveStartDate", "") <= now]
    if basis == "vm_hours":
        valid = [item for item in valid if not any(word in (item.get("productName", "") + item.get("skuName", "")).lower()
                                                  for word in ("windows", "spot", "low priority"))]
    # Show the paid ingestion rate, rather than mistaking its shared free tier
    # for the cost of all traffic. Blob capacity uses its first paid tier.
    if label == "Log ingestion":
        valid = [item for item in valid if item["retailPrice"] > 0]
    elif basis == "usage":
        valid = [item for item in valid if item.get("tierMinimumUnits", 0) == 0]
    if not valid:
        return None
    primary = [item for item in valid if item.get("isPrimaryMeterRegion")]
    if primary:
        valid = primary
    valid.sort(key=lambda item: item.get("effectiveStartDate", ""), reverse=True)
    rates = {item["retailPrice"] for item in valid
             if item.get("effectiveStartDate", "") == valid[0].get("effectiveStartDate", "")}
    return valid[0] if len(rates) == 1 else None


def add_costs(rows: list[dict], refresh: bool = False) -> None:
    specs = {id(row): charge_specs(row) for row in rows}
    queries = sorted({spec[1] for entries in specs.values() for spec in entries if spec[1]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        catalogs = dict(zip(queries, pool.map(lambda query: retail_prices(query, refresh), queries)))
    for row in rows:
        row["charges"] = []
        row["monthly"] = None
        row["hourly"] = 0
        row["cost_status"] = "Unpriced"
        row["cost_note"] = "No automatic estimate for this resource type or SKU."
        if row["status"] not in {"Recorded", "State only", "Deposed"}:
            row["cost_status"] = "Unverified" if row["status"] == "Unverified" else "Not in state"
            row["cost_note"] = "Only resources recorded in state contribute to the estimate."
            continue
        if row["type"] in INCLUDED or (row["type"] == "azapi_resource" and "/workspaces/" in row["details"].get("id", "").lower()):
            row["cost_status"] = "Included"
            row["cost_note"] = INCLUDED.get(row["type"], "Custom table usage is billed through its workspace.")
            continue
        entries = specs[id(row)]
        for spec in entries:
            label, query, unit, basis = spec
            catalog = catalogs.get(query, {})
            rate = choose_rate(catalog, spec)
            charge = {"label": label, "unit": unit, "basis": basis,
                      "url": catalog.get("url", ""), "checked_at": catalog.get("checked_at", ""),
                      "rate": rate["retailPrice"] if rate else None,
                      "meter": rate.get("meterName") if rate else None,
                      "effective": rate.get("effectiveStartDate") if rate else None}
            row["charges"].append(charge)
            if rate and basis != "usage":
                amount = rate["retailPrice"] * (HOURS if basis in {"vm_hours", "allocated_hours"} else 1)
                row["monthly"] = (row["monthly"] or 0) + amount
                if basis == "vm_hours":
                    row["hourly"] += rate["retailPrice"]
        if any(charge["basis"] != "usage" for charge in row["charges"]):
            row["cost_status"] = ("Unpriced" if row["monthly"] is None else "Partial estimate"
                                  if any(charge["rate"] is None for charge in row["charges"]) else "Fixed estimate")
            row["cost_note"] = "730 allocated hours/month; compute uses the VM-hours assumption. Disk operations, traffic, backups, discounts, and tax are excluded."
        elif row["charges"]:
            row["cost_status"] = "Usage based"
            row["cost_note"] = "Usage is not stored in Terraform. Rates are shown without assuming usage or free-tier eligibility; operations, retention, and extra meters may add charges."
        elif row["type"] == "azurerm_monitor_action_group":
            row["cost_status"] = "Usage based"
            row["cost_note"] = "Notification and alert charges depend on actual activity."


def scan(repo: Path, *, refresh: bool = False) -> dict:
    roots = declarations(repo)
    rows, stacks = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(read_state, roots))
    for (directory, declared), (snapshot, message) in zip(roots.items(), snapshots):
        name = str(directory.relative_to(repo)) or "."
        records = state_resources(snapshot, declared, name) if snapshot else []
        present = {row["base"] for row in records}
        for address, source in declared.items():
            if address not in present:
                records.append({"address": address, "base": address, "type": source["type"],
                                "name": address, "stack": name,
                                "status": "Declared" if snapshot is not None else "Unverified",
                                "source": source["source"], "details": {}, "dependencies": [], "deposed": None})
        rows.extend(records)
        stacks.append({"path": name, "available": snapshot is not None,
                       "serial": snapshot.get("serial") if snapshot else None,
                       "message": message, "recorded": sum(row["status"] in {"Recorded", "State only", "Deposed"} for row in records)})
    rows.sort(key=lambda row: (row["stack"], row["type"], row["address"]))
    add_costs(rows, refresh)
    return {"resources": rows, "stacks": stacks, "checked_at": utc_now(),
            "monthly": sum(row["monthly"] or 0 for row in rows),
            "estimated": sum(row["monthly"] is not None for row in rows),
            "recorded": sum(stack["recorded"] for stack in stacks)}


def esc(value: object) -> str:
    return "" if value is None else html.escape(str(value), quote=True)


def money(value: float) -> str:
    return f"${value:,.2f}"


def render(data: dict) -> str:
    rows, stacks = data["resources"], data["stacks"]
    metrics = "".join(f'<div class="metric-card"><span class="metric-label">{esc(label)}</span>'
                      f'<span class="metric-num"{attrs}>{esc(value)}</span></div>'
                      for label, value, attrs in (
                          ("Recorded resources", f'{data["recorded"]:,}', ""),
                          ("Terraform stacks", f"{len(stacks):,}", ""),
                          ("Fixed USD/month subtotal", money(data["monthly"]) if data["estimated"] else "—", ' id="deployments-total"'),
                          ("Resources estimated", f'{data["estimated"]:,}', "")))
    stack_rows = "".join(render_stack(stack) for stack in stacks)
    inventory = "".join(render_resource(row) for row in rows)
    stack_filters = radio_filter("Stack", "deployments-stack", [("", "All stacks"), *[(stack["path"], stack["path"]) for stack in stacks]])
    state_filters = radio_filter("State", "deployments-state", [("", "All resources"), ("recorded", "Recorded in state"), ("unverified", "Unverified / declared")])
    return f'''<!doctype html>
<html lang="en" data-theme="dark"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light"><title>Deployment resources</title>
<link rel="stylesheet" href="/assets/app.css">
<script>
(function () {{
  var root = document.documentElement;
  function setTheme(t) {{ if (t === "light" || t === "dark") root.dataset.theme = t; }}
  var handed = new URLSearchParams(location.search).get("theme");
  if (handed) setTheme(handed);
  else {{
    var stored = null;
    try {{ stored = localStorage.getItem("codeview-theme"); }} catch (err) {{ /* opaque frame */ }}
    if (stored) setTheme(stored);
    else if (window.matchMedia("(prefers-color-scheme: light)").matches) setTheme("light");
  }}
  window.addEventListener("message", function (e) {{
    if (e.source === window.parent && e.data && e.data.type === "codeview-theme") setTheme(e.data.theme);
  }});
}})();
</script>
<style>
.deployments-stack {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; padding-top: 12px; }}
.deployments-toolbar {{ display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px 24px; }}
.deployments-toolbar form {{ margin-left: auto; }}
.deployments-metrics {{ display: flex; flex-wrap: wrap; gap: 24px; width: max-content; }}
.deployments-stacks {{ padding: 0; margin: 0; list-style: none; display: grid; gap: 4px; font-size: 13px; }}
.deployments-stacks li {{ display: flex; flex-wrap: wrap; gap: 4px 12px; }}
.deployments-stacks span, .deployments-note {{ color: var(--muted); }}
.deployments-note {{ margin: 0; font-size: 12px; overflow-wrap: anywhere; }}
.deployments-hours {{ max-width: 160px; }}
.deployments-hours [aria-invalid="true"] {{ border-color: var(--danger); border-radius: 0; }}
.deployments-error {{ color: var(--danger); font-size: 12px; }}
.deployments-filters {{ display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 0; margin: 0; border: 0; min-width: 0; }}
.deployments-filters legend {{ padding: 0; margin-bottom: 4px; font-size: 13.5px; font-weight: 500; color: var(--ink-strong); }}
.deployments-radio {{ display: inline-flex; align-items: center; gap: 10px; min-height: 44px; font-size: 13.5px; cursor: pointer; }}
.deployments-radio input {{ appearance: none; flex-shrink: 0; width: 18px; height: 18px; margin: 0; border: 1px solid var(--line-strong); border-radius: 50%; background: var(--surface); }}
.deployments-radio input:checked {{ border-color: var(--control-selected); background: radial-gradient(circle, var(--control-selected) 0 4px, var(--surface) 4px); }}
.deployments-radio:hover input:checked {{ border-color: var(--control-selected-hover); background: radial-gradient(circle, var(--control-selected-hover) 0 4px, var(--surface) 4px); }}
.deployments-resource {{ min-width: 0; border-bottom: 1px solid var(--line); }}
.deployments-resource:last-child {{ border-bottom: 0; }}
.deployments-resource[hidden] {{ display: none; }}
.deployments-resource > summary {{ display: grid; grid-template-columns: 16px minmax(0, 1fr) 120px 160px; gap: 12px; align-items: center; min-height: 36px; padding: 4px; cursor: pointer; list-style: none; border-radius: var(--radius-sm); }}
.deployments-resource > summary::-webkit-details-marker {{ display: none; }}
.deployments-resource > summary:hover {{ background: var(--surface-raised); }}
.deployments-resource[open] .deployments-chevron {{ transform: rotate(90deg); }}
.deployments-name, .deployments-type {{ display: block; overflow-wrap: anywhere; }}
.deployments-name {{ font-size: 13.5px; font-weight: 600; }}
.deployments-type, .deployments-region, .deployments-cost-label {{ font-size: 12px; color: var(--muted); }}
.deployments-cost {{ text-align: right; font-family: var(--mono); font-size: 13px; }}
.deployments-cost-label {{ display: block; font-family: var(--ui); }}
.deployments-estimate {{ color: var(--derived); }}
.deployments-details {{ padding: 12px 4px 20px 32px; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; }}
.deployments-details h3 {{ font-size: 14px; margin: 0 0 8px; }}
.deployments-fields {{ margin: 0; display: grid; gap: 8px; font-size: 12px; }}
.deployments-fields > div {{ display: grid; grid-template-columns: minmax(80px, 140px) minmax(0, 1fr); gap: 12px; }}
.deployments-fields dt {{ color: var(--muted); overflow-wrap: anywhere; }}
.deployments-fields dd {{ margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }}
.deployments-fields code {{ font: inherit; }}
.deployments-fields .meta-tag-copy {{ max-width: 100%; white-space: normal; text-align: left; overflow-wrap: anywhere; }}
.deployments-copy {{ margin-top: 8px; max-width: 100%; white-space: normal; }}
.deployments-charge {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; margin-bottom: 12px; min-width: 0; font-size: 13px; }}
.deployments-charge .deployments-copy {{ justify-self: start; }}
.deployments-charge small {{ color: var(--muted); overflow-wrap: anywhere; }}
.deployments-notes > summary {{ font-size: 12px; color: var(--muted); cursor: pointer; }}
.deployments-board {{ max-width: 1240px; }}
@media (max-width: 600px), (pointer: coarse) {{ .deployments-resource > summary {{ min-height: 44px; }} }}
#deployments-history .toast-history-item {{ grid-template-columns: minmax(0, 1fr); }}
@media (min-width: 421px) and (max-width: 900px) {{
  .deployments-metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 700px) {{
  .deployments-resource > summary {{ grid-template-columns: 16px minmax(0, 1fr) 112px; gap: 8px; }}
  .deployments-region {{ display: none; }}
  .deployments-details {{ grid-template-columns: minmax(0, 1fr); padding-left: 4px; gap: 16px; }}
  .deployments-toolbar form {{ margin-left: 0; }}
}}
@media (max-width: 420px) {{
  .deployments-metrics {{ width: 100%; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
  .deployments-resource > summary {{ grid-template-columns: 14px minmax(0, 1fr); }}
  .deployments-cost {{ grid-column: 2; text-align: left; }}
  .deployments-cost-label {{ display: inline; margin-left: 8px; }}
  .deployments-fields > div {{ grid-template-columns: 80px minmax(0, 1fr); gap: 8px; }}
}}
</style></head><body>
<div class="dashboard-layout deployments-stack">
  <header class="dashboard-heading"><div class="dashboard-title"><h1>Deployment resources</h1></div>
    <div class="dashboard-context"><time datetime="{esc(data['checked_at'])}">Read {esc(datetime.fromisoformat(data['checked_at']).strftime('%b %d %Y %I:%M %p'))} UTC</time></div>
  </header>
  <div class="metric-grid deployments-metrics">{metrics}</div>
  <details class="deployments-notes"><summary>Snapshot sources · {len(stacks)} stacks</summary>
    <ul class="deployments-stacks">{stack_rows or '<li>No local Terraform configuration found.</li>'}</ul></details>
  <p class="deployments-note">Terraform state snapshot; fixed USD estimates only. Usage charges and unpriced resources are excluded.</p>
  <div class="deployments-toolbar">
    <div class="form-group"><label class="form-label" for="deployments-filter">Filter resources</label><div class="input-with-action"><svg class="icon input-leading-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.34-4.34"></path></svg><input class="input-field" type="search" id="deployments-filter" autocomplete="off"><button type="button" class="input-clear-btn" id="deployments-clear" aria-label="Clear resource filter" title="Clear resource filter" hidden><svg class="icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"></path></svg></button></div></div>
    <div class="form-group deployments-hours"><label class="form-label" for="deployments-hours">VM hours/month</label><input class="input-field" type="number" id="deployments-hours" min="0" max="744" step="1" value="730" aria-describedby="deployments-hours-error"><span class="deployments-error" id="deployments-hours-error" role="alert" hidden>Enter a whole number from 0 to 744</span></div>
    <form method="get"><input type="hidden" name="theme" id="deployments-theme"><input type="hidden" name="refresh" value="1"><button type="submit" class="btn btn-secondary">Refresh inventory and prices</button></form>
  </div>
  <div class="deployments-toolbar" id="deployments-filter-controls">
    {stack_filters}{state_filters}
    <button type="button" class="btn btn-quiet" id="deployments-expand">Expand visible</button>
    <button type="button" class="btn btn-quiet" id="deployments-collapse">Collapse all</button>
    <span class="section-note" id="deployments-count" aria-live="polite">{len(rows):,} resources shown</span>
  </div>
  <section class="deployments-board" aria-label="Terraform resources">{inventory}</section>
  <div class="callout callout-info" id="deployments-empty" {'hidden' if rows else ''}><strong>No matches:</strong> no resources match these filters. <button type="button" class="btn btn-secondary" id="deployments-reset">Clear filters</button></div>
  <details class="deployments-notes"><summary>Pricing basis and exclusions</summary>
  <p class="deployments-note">USD public pay-as-you-go rates from Azure Retail Prices. Disks and allocated IPs remain provisioned for the full month. Blob capacity, vault operations, log ingestion/retention, disk transactions, egress, backups, taxes, and negotiated discounts are not added without usage data. Rates cache for 24 hours; inventory caches for 60 seconds. Refresh bypasses both caches.</p>
  <p class="deployments-note">Pricing source: {esc(PRICE_DOC)}</p>
  </details>
</div>
<div class="toast-container" id="deployments-toasts" aria-live="polite"></div>
<dialog class="modal-dialog" id="deployments-history" aria-labelledby="deployments-history-title"><div class="modal-header"><h2 class="modal-title" id="deployments-history-title">Notification history</h2><button type="button" class="btn-icon" id="deployments-history-close" aria-label="Close" title="Close"><svg class="icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"></path></svg></button></div><div class="modal-body"><ul class="toast-history-list" id="deployments-history-list"></ul></div></dialog>
<script>
(function () {{
  var rows = Array.from(document.querySelectorAll(".deployments-resource"));
  var input = document.getElementById("deployments-filter"), clear = document.getElementById("deployments-clear"), stack = document.getElementById("deployments-stack"), state = document.getElementById("deployments-state");
  var dropdowns = [];
  var resourceOrder = "Name";
  function orderResources() {{
    var currentHours = Number(document.getElementById("deployments-hours").value);
    rows.slice().sort(function (a, b) {{
      if (resourceOrder === "Monthly cost") {{
        var one = a.hasAttribute("data-monthly") ? Number(a.dataset.monthly) + Number(a.dataset.hourly) * (currentHours - 730) : -1;
        var two = b.hasAttribute("data-monthly") ? Number(b.dataset.monthly) + Number(b.dataset.hourly) * (currentHours - 730) : -1;
        return two - one;
      }}
      var selector = resourceOrder === "Resource type" ? ".deployments-type" : ".deployments-name";
      return a.querySelector(selector).textContent.localeCompare(b.querySelector(selector).textContent);
    }}).forEach(function (row) {{ document.querySelector(".deployments-board").appendChild(row); }});
  }}
  function filter() {{
    var query = input.value.trim().toLowerCase(), count = 0, selectedStack = stack.querySelector(":checked").value, selectedState = state.querySelector(":checked").value;
    clear.hidden = !input.value;
    rows.forEach(function (row) {{
      row.hidden = !((!query || row.dataset.search.indexOf(query) >= 0) && (!selectedStack || row.dataset.stack === selectedStack) && (!selectedState || row.dataset.state === selectedState));
      if (!row.hidden) count++;
    }});
    document.getElementById("deployments-count").textContent = count.toLocaleString("en-US") + (count === 1 ? " resource shown" : " resources shown");
    document.getElementById("deployments-empty").hidden = count !== 0;
    dropdowns.forEach(function (item) {{ item.control.setValue(item.group.querySelector(":checked").value); }});
  }}
  if (window.CodeviewUI) {{
    [stack, state].forEach(function (group) {{
      var choices = Array.from(group.querySelectorAll("input")).map(function (radio) {{ return {{ value: radio.value, label: radio.parentElement.textContent.trim() }}; }});
      var control = CodeviewUI.picker(group.querySelector("legend").textContent, choices, "", function (value) {{
        Array.from(group.querySelectorAll("input")).forEach(function (radio) {{ radio.checked = radio.value === value; }});
        group.dispatchEvent(new Event("input"));
      }});
      dropdowns.push({{ group: group, control: control }}); group.before(control); group.hidden = true;
    }});
    document.getElementById("deployments-filter-controls").prepend(CodeviewUI.picker("Order", ["Name", "Monthly cost", "Resource type"], "Name", function (value) {{
      resourceOrder = value; orderResources();
    }}));
  }}
  document.getElementById("deployments-expand").onclick = function () {{ rows.forEach(function (row) {{ if (!row.hidden) row.open = true; }}); }};
  orderResources();
  document.getElementById("deployments-collapse").onclick = function () {{ rows.forEach(function (row) {{ row.open = false; }}); }};
  [input, stack, state].forEach(function (el) {{ el.addEventListener("input", filter); }});
  clear.addEventListener("click", function () {{ input.value = ""; input.dispatchEvent(new Event("input")); input.focus(); }});
  document.getElementById("deployments-reset").addEventListener("click", function () {{ input.value = ""; stack.querySelector("input").checked = state.querySelector("input").checked = true; filter(); input.focus(); }});
  var hours = document.getElementById("deployments-hours");
  hours.addEventListener("input", function () {{
    var value = Number(hours.value), valid = hours.value !== "" && Number.isInteger(value) && value >= 0 && value <= 744;
    hours.setAttribute("aria-invalid", String(!valid));
    document.getElementById("deployments-hours-error").hidden = valid;
    if (!valid) return;
    var total = 0, estimated = 0;
    rows.forEach(function (row) {{
      if (!row.hasAttribute("data-monthly")) return;
      var monthly = Number(row.dataset.monthly) + Number(row.dataset.hourly) * (value - 730);
      total += monthly; estimated++;
      row.querySelector("[data-monthly-label]").textContent = "$" + monthly.toLocaleString("en-US", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    }});
    document.getElementById("deployments-total").textContent = estimated ? "$" + total.toLocaleString("en-US", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }}) : "—";
    document.querySelectorAll("[data-compute-rate]").forEach(function (charge) {{
      charge.textContent = "$" + (Number(charge.dataset.computeRate) * value).toFixed(2) + " for " + value + " VM hours";
    }});
    orderResources();
  }});
  document.getElementById("deployments-theme").value = document.documentElement.dataset.theme;
  document.querySelector("form").addEventListener("submit", function () {{
    document.getElementById("deployments-theme").value = document.documentElement.dataset.theme;
    var button = this.querySelector("button");
    button.style.minWidth = button.getBoundingClientRect().width + "px";
    button.setAttribute("aria-busy", "true"); button.disabled = true; button.textContent = "Refreshing…";
  }});
  var tray = document.getElementById("deployments-toasts"), history = document.getElementById("deployments-history"), historyList = document.getElementById("deployments-history-list"), returnFocus = null;
  function notify(message, danger) {{
    var toast = document.createElement("div"), button = document.createElement("button"), created = new Date();
    toast.className = "toast"; toast.dataset.tone = danger ? "danger" : "good";
    button.type = "button"; button.className = "toast-main"; button.textContent = message;
    toast.appendChild(button); tray.appendChild(toast); tray.classList.add("has-history"); tray.classList.remove("is-resting");
    var entry = document.createElement("li"), copy = document.createElement("div"), text = document.createElement("span"), stamp = document.createElement("time");
    entry.className = "toast-history-item"; entry.dataset.tone = toast.dataset.tone;
    copy.className = "toast-history-copy"; text.className = "toast-history-message"; text.textContent = message;
    stamp.className = "toast-history-time"; stamp.dateTime = created.toISOString(); stamp.textContent = created.toLocaleString();
    copy.append(text, stamp); entry.appendChild(copy); historyList.prepend(entry);
    button.addEventListener("click", function () {{ returnFocus = button; history.showModal(); document.getElementById("deployments-history-close").focus(); }});
    var timer, remaining = 5000, started;
    function resume() {{ if (timer || toast.classList.contains("is-archived")) return; started = performance.now(); timer = setTimeout(function () {{ toast.classList.add("is-archived"); timer = null; if (!tray.querySelector(".toast:not(.is-archived)")) tray.classList.add("is-resting"); }}, remaining); }}
    function pause() {{ if (timer) {{ clearTimeout(timer); timer = null; remaining -= performance.now() - started; }} }}
    toast.addEventListener("mouseenter", pause); toast.addEventListener("mouseleave", resume); toast.addEventListener("focusin", pause); toast.addEventListener("focusout", resume); resume();
  }}
  document.getElementById("deployments-history-close").addEventListener("click", function () {{ history.close(); }});
  history.addEventListener("click", function (event) {{ if (event.target === history) history.close(); }});
  history.addEventListener("close", function () {{ if (returnFocus) returnFocus.focus({{ preventScroll: true }}); }});
  document.addEventListener("click", async function (event) {{
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    try {{
      var copied = false;
      try {{ if (navigator.clipboard) {{ await navigator.clipboard.writeText(button.dataset.copy); copied = true; }} }} catch (err) {{ /* fall back for sandboxed frames */ }}
      if (!copied) {{
        var area = document.createElement("textarea");
        area.value = button.dataset.copy; area.style.position = "fixed"; area.style.opacity = "0";
        document.body.appendChild(area); area.select(); copied = document.execCommand("copy"); area.remove(); button.focus({{ preventScroll: true }});
      }}
      if (!copied) throw new Error("Clipboard unavailable");
      button.classList.add("is-copied"); setTimeout(function () {{ button.classList.remove("is-copied"); }}, 1600);
      notify("Copied to clipboard", false);
    }} catch (err) {{ notify("Could not copy to clipboard", true); }}
  }});
}})();
</script></body></html>'''


def render_resource(row: dict) -> str:
    attrs = row["details"]
    recorded = row["status"] in {"Recorded", "State only", "Deposed"}
    monthly = f' data-monthly="{row["monthly"]}" data-hourly="{row["hourly"]}"' if row["monthly"] is not None else ""
    cost = f'<span data-monthly-label>{money(row["monthly"])}</span>' if row["monthly"] is not None else esc(row["cost_status"])
    fields = {"Terraform address": row["address"], "State": row["status"], "Stack": row["stack"], "Source": row["source"], **attrs}
    if row["dependencies"]:
        fields["Depends on"] = row["dependencies"]
    if row["deposed"]:
        fields["Deposed instance"] = row["deposed"]
    details = "".join(render_field(key, value) for key, value in fields.items() if value is not None and value != "")
    portal = "https://portal.azure.com/#resource" + quote(attrs["id"], safe="/") if str(attrs.get("id", "")).startswith("/subscriptions/") else ""
    copy = f'<button type="button" class="btn btn-secondary deployments-copy" data-copy="{esc(portal)}">Copy Azure portal URL</button>' if portal else ""
    charges = []
    for charge in row["charges"]:
        rate = charge["rate"]
        unit = {"1/Month": "month", "1 Hour": "hour", "10K": "10,000 operations",
                "1 GB": "GB", "1 GB/Month": "GB/month"}.get(charge["unit"], charge["unit"])
        desc = "Rate unavailable" if rate is None else f'${rate:g} / {unit}'
        amount = ""
        if rate is not None and charge["basis"] == "vm_hours":
            amount = f'<span data-compute-rate="{rate}">{money(rate * HOURS)} for {HOURS} VM hours</span>'
        elif rate is not None and charge["basis"] == "allocated_hours":
            amount = f'<span>{money(rate * HOURS)} per month</span>'
        charges.append(f'<div class="deployments-charge"><strong>{esc(charge["label"])}</strong><span>{esc(desc)}</span>{amount}'
                       f'<small>{esc(charge["meter"] or "No matching retail meter")} · Effective {esc(charge["effective"] or "unknown")} · Checked {esc(charge["checked_at"] or "unknown")}</small>'
                       f'{copy_price(charge["url"])}</div>')
    search = " ".join(str(value) for value in (row["address"], row["name"], row["type"], row["stack"], *attrs.values())).lower()
    return f'''<details class="deployments-resource" data-search="{esc(search)}" data-stack="{esc(row['stack'])}" data-state="{'recorded' if recorded else 'unverified'}"{monthly}>
  <summary><svg class="icon deployments-chevron" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"></path></svg>
    <span><span class="deployments-name">{esc(row['name'])}</span><span class="deployments-type">{esc(row['type'])} · {esc(row['stack'])} · {esc(row['status'])}</span></span>
    <span class="deployments-region">{esc(attrs.get('location', '—'))}</span>
    <span class="deployments-cost{' deployments-estimate' if row['monthly'] is not None else ''}">{cost}{f'<small class="deployments-cost-label">{esc(row["cost_status"])}</small>' if row['monthly'] is not None else ''}</span>
  </summary>
  <div class="deployments-details"><section><h3>Resource information</h3><dl class="deployments-fields">{details}</dl>{copy}</section>
    <section><h3>Monthly cost basis</h3>{''.join(charges)}<p class="deployments-note">{esc(row['cost_note'])}</p></section></div>
</details>'''


def copy_price(url: str) -> str:
    return f'<button type="button" class="btn btn-quiet deployments-copy" data-copy="{esc(url)}">Copy pricing source URL</button>' if url else ""


def render_stack(stack: dict) -> str:
    serial = f' · serial {stack["serial"]}' if stack["available"] else ""
    return (f'<li><strong>{esc(stack["path"])}</strong><span>{esc(stack["message"])}'
            f'{esc(serial)} · {stack["recorded"]:,} resources</span></li>')


def radio_filter(label: str, name: str, options: list[tuple]) -> str:
    choices = "".join(f'<label class="deployments-radio"><input type="radio" name="{esc(name)}" value="{esc(value)}" {"checked" if index == 0 else ""}><span>{esc(text)}</span></label>'
                      for index, (value, text) in enumerate(options))
    return f'<fieldset class="deployments-filters" id="{esc(name)}"><legend>{esc(label)}</legend>{choices}</fieldset>'


def render_field(key: str, value: object) -> str:
    label = key.replace("_", " ")
    if isinstance(value, str) and (key.endswith("id") or key in {"Terraform address", "ip_address", "fqdn", "size"}):
        display = (f'<button type="button" class="meta-tag meta-tag-copy" data-copy="{esc(value)}" '
                   f'aria-label="Copy {esc(label)} {esc(value)}" title="Copy {esc(label)} {esc(value)}">{esc(value)}</button>')
    else:
        display = esc(json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (list, dict)) else value)
    return f'<div><dt>{esc(label)}</dt><dd>{display}</dd></div>'
