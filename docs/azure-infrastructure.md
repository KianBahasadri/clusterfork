# Azure infrastructure

Terraform owns Azure resources/configuration; deployment installs versioned source.
Task state, auth caches, instructions and leases are private SQLite runtime data
on the Terraform-managed disk.

## Stacks and ownership

| Stack | Resources |
| --- | --- |
| `infra/bootstrap` | Dedicated state resource group/account, private containers and state access roles |
| `infra/azure` | Automation group, controller VM/network/disk, monitoring/ingestion/workbook, Key Vault and credential references, deployment identity/federation/permissions |

The legacy Function App, plan, runtime storage/queues/tables and obsolete grants
are retired. Retained Application Insights holds historical records.
`monitoring.tf` owns AzAPI `ClusterforkController_CL` schema, direct collection rule,
VM DCR-scoped Monitoring Metrics Publisher, deployment workspace-scoped Log
Analytics Reader and workbook. Layout/KQL live under `infra/azure/monitoring/`.
Workspace and table retention is 30 days, without extended archive; workspace quota
is 0.1 GB/day. [Diagnostics](remote-development-observability.md#controller-telemetry-and-workbook)
owns telemetry behavior, privacy, workbook usage and smoke checks.

## State and deletion protection

State account `cftfstate7604cac329` in `rg-clusterfork-tfstate` has separately
assigned `bootstrap-state`/`automation-state` containers. Shared keys/public container
access are disabled. Azure encryption, blob versioning and deletion retention
protect recovery. Microsoft Entra/OIDC authenticates; blob leases serialize writes
with five-minute lock waits. Operator Blob Data Owner covers both containers;
deployment Blob Data Contributor covers automation only, never bootstrap state.
Bootstrap changes need operator permissions. Treat state/plans as private: storage
credentials can appear even when secret inputs use write-only arguments.

Stateful resources use `prevent_destroy`; routine saved-plan applies reject all
deletions/replacements. The once-only legacy retirement used a separately reviewed
operator plan limited to known obsolete resources; the routine gate is unchanged.

## Credentials and identity

Key Vault holds runtime Linear credentials and App private key for direct managed-
identity reads. Terraform declares public IDs, host/sizing/grants, the public
`Clusterfork` OAuth client ID, protected secret and fallback key. [Setup](remote-development-setup.md#local-configuration)
owns credential selection; `common/configuration.py` only reads local values.

Initial adoption supplies credentials ephemerally. `azurerm_key_vault_secret.value_wo`
writes without storing inputs in plan/state; later plans use ephemeral Key Vault
reads and retain versions. Write-only values are excluded from comparison, so
declared versions control updates. Credentials never enter GitHub secrets/jobs.
For rotation, increment `credential_versions`, supply private JSON map
`TF_VAR_runtime_credentials` for all declared credentials and set
`TF_VAR_seed_credentials=true` for operator plan/apply. Commit only the reviewed
version increment, never credential inputs.

`clusterfork-github-deploy` retains `azure-production` federation, automation-group
Contributor/RBAC Administrator, Key Vault Secrets User and its scoped state grant.
The operator has Key Vault Secrets Officer for setup/rotation. Permissions remain
application-scoped; obsolete website/control-table grants are removed.

## Single controller host

`controller.tf` owns one `Standard_B2ats_v2`, pinned Ubuntu 24.04 image in
`mexicocentral`, static address/DNS and exclusive 32 GiB managed disk at LUN 0
without host caching. VM/address/disk have deletion/replacement protection; disk
network export is denied. SQLite stays on its mounted local filesystem.
Only certificate-validation HTTP and HTTPS enter. The app binds loopback behind
Caddy; operators use Run Command/private socket. An existing public SSH key
satisfies VM creation, but no SSH port is exposed. Outputs exclude secrets.
VM system identity has Key Vault read/ingestion grants, no Table Data Contributor;
service startup verifies VM identity. [Controller](remote-development-controller.md)
owns task/resource locking and dated acceptance.

## Bootstrap and retained import history

Use Terraform 1.15.9 and the existing Azure CLI operator identity:

```bash
python3 automation/infrastructure.py bootstrap
```

Adoption is complete; old pause/snapshot/import CLI and conditional receipts are
retired. Routine plan/apply needs no credential-file argument. The state account
is separate from retired runtime storage. Private recovery workspaces remain at
`~/.local/state/clusterfork/terraform/`; backend config holds identifiers only,
with authentication from the current identity.

## Routine changes and verification

Edit configuration and inspect/apply through the helper or normal deployment:

```bash
python3 automation/infrastructure.py plan
python3 automation/infrastructure.py apply
```

The helper prints addresses, counts, field names and safe diagnostics, withholding
values/expression context. Private saved plans are removed after use; apply uses
the reviewed plan and verifies HTTPS health. Settled configuration should plan no
changes. [Deployment](remote-development-deployments.md) owns main-triggered rollout,
CI/drain/install/version gates and holds. There are no parallel provisioning or
app-setting mutation helpers.

Local validation:

```bash
terraform fmt -check -recursive infra
terraform -chdir=infra/bootstrap init -backend=false -lockfile=readonly
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/azure init -backend=false -lockfile=readonly
terraform -chdir=infra/azure validate
```
