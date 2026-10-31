# Azure infrastructure

Terraform owns Clusterfork's Azure resources and configuration. Application source
remains versioned repository content installed by the deployment workflow.
Task state, account auth caches, instructions and leases are runtime data in
the controller’s private SQLite database. Terraform owns its persistent disk.

## Stacks and ownership

| Stack | Resources |
| --- | --- |
| `infra/bootstrap` | Dedicated state resource group, storage account, private state containers, and state access roles |
| `infra/azure` | Automation resource group, controller VM/network/disk, retained monitoring, controller ingestion/workbook, Key Vault and credential references, deployment identity, federation, and permissions |

The runtime stack owns the controller infrastructure and protected secrets. The
legacy Function App, consumption plan, runtime storage, containers, queues, tables
and their obsolete role assignments are retired. Historical Application Insights
and its capped Log Analytics workspace retain their existing records; they do
not host a controller or receive new Function App traffic.

`monitoring.tf` owns the `ClusterforkController_CL` custom table/schema through
AzAPI, a direct data collection rule, the VM's DCR-scoped Monitoring Metrics
Publisher grant, the deployment identity's workspace-scoped Log Analytics Reader
grant, and the **Clusterfork controller** workbook. Its layout and KQL live under
`infra/azure/monitoring/`. The retained workspace keeps its 30-day retention and
0.1 GB/day ingestion quota; the custom table uses the same retention with no
extended archive. [Remote diagnostics](remote-development-observability.md#controller-telemetry-and-workbook)
owns telemetry behavior, workbook usage, privacy boundaries and smoke checks.

## State and deletion protection

State lives in the dedicated `cftfstate7604cac329` account in
`rg-clusterfork-tfstate`. Its `bootstrap-state` and `automation-state` containers
have separate role assignments. Shared-key authentication and public container
access are disabled. Azure encrypts stored data; blob versioning and deletion
retention protect state recovery. Backend access uses Microsoft Entra identities,
including GitHub OIDC, and Azure blob leases serialize state writes. Plans wait up to five minutes for an
existing operation to release its state lock.

The operator has Blob Data Owner on both containers. The deployment identity has
Blob Data Contributor on the automation container only. It cannot read or rewrite
bootstrap state. Bootstrap changes require the operator's Azure permissions.
Terraform state can contain storage credentials even when secret values use
write-only arguments; treat state and saved plans as private.

Stateful resources use `prevent_destroy`. Routine deployments reject every
proposed deletion or replacement before applying the saved plan. The once-only
legacy retirement used a separately inspected operator plan restricted to the
known relay, runtime storage and obsolete role assignments; the routine gate
remains unchanged.

## Credentials and identity

Runtime Linear credentials and the GitHub App private key are Key Vault secrets.
The controller's managed identity reads them directly. Public application IDs,
host configuration, sizing and role assignments are declared in Terraform.

Terraform declares the `Clusterfork` app’s public client ID and protected OAuth
secret, alongside the fallback API key. Configure or rotate these through this workflow;
`common/configuration.py` only reads local values. [Remote setup](remote-development-setup.md#local-configuration)
owns OAuth `client_credentials` selection and token handling.

Initial adoption supplies the existing credential values through an ephemeral
Terraform variable. `azurerm_key_vault_secret.value_wo` writes them without saving
the input in the Terraform plan or state. Later runs read the current values with
ephemeral Key Vault resources and retain the declared secret versions. Runtime
credentials are not copied into GitHub secrets or exposed to coding jobs.
The write-only value is excluded from value comparisons; its declared version
controls updates.

To rotate a credential, increment its entry in `credential_versions`, provide
`TF_VAR_runtime_credentials` as a private JSON map for all declared credentials,
and set `TF_VAR_seed_credentials=true` for the operator's plan/apply. Never commit
credential inputs. Keep the version increment in the reviewed configuration change.

The existing `clusterfork-github-deploy` identity retains its GitHub
`azure-production` federation. Obsolete website and control-table grants are
removed.
Terraform also declares Contributor and Role Based Access Control Administrator
on the automation resource group so the pipeline can reconcile its resources and
role assignments. Key Vault Secrets User permits ephemeral reads during plans.
Permissions are scoped to this application's resources and state container.
The operator receives Key Vault Secrets Officer for initial setup and rotation.

## Single controller host

`controller.tf` owns one `Standard_B2ats_v2` Ubuntu 24.04 VM in `mexicocentral`,
an allowed subscription region with verified capacity for this size. It uses a
pinned image version, a static public address/DNS name, and a separate 32 GiB
managed disk attached exclusively at LUN 0 without host caching. Deletion and
replacement protection covers the VM, address, and task disk. The disk denies
network export; the application database is local to its mounted filesystem.

Only HTTP certificate validation and HTTPS are allowed inbound. The application
binds to loopback behind Caddy, and operator access uses Azure Run Command and a
private local socket. An existing operator public SSH key satisfies Azure VM
creation; no SSH port is exposed. Terraform outputs contain public configuration
and resource identity, not runtime secrets.

The VM's system-assigned identity has Key Vault Secrets User and the ingestion
grant described above. It has no Table
Data Contributor grant. The service verifies the configured VM identity before
opening its HTTP transports.

The host/disk are provisioned and the controller is active. Cutover and live
acceptance evidence are tracked in [controller implementation](remote-development-controller.md).

## Bootstrap and retained import history

Use Terraform 1.15.9 and the existing Azure CLI operator identity. The bootstrap
command creates the separate state backend when needed:

```bash
python3 automation/infrastructure.py bootstrap
```

The original resource adoption is complete. Its old pause, shared-key snapshot
and import CLI are retired with the coordinator. The obsolete conditional import receipts have been removed. Routine runs use `plan` and
`apply`, with no credential-file argument.

The state account is separate from the retired runtime account. Private recovery
workspaces remain under `~/.local/state/clusterfork/terraform/`. Backend
configuration contains resource identifiers only; authentication comes from the
current identity.

## Routine changes and verification

Edit Terraform, inspect the plan, and apply through the helper or the normal
main-branch deployment workflow:

```bash
python3 automation/infrastructure.py plan
python3 automation/infrastructure.py apply
```

The helper prints resource addresses, action counts, changed field names, and safe
diagnostic summaries. It withholds raw values and Terraform expression context.
Saved plans use private temporary directories and are removed after use. An apply
uses the reviewed plan and verifies the controller’s HTTPS health afterwards.

The enabled controller deployment workflow handles main pushes
that change runtime inputs and manual deployments. It waits for exact-revision
CI and verified drain, reconciles Terraform through the same plan gate, then
installs and verifies the controller release. Deployment, migration and hold
semantics live in [controller deployment](remote-development-deployments.md).
There are no parallel ARM/CLI provisioning or app-setting mutation helpers.
The legacy Functions packager is removed.

After adoption, a normal plan should report no resource changes. Monitoring and
logging are covered by [remote diagnostics](remote-development-observability.md);
worker draining and publication by [controller deployment](remote-development-deployments.md).

Local validation:

```bash
terraform fmt -check -recursive infra
terraform -chdir=infra/bootstrap init -backend=false -lockfile=readonly
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/azure init -backend=false -lockfile=readonly
terraform -chdir=infra/azure validate
```
