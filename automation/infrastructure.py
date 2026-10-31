#!/usr/bin/env python3
"""Bootstrap, plan and apply the checked-in Azure Terraform stacks."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
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
        terraform(directory, "plan", "-input=false", "-lock-timeout=5m", "-json", "-detailed-exitcode", f"-out={target}",
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
    parser.add_argument("action", choices=["bootstrap", "plan", "apply", "verify"])
    args = parser.parse_args()
    os.umask(0o077)
    os.environ["TF_IN_AUTOMATION"] = "true"
    if args.action == "bootstrap":
        bootstrap()
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
