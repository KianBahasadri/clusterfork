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

Before enrolling a team with the app actor, create its team-scoped issue labels
using a user account that can manage labels. The app can apply existing labels,
but label creation can be rejected with `FORBIDDEN`; that rejection also prevents
automatic repair from creating its sub-issue. Provision all of these names:
`clusterfork: queued`, `clusterfork: selected`, `clusterfork: needs clarification`,
`clusterfork: auto-repair`, and the seven stage names in
`automation/azure/worker_reporting.py`'s `STAGE_LABELS`, including
`clusterfork: waiting for quota`. Keep each label independently selectable.
If repair already has a durable intent, create the missing label and reconcile;
retain its original issue UUID, dispatch receipt, queue position, and repair budget.

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
   publication needs Contents/Pull requests write. To permit automated edits under
   `.github/workflows/`, also grant **Workflows** read/write and approve the updated
   installation permissions. Changing the App registration alone does not update
   an installation's grant. Administration access is not required.
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
and merge policy. Shared-worker routes also carry an immutable `worker_revision`
and `required_checks`. Another repository needs both an App installation grant and
its mapping/workflow. A generation change permits every eligible issue to run
again; it is not a targeted retry switch.

## Repository onboarding

Invoke the repository [setup-remote-development skill](../skills/setup-remote-development/SKILL.md)
in the target checkout. Its detailed discovery and enrollment procedure is
canonical in that skill. The helper lives in the central source checkout and
needs no dotfile installation in the target. It reuses the existing GitHub/Linear
operator access; it never changes authentication or installs repository secrets.

`automation/onboard.py` provides these operations:

| Operation | Result |
| --- | --- |
| `discover --checkout PATH` | Reads GitHub origin/default branch and existing Linear project UUIDs; `--project-id UUID` discovers its teams and states |
| `configure --checkout PATH --project-id UUID ...` | Previews the explicit UUID mapping and thin worker workflow; `--apply` writes local files |
| `configure ... --install-ci` | Also prepares a separate validation workflow using the configured argv commands; existing CI is preserved |
| `merge --checkout PATH --project-id UUID` | Previews automatic squash merge settings and additive required-check protection; `--apply` writes with existing operator permissions |
| `verify --checkout PATH --project-id UUID` | Verifies the published/deployed setup and lists existing eligible Todo candidates without enrolling |
| `enroll --checkout PATH --project-id UUID` | Repeats verification, applies the project label last, and verifies it by reading it back |

There is no project-name/repository-name inference. Select the existing project
and team before preparing configuration, and supply discovered state UUIDs.
Validation commands must test meaningful repository behavior and include needed
dependency setup. Existing CI can supply required checks; the optional generated
workflow runs the commands on Ubuntu with Python 3.12 and Node 22 for pushes/PRs.
Merge setup retains all existing protection and uses a branch ruleset with no
bypass actors to add missing checks. The App still needs no Administration grant;
changing repository settings requires the existing repository operator's access.
Missing access or a conflicting existing workflow/ruleset leaves setup incomplete.

Configuration is an upsert keyed by project UUID. It refuses to silently remap
that UUID to another repository, retains other mappings and policy, and writes
nothing on an identical rerun. Existing required checks accumulate instead of
being removed. Enrollment adds the existing project label with `projectAddLabel`,
preserving unrelated labels, and is a no-op when it is already applied. Setup
does not change the generation, issue states, receipts, or cloud resources.

### Shared worker versions and caller identity

New repositories carry only `clusterfork-worker.yml`, which calls the central
`clusterfork-select-account.yml` and `clusterfork-develop.yml` at a full commit
SHA. Each called workflow checks that its OIDC `job_workflow_sha` equals the
engine revision before checking out central code. The engine and pinned CLI
setup run outside the target's git checkout; validation, agent edits, and
publication operate in the actual caller checkout. No implementation files are
copied into enrolled repositories. Clusterfork's own caller uses its current
commit with local reusable workflow references.

The broker authorizes the actual caller repository, caller workflow/ref, hosted
runner, event, run identity, and durable dispatch. For a shared route it also
requires the exact central reusable workflow/ref and pinned `job_workflow_sha`.
It never grants access from the central worker identity alone. Account preflight
retains its restricted signed-workflow role; development receives only the bound
account. Every new route uses the existing repository serial queue and readiness
review. GitHub documents the distinction between caller claims and
[`job_workflow_ref`](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows).

Publish shared worker updates once, then update the thin callers' SHA references
and their central `worker_revision` mappings through normal validation/review.
The helper verifies the contract loaded from that exact git revision against the
deployed broker's protocol. A protocol-changing update needs coordinated caller
pins while the existing deployment pause is held; resume only after compatibility
is restored. Deployment checks each enrolled pin's contract before packaging and
verifies each installed caller before resuming. Hold admissions before changing
an enrolled caller's pin. Existing pins continue to select their original implementation.
The shared engine checkout uses Clusterfork's public source. A private central
source needs runtime read access for the caller's Actions token; verification
reports that missing access instead of treating workflow sharing alone as sufficient.

### Enrollment evidence

Health exposes `shared_worker_version`, the readiness protocol, and the canonical
registry digest. `GET /api/health?setup_project_id=UUID` additionally has the
broker verify App installation grants and the active worker workflow's content
digest on that mapped branch. It returns only setup metadata; the App key and
short-lived installation tokens remain inside the broker. During a deployment
pause, `workers_drained` checks all deployed mapped repositories through the App.
The deployment job uses this evidence for external repositories instead of
retrieving keys or assuming its own `GITHUB_TOKEN` can read them.

Before enabling a project, `verify` requires the complete local registry/source
digests to match the deployed package, a compatible pinned protocol, resumed
serial admission, active Actions/workflows, approved App grants, effective required
checks, successful current default-branch CI, and required Linear states/labels.
Publish target workflows before deploying their central mapping. Deploy the
shared-worker support itself before enrolling another repository. Keep the label
off until verification passes, and show the existing Todo candidates first.
Candidates still pass through dependencies, durable dispatch deduplication, quota,
and readiness review. If access is missing, report the precise operator step;
do not claim enrollment succeeded. Once enabled, project label + Todo suffices.

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
The shared `setup-codex` action installs Python 3.12 and these dependencies before
account selection or agent execution, so configured worker validation uses the
same packages as CI. Installation failures stop setup before leasing an account.
CI also imports the Function App to index all five handlers. Reproduce in an isolated environment:

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
