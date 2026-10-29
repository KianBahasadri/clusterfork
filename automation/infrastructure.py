#!/usr/bin/env python3
"""Bootstrap, adopt, plan and apply the checked-in Azure Terraform stacks."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
PRIVATE = Path.home() / ".local/state/clusterfork/terraform"
SECRETS = {"LINEAR_API_KEY", "LINEAR_WEBHOOK_SECRET", "GITHUB_APP_PRIVATE_KEY", "LINEAR_OAUTH_CLIENT_SECRET"}
APP = "/subscriptions/37d24562-0c19-408a-b492-f765921e443a/resourceGroups/rg-clusterfork-automation/providers/Microsoft.Web/sites/clusterfork-dispatch-7604cac329"


class InfrastructureError(RuntimeError):
    pass


def check_plan(data):
    """No automatic infrastructure operation may delete or replace a resource."""
    changes = [item for item in data.get("resource_changes", []) if item.get("mode", "managed") == "managed"]
    blocked = [item["address"] for item in changes if "delete" in item["change"]["actions"]]
    if blocked:
        raise InfrastructureError("Terraform proposes deletion/replacement: " + ", ".join(blocked))
    return Counter(action for item in changes for action in item["change"]["actions"] if action != "no-op")


def diagnostics(output):
    """Never print Terraform values, expression context, request bodies or raw stderr."""
    result = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("type") == "diagnostic":
            detail = item.get("diagnostic", {})
            location = detail.get("range", {}).get("start", {})
            result.append({"severity": detail.get("severity"), "summary": detail.get("summary"),
                           "file": detail.get("range", {}).get("filename"), "line": location.get("line")})
    return result


def run(args, *, cwd=None, env=None, codes=(0,), terraform_json=False):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if terraform_json:
        for item in diagnostics(result.stdout):
            print(json.dumps(item), flush=True)
    if result.returncode not in codes:
        raise InfrastructureError(f"{Path(args[0]).name} {args[1]} failed (exit {result.returncode}); raw output withheld")
    return result.stdout


def terraform(directory, *args, env=None, **kwargs):
    return run(["terraform", *args], cwd=directory, env=env, **kwargs)


def init(directory, env=None):
    terraform(directory, "init", "-input=false", "-lockfile=readonly", "-backend-config=backend.hcl", env=env)


def plan(directory, *, env=None, apply=False):
    with tempfile.TemporaryDirectory(prefix="clusterfork-tf-plan-") as temporary:
        target = Path(temporary) / "reviewed.tfplan"
        terraform(directory, "plan", "-input=false", "-json", "-detailed-exitcode", f"-out={target}",
                  env=env, codes=(0, 2), terraform_json=True)
        data = json.loads(terraform(directory, "show", "-json", str(target), env=env))
        counts = check_plan(data)
        imports = sum(bool(item["change"].get("importing")) for item in data.get("resource_changes", []))
        print("Terraform reviewed: " + json.dumps(dict(counts)) + f"; imports: {imports}", flush=True)
        for item in data.get("resource_changes", []):
            if item["change"]["actions"] != ["no-op"] or item["change"].get("importing"):
                before, after = item["change"].get("before") or {}, item["change"].get("after") or {}
                fields = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
                print(json.dumps({"resource": item["address"], "actions": item["change"]["actions"], "fields": fields}), flush=True)
        if apply:
            terraform(directory, "apply", "-input=false", "-json", str(target), env=env, terraform_json=True)
            print("Terraform apply completed.", flush=True)
        return data


def workspace(stack):
    directory = PRIVATE / stack
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    for source in (INFRA / stack).iterdir():
        if source.name != "backend.tf" and source.is_file():
            shutil.copy2(source, directory / source.name)
    return directory


def backend_exists(stack):
    container = "bootstrap-state" if stack == "bootstrap" else "automation-state"
    key = "bootstrap.tfstate" if stack == "bootstrap" else "automation.tfstate"
    account = subprocess.run(["az", "storage", "account", "show", "-g", "rg-clusterfork-tfstate", "-n", "cftfstate7604cac329",
                              "--query", "id", "-o", "tsv", "--only-show-errors"], capture_output=True, text=True)
    if account.returncode:
        if any(code in account.stderr for code in ("ResourceNotFound", "ResourceGroupNotFound", "AccountNotFound")):
            return False
        raise InfrastructureError("Unable to inspect Terraform state storage with the current Azure identity")
    result = subprocess.run(["az", "storage", "blob", "exists", "--account-name", "cftfstate7604cac329",
                             "--container-name", container, "--name", key, "--auth-mode", "login",
                             "-o", "json", "--only-show-errors"], capture_output=True, text=True)
    if result.returncode:
        if any(code in result.stderr for code in ("ResourceNotFound", "AccountNotFound", "ContainerNotFound")):
            return False
        raise InfrastructureError("Unable to inspect the Terraform backend with the current Azure identity")
    return json.loads(result.stdout)["exists"] is True


def migrate(directory, stack, env=None):
    shutil.copy2(INFRA / stack / "backend.tf", directory / "backend.tf")
    for attempt in range(12):
        try:
            terraform(directory, "init", "-migrate-state", "-force-copy", "-lockfile=readonly", "-backend-config=backend.hcl", env=env)
            (directory / "remote.ready").write_text("Remote state migration completed.\n")
            print(f"{stack}: state migrated to the private Azure backend.", flush=True)
            return
        except InfrastructureError:
            if attempt == 11:
                raise
            print("Waiting for Azure state-storage permissions to propagate.", flush=True)
            time.sleep(10)


def bootstrap():
    directory = workspace("bootstrap")
    if (directory / "remote.ready").exists() or backend_exists("bootstrap"):
        init(INFRA / "bootstrap")
        plan(INFRA / "bootstrap", apply=True)
        return
    if (directory / "backend.tf").exists() and (directory / "terraform.tfstate").exists():
        migrate(directory, "bootstrap")
        return
    # First creation uses a protected local state; Terraform creates its own backend.
    terraform(directory, "init", "-input=false", "-lockfile=readonly")
    plan(directory, apply=True)
    migrate(directory, "bootstrap")


def adopt(credentials_file, *, apply=True):
    directory = workspace("azure")
    if (directory / "remote.ready").exists() or backend_exists("azure"):
        raise InfrastructureError("Azure resources were already adopted; use plan or apply")
    values = json.loads(credentials_file.read_text())
    if isinstance(values, list):
        values = {item["name"]: item["value"] for item in values}
    secrets = {name: values[name] for name in SECRETS}
    if any(not isinstance(value, str) or not value or value.startswith("@Microsoft.KeyVault(") for value in secrets.values()):
        raise InfrastructureError("Adoption requires the existing runtime credential values")
    state_path = directory / "terraform.tfstate"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    identities = [instance.get("attributes", {}).get("identity", []) for resource in state.get("resources", [])
                  if resource.get("type") == "azurerm_function_app_flex_consumption"
                  for instance in resource.get("instances", [])]
    adopting_identity = not any(identity and identity[0].get("principal_id") for identity in identities)
    env = {**os.environ, "TF_VAR_seed_credentials": "true", "TF_VAR_adopt_existing": "true", "TF_VAR_runtime_credentials": json.dumps(secrets),
           "TF_VAR_adopting_identity": str(adopting_identity).lower()}
    terraform(directory, "init", "-input=false", "-lockfile=readonly", env=env)
    control, pause_revision = pause_adoption() if apply else (None, None)
    plan(directory, env=env, apply=apply)
    if not apply:
        return
    verify()
    migrate(directory, "azure", env=env)
    if pause_revision is not None:
        control.resume("terraform-adoption", pause_revision)
        print("Adoption verified; its deployment pause was released.", flush=True)


def pause_adoption():
    # This is runtime admission control, not infrastructure provisioning.
    sys.path.insert(0, str(ROOT / "automation/azure"))
    from deployment_control import DeploymentControl
    from worker_store import AzureTable

    connection = run(["az", "storage", "account", "show-connection-string", "-g", "rg-clusterfork-automation",
                      "-n", "cfdispatch7604cac329", "--query", "connectionString", "-o", "tsv", "--only-show-errors"]).strip()
    control = DeploymentControl(AzureTable(connection, "ClusterforkOperations", "control"))
    state, _ = control.state()
    revision = None
    if not state.get("paused"):
        revision = run(["git", "rev-parse", "HEAD"], cwd=ROOT).strip()
        control.pause("terraform-adoption", revision)
    elif state.get("owner") == "terraform-adoption":
        revision = state["revision"]
    active = run(["gh", "api", "repos/KianBahasadri/clusterfork/actions/workflows/clusterfork-worker.yml/runs?per_page=100",
                  "--paginate", "--jq", '.workflow_runs[] | select(.status != "completed") | .id']).strip()
    if active:
        raise InfrastructureError("Active coding/readiness runs must stop before adoption; dispatch remains paused")
    return control, revision


def reference_statuses(data):
    if isinstance(data.get("properties"), dict):
        return {name: value.get("status") for name, value in data["properties"].items()}
    return {item["name"]: item.get("properties", {}).get("status") for item in data.get("value", [])}


def verify():
    endpoint = f"https://management.azure.com{APP}/config/configreferences/appsettings"
    run(["az", "rest", "--method", "post", "--url", endpoint + "/refresh?api-version=2022-03-01", "-o", "none", "--only-show-errors"])
    for attempt in range(30):
        data = json.loads(run(["az", "rest", "--method", "get", "--url", endpoint + "?api-version=2022-03-01", "-o", "json", "--only-show-errors"]))
        statuses = reference_statuses(data)
        unresolved = [name for name in SECRETS if statuses.get(name) != "Resolved"]
        if not unresolved:
            print("All runtime Key Vault references resolved.", flush=True)
            return
        if attempt == 29:
            raise InfrastructureError("Unresolved Key Vault references: " + ", ".join(sorted(unresolved)))
        print("Waiting for Key Vault reference resolution: " + ", ".join(sorted(unresolved)), flush=True)
        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["bootstrap", "adopt", "plan", "apply", "verify"])
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    os.environ["TF_IN_AUTOMATION"] = "true"
    if args.action == "bootstrap":
        bootstrap()
    elif args.action == "adopt":
        if not args.credentials_file:
            parser.error("adopt requires --credentials-file with the existing private credential snapshot")
        adopt(args.credentials_file, apply=not args.plan_only)
    elif args.action == "verify":
        verify()
    else:
        init(INFRA / "azure")
        plan(INFRA / "azure", apply=args.action == "apply")
        if args.action == "apply":
            verify()


if __name__ == "__main__":
    try:
        main()
    except (InfrastructureError, KeyError, ValueError) as exc:
        raise SystemExit(f"Infrastructure operation failed: {exc}") from None
