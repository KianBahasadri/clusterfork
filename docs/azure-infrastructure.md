# Azure infrastructure

Terraform owns Clusterfork's Azure resources and configuration. Application source
and `host.json` remain repository files published by the deployment workflow.
Account auth caches, queue messages, receipts, leases, and table rows are runtime
data: Terraform manages their storage resources, not their contents.

## Stacks and ownership

| Stack | Resources |
| --- | --- |
| `infra/bootstrap` | Dedicated state resource group, storage account, private state containers, and state access roles |
| `infra/azure` | Automation resource group, runtime storage and containers, queues, eight tables, Flex Consumption plan and Function App, monitoring, Key Vault and credential references, deployment identity, federation, and permissions |

The runtime stack adopts the original resource names. `imports.tf` records the
existing Azure resource IDs, including the Azure-created plan, package container,
and Smart Detection action group. Importing storage metadata does not import or
replace its contents. The poison queue is provisioned explicitly before it is
needed. Azure clients open existing tables; application startup no longer creates
infrastructure.

Terraform CLI and the AzureRM provider are pinned, and each stack commits its
provider lock file. CI validates both stacks without Azure credentials.
The helper enables import receipts only during adoption; normal reconciliation
creates missing infrastructure from its resource declarations rather than trying
to re-import a deleted resource.

## State and deletion protection

State lives in the dedicated `cftfstate7604cac329` account in
`rg-clusterfork-tfstate`. Its `bootstrap-state` and `automation-state` containers
have separate role assignments. Shared-key authentication and public container
access are disabled. Azure encrypts stored data; blob versioning and deletion
retention protect state recovery. Backend access uses Microsoft Entra identities,
including GitHub OIDC, and Azure blob leases serialize state writes.

The operator has Blob Data Owner on both containers. The deployment identity has
Blob Data Contributor on the automation container only. It cannot read or rewrite
bootstrap state. Bootstrap changes require the operator's Azure permissions.
Terraform state can contain storage credentials even when secret values use
write-only arguments; treat state and saved plans as private.

Stateful resources use `prevent_destroy`. The checked-in plan gate also rejects
every proposed deletion or replacement before applying the reviewed saved plan.
Changing this policy requires an explicit code change and separate review.

## Credentials and identity

Runtime Linear credentials and the GitHub App private key are Key Vault secrets.
The Function App uses a system-assigned managed identity with Key Vault Secrets
User access. Its app settings contain versionless Key Vault references. Public
application identifiers, storage connections, monitoring linkage, runtime, sizing,
and other settings are declared in Terraform.

Terraform already declares the `Clusterfork` app's `LINEAR_OAUTH_CLIENT_ID` and
the `LINEAR_OAUTH_CLIENT_SECRET` Key Vault reference, alongside the fallback
`LINEAR_API_KEY` reference. Configure or rotate these through this workflow;
`pilot.py` only reads local values. [Remote setup](remote-development-setup.md#local-configuration)
owns OAuth `client_credentials` selection and token handling.

Initial adoption supplies the existing credential values through an ephemeral
Terraform variable. `azurerm_key_vault_secret.value_wo` writes them without saving
the input in the Terraform plan or state. Later runs read the current values with
ephemeral Key Vault resources and retain the declared secret versions. Runtime
credentials are not copied into GitHub secrets or exposed to coding jobs.
The write-only value is excluded from value comparisons; its declared version
controls updates. Provider-managed storage connection settings are derived from
`storage_access_key` rather than duplicated in the generic app-setting map.

To rotate a credential, increment its entry in `credential_versions`, provide
`TF_VAR_runtime_credentials` as a private JSON map for all declared credentials,
and set `TF_VAR_seed_credentials=true` for the operator's plan/apply. Never commit
credential inputs. Keep the version increment in the reviewed configuration change.
Reference verification refreshes the runtime cache and waits for every reference
to resolve; this is an operational cache refresh, not a configuration writer.

The existing `clusterfork-github-deploy` identity retains its GitHub
`azure-production` federation and original deployment/control-table grants.
Terraform also declares Contributor and Role Based Access Control Administrator
on the automation resource group so the pipeline can reconcile its resources and
role assignments. Key Vault Secrets User permits ephemeral reads during plans.
Permissions are scoped to this application's resources and state container.
The operator receives Key Vault Secrets Officer for initial setup and rotation.

## Bootstrap and adoption

Use Terraform 1.15.9 and Azure CLI authenticated to the configured subscription.
Adoption also uses the Python dependencies in `automation/azure/requirements.txt`.
The existing GitHub environment variables identify the deployment identity; there
is no client secret. Authenticate as the operator for bootstrap/adoption, and
confirm no coding worker is active before an infrastructure change that can
restart the Function App.
The adoption helper pauses new admissions and checks all GitHub worker attempts;
an existing operator pause is preserved, and a failed adoption leaves its pause held.

```bash
python3 automation/infrastructure.py bootstrap
python3 automation/infrastructure.py adopt --credentials-file /private/current-app-settings.json --plan-only
python3 automation/infrastructure.py adopt --credentials-file /private/current-app-settings.json
```

The private snapshot can be a map or the JSON list returned by Azure app-setting
inspection. It must contain the existing values for the declared credentials.
Never put it in the repository. Adoption is a one-time migration; routine runs
use `plan` and `apply`.

Bootstrap first applies its stack using protected local state because the remote
backend does not exist yet, then migrates that state into its own Azure container.
Adoption keeps the initial import private and migrates the updated state after
credential references resolve. Recovery workspaces live under
`~/.local/state/clusterfork/terraform/` with restrictive permissions so an
interrupted migration does not discard state. Backend configuration contains
resource identifiers only; authentication comes from the current identity.

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
uses the reviewed plan and verifies Key Vault references afterwards.

Every main push or manual deployment waits for the exact revision's CI and for
workers to drain, then reconciles Terraform before publishing the source ZIP.
Successful package health verification resumes the deployment pause; the normal
repository queue still controls whether a task can run. Infrastructure failures
leave dispatch paused for repair. There are no parallel ARM/CLI provisioning or
app-setting mutation helpers. `pilot.py` only checks local inputs and packages code.

After adoption, a normal plan should report no resource changes. Monitoring and
logging are covered by [remote diagnostics](remote-development-observability.md);
worker draining and publication by [remote runtime](remote-development.md#automatic-deployments).

Local validation:

```bash
terraform fmt -check -recursive infra
terraform -chdir=infra/bootstrap init -backend=false -lockfile=readonly
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/azure init -backend=false -lockfile=readonly
terraform -chdir=infra/azure validate
```
