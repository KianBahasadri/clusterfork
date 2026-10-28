# Remote development setup

Read [the runtime overview](remote-development.md) before enrolling a project.
Cloud automation is independent of the dotfile installer. These commands are
operator setup steps; repository tests and packaging do not deploy anything.

## Local configuration

The helper reads the checkout's root `.env` as literal dotenv values; it does
not execute it. Quote values containing spaces. Environment variables take
precedence. `--env-file PATH` selects another file.

| Variable | Purpose |
| --- | --- |
| `LINEAR_API_KEY` | Read mapped projects/issues and labels, update issue state, and create/update status comments |
| `GITHUB_APP_ID` | Numeric App ID, not Client ID or installation ID |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Path to the App PEM outside the repo; relative paths resolve against the checkout |
| `LINEAR_WEBHOOK_SECRET` | Linear signing secret, filled after initial deployment |

Create the Linear personal key under account Security & access settings with
Read, Write, and Create comments access for the mapped team. Webhook creation
is a separate workspace-admin operation. Store the PEM outside the checkout
with mode 0600. The helper reports only set/blank credential status and config
metadata.

```bash
python3 automation/pilot.py check
```

`check` validates required local values and the registry. It reports workflow
filenames/refs, model/effort, enrollment label, and quota policy. Worker mappings
report `Codex worker`; mappings all using `clusterfork-pilot.yml` report
`routing pilot (no coding agent)`. It neither launches a worker nor verifies a
live agent login. A missing webhook secret is allowed for initial deployment;
other required missing values fail before resource creation.

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

With Azure CLI authenticated to the intended subscription:

```bash
az account show --query '{name:name,state:state}'
python3 automation/pilot.py deploy
```

The helper creates `rg-clusterfork-automation` in `eastus`, a deterministic
subscription-derived StorageV2 account, the event queue, and a Python 3.12
Flex Consumption Function App. New apps use 512 MB instances, a 40-instance
maximum, no always-ready instances, and no Application Insights resource.
Normal Azure compute/storage charges and allowances apply.

Credentials go to app settings through a temporary mode-0600 file. The source
ZIP uses an explicit allowlist and remote dependency build; it excludes the
root `.env`, PEM, auth caches, and unrelated local keys. `configure` updates
runtime credentials without redeploying source:

```bash
python3 automation/pilot.py configure
```

The recorded deployment uses these resources:

| Resource | Name |
| --- | --- |
| Function App | `clusterfork-dispatch-7604cac329` |
| Storage account | `cfdispatch7604cac329` |
| Resource group | `rg-clusterfork-automation` |

The helper accepts `--resource-group` and `--location`. Moving the deployment
also requires updating the broker URL allowlist in `automation/worker.py`, the
workflow's broker URL, and the storage/group names in `automation/accounts.py`.
Creating differently named Azure resources alone does not relocate the worker.

The helper prints `/api/linear` and `/api/health` URLs. With no webhook secret,
health returns 503, webhook requests are rejected, and the timer stays idle.
Azure hostname responses are accepted at either the root or `properties` level;
a missing hostname is an error.

## Linear webhook and first run

1. Create one Linear webhook under workspace **API → Webhooks**, targeting the
   printed `/api/linear` URL. Select the mapped team and **Issues** and
   **Projects** resource types.
2. Set its signing secret in `LINEAR_WEBHOOK_SECRET`, then run `configure`.
3. Check `/api/health` returns 200 and `"missing": []`. This verifies runtime
   configuration presence, not account quota or an end-to-end coding run.
4. Inspect the mapped project's ready issues before adding the **project label**
   `symphony`: enrollment can dispatch existing ready issues. The current
   Clusterfork mapping uses the Kian team's Todo UUID.
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
