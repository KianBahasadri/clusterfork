# Remote development setup

Read [the runtime overview](remote-development.md) before enrolling a project.
Cloud automation is independent of the dotfile installer. Infrastructure is
managed through [Terraform](azure-infrastructure.md); tests and packaging remain local.

## Local configuration

The helper reads the checkout's root `.env` as literal dotenv values; it does
not execute it. Quote values containing spaces. Environment variables take
precedence. `--env-file PATH` selects another file.

| Variable | Purpose |
| --- | --- |
| `LINEAR_OAUTH_CLIENT_ID` | Linear OAuth2 Application client ID; preferred with its matching secret |
| `LINEAR_OAUTH_CLIENT_SECRET` | OAuth2 Application client secret for the `client_credentials` grant |
| `LINEAR_API_KEY` | Fallback Linear credential when the OAuth client ID/secret pair is incomplete |
| `GITHUB_APP_ID` | Numeric App ID, not Client ID or installation ID |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Path to the App PEM outside the repo; relative paths resolve against the checkout |
| `LINEAR_WEBHOOK_SECRET` | Linear signing secret, filled after initial deployment |

Enable **Client credentials tokens** on the `Clusterfork` OAuth2 Application
and configure its client ID and secret. The dispatcher exchanges them at
`https://api.linear.app/oauth/token` as form data with `grant_type=client_credentials`
and `scope=read,write,comments:create,issues:create`, then uses the returned Bearer
token. Tokens stay in process memory, refresh five minutes before `expires_in`
(half the lifetime for tokens lasting under ten minutes), and refresh/retry once
on a GraphQL HTTP 401. Configured OAuth failures do not fall back to the personal
key. See [Linear's client credentials protocol](https://linear.app/developers/oauth-2-0-authentication#client-credentials-tokens).

Without both OAuth values, the dispatcher uses `LINEAR_API_KEY` unchanged. Create
that fallback personal key under account Security & access with Read, Write, and
Create comments access for the mapped team. Webhook creation is a separate
workspace-admin operation. Store the PEM outside the checkout with mode 0600.
The helper reports only set/blank credential status and config metadata; secrets,
access tokens, and remote response bodies are excluded from errors and logs.

```bash
python3 automation/pilot.py check
```

`check` validates required local values and the registry. It reports workflow
filenames/refs, model/effort, enrollment label, and quota policy. Worker mappings
report `Codex worker`; mappings all using `clusterfork-pilot.yml` report
`routing pilot (no coding agent)`. It neither launches a worker nor verifies a
live agent login. Linear configuration is complete with either both OAuth values
or an API key; `/api/health` uses the same rule. A missing webhook secret is allowed
for the initial local check but required by runtime health. Other missing required
values fail the check.

## GitHub App and repository

1. [Register a GitHub App](https://github.com/settings/apps/new) for the owning
   account. Use the repository URL as its homepage, leave OAuth/callback/setup
   URLs empty, and disable the App's GitHub webhook.
2. Grant Repository **Actions**, **Contents**, and **Pull requests** read/write;
   Metadata read is automatic. The dispatcher needs Actions write, and worker
   publication needs Contents/Pull requests write. It has no Workflows or
   Administration grant.
3. Record the numeric App ID and download a private key into the paths above.
4. Install the App on the selected repositories. Approve expanded permissions
   if updating an existing installation. The dispatcher discovers installation
   IDs and issues short-lived repository-scoped tokens.
5. Place the worker workflow on each mapping's default target branch before
   dispatching. The deploy helper does not publish workflows.
6. Enable repository auto-merge and branch protection with the required CI checks
   for mappings using automatic merges. Alternatively set `auto_merge: false`.
   The runner refuses to request automatic merging without observable required
   checks; it does not bypass branch protection.

`automation/azure/projects.json` maps Linear project UUIDs to repository,
workflow, ref, ready-state IDs, lifecycle-state IDs, validation argv arrays,
and merge policy. Another repository needs both an App installation grant and
its mapping/workflow. A generation change permits every eligible issue to run
again; it is not a targeted retry switch.

## Azure deployment

[Azure infrastructure](azure-infrastructure.md) owns Terraform provisioning,
existing-resource adoption, protected state, runtime credentials, and cloud roles.
Use that workflow for resource and configuration changes. `pilot.py` remains a
local configuration check and source packager; it no longer provisions Azure or
writes app settings.

Routine source updates use [automatic deployment](remote-development.md#automatic-deployments).
The workflow reconciles Terraform before deploying the tested source package.
The package uses an explicit allowlist and excludes `.env`, PEMs, auth caches,
and unrelated local keys.

The existing deployment uses:

| Resource | Name |
| --- | --- |
| Function App | `clusterfork-dispatch-7604cac329` |
| Storage account | `cfdispatch7604cac329` |
| Resource group | `rg-clusterfork-automation` |

Relocating it also requires updating the broker URL allowlist in
`automation/worker.py`, the workflow's broker URL, and storage/group names in
`automation/accounts.py`. Creating new resources alone does not relocate workers.

## Automatic deployment identity

The `azure-production` GitHub environment permits only main and has no required
reviewers. Its `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`
variables identify the user-assigned deployment identity. Azure permissions and
GitHub OIDC federation are declared in Terraform; see
[credentials and identity](azure-infrastructure.md#credentials-and-identity).

Run a recovery against the current tested main with:

```bash
gh workflow run deploy-azure.yml --repo KianBahasadri/clusterfork --ref main
```

## Linear webhook and first run

1. Create one Linear webhook under workspace **API → Webhooks**, targeting the
   printed `/api/linear` URL. Select the mapped team and **Issues** and
   **Projects** resource types.
2. Store its signing secret through the Terraform credential workflow.
3. Check `/api/health` returns 200 and `"missing": []`. This verifies runtime
   configuration presence, not account quota or an end-to-end coding run.
4. Inspect the mapped project's ready issues before adding the **project label**
   `clusterfork-symphony`: enrollment can dispatch existing ready issues. The
   current Clusterfork mapping uses the Kian team's Todo UUID.
5. Enroll Codex accounts using [remote account setup](remote-development-accounts.md).
   Create or select a Todo issue and inspect **Clusterfork Codex worker** in
   [Actions](https://github.com/KianBahasadri/clusterfork/actions). Its summary
   reports the model, outcome, merge policy, and PR link. The authenticated
   broker supplies issue text, not workflow inputs.
6. A second issue edit should not create another dispatch for the same registry
   generation. Read [retry behavior](remote-development.md#retries-and-troubleshooting)
   before trying to rerun it.

## Local validation

The [repository suite](conventions.md#local-validation) covers routing, worker
identity, leases, quotas, publication/recovery, and lifecycle reconciliation.
It uses fixtures/mocks rather than live cloud writes. Packaging is also local:

```bash
python3 automation/pilot.py package --output /tmp/clusterfork-function.zip
```

Runtime dependencies are pinned in `automation/azure/requirements.txt`.
CI installs them in Python 3.12 and imports the Function App to index all five
handlers. Reproduce in an isolated environment:

```bash
python3.12 -m venv /tmp/clusterfork-routing-venv
/tmp/clusterfork-routing-venv/bin/pip install -r automation/azure/requirements.txt
/tmp/clusterfork-routing-venv/bin/python - <<'PY'
import sys
sys.path.insert(0, 'automation/azure')
import function_app
names = {fn.get_function_name() for fn in function_app.app.get_functions()}
assert names == {'health', 'linear_webhook', 'route_event', 'reconcile', 'worker'}, names
print('All five functions indexed')
PY
```

Protocol references: [Linear webhooks](https://linear.app/developers/webhooks),
[GitHub workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event),
[Azure queues](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-storage-queue-trigger),
[Flex Consumption](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan).
