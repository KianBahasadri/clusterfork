# Remote development setup

Read [the runtime overview](remote-development.md) before enrolling a project.
Cloud automation is independent of the dotfile installer. [Azure infrastructure](azure-infrastructure.md)
owns provisioning, identities, persistent state and secret references.

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
and configure its client ID and secret. The controller exchanges them at
`https://api.linear.app/oauth/token` as form data with `grant_type=client_credentials`
and `scope=read,write,comments:create,issues:create`, then uses the returned Bearer
token. Tokens stay in process memory, refresh five minutes before `expires_in`
(half the lifetime for tokens lasting under ten minutes), and refresh/retry once
on a GraphQL HTTP 401. Configured OAuth failures do not fall back to the personal
key. See [Linear's client credentials protocol](https://linear.app/developers/oauth-2-0-authentication#client-credentials-tokens).

Without both OAuth values, the controller uses `LINEAR_API_KEY` unchanged. Create
that fallback personal key under account Security & access with Read, Write, and
Create comments access for the mapped team. Webhook creation is a separate
workspace-admin operation. Store the PEM outside the checkout with mode 0600.
The helper reports only set/blank credential status and config metadata; secrets,
access tokens, and remote response bodies are excluded from errors and logs.

Before enrolling a team with the app actor, provision independent team issue
labels using an existing label operator. The app can apply labels but may lack
permission to create them. Required names are `clusterfork: queued`,
`clusterfork: selected`, `clusterfork: needs clarification`, `clusterfork: auto-repair`
and the seven stage names in `automation/common/reporting.py`. Keep existing
labels and task/repair identity when repairing a missing label.

`automation/common/configuration.py` validates local credentials as literal values.
The onboarding helper validates the registry and deployed controller before enrollment.
The retired local Functions packager and deployment CLI are removed.

## GitHub App and mappings

Use the existing GitHub App installation on the central engine and each target.
It needs Actions, Contents and Pull requests access for dispatch, checkout,
publication and check observation. Workflows write access is required when edits
under `.github/workflows/` are permitted. Updated installation grants must be
approved separately from App registration changes. Administration is not needed
for the App, and credentials stay outside target repositories.

`automation/controller/projects.json` maps explicit Linear project UUIDs to target
repositories/default branches, ready/lifecycle states, validation argv arrays and
merge policy. New central mappings name `clusterfork-task.yml` and required checks;
the controller chooses its own verified runtime version. The central workflow is the execution entry point. A generation change permits eligible issues to run again; it
is not a retry mechanism.

The central repository's `CLUSTERFORK_BROKER_URL` Actions variable is
`https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker`.
The worker independently allowlists the broker host. Relocation must update these
contracts as well as Terraform; creating a new host alone does not relocate workers.

## Repository onboarding

The repository [setup-remote-development skill](../skills/setup-remote-development/SKILL.md)
is canonical for detailed discovery, configuration and enrollment steps. The
helper runs from the central source checkout against an explicit target checkout
and reuses existing operator access. No dotfile install, new account secret,
per-target worker workflow or per-target runtime pin is required.

| Helper action | Purpose |
| --- | --- |
| `discover` | Find the checkout's real GitHub identity and existing Linear project/team/state UUIDs |
| `configure` | Preview/write the central mapping and optional validation CI, preserving unrelated files |
| `merge` | Add required checks and automatic squash-merge settings without replacing existing protections |
| `verify` | Check deployed mapping/runtime, central workflow/tag, App access, current target CI and Linear states/labels |
| `enroll` | Repeat verification and add the existing enrollment project label last |

The deployed controller supplies read-only `repository_setup` evidence from
`/api/health?setup_project_id=UUID`. It identifies the target/ref and current
branch, existing App grants, central task workflow and immutable runtime tag.
It never returns installation tokens or App credentials. Enrollment verifies
committed runtime inputs and exact registry digest; uncommitted/unpublished
runtime changes, missing grants, inactive workflow or failed current CI keep it
disabled. Todo candidates are shown before applying the label.

Project and repository names may differ. The binding uses the selected UUID,
not name similarity. Existing project enrollment is additive/idempotent; repair
preserves its label, completed identities, queue references and credential pool.

## Deployment and identity

[Automatic deployment](remote-development-deployments.md) owns testing, draining,
installation, version verification and independent holds. [Azure infrastructure](azure-infrastructure.md)
owns the resources and Terraform plan gate. Target-only changes reach subsequent
tasks through current checkouts; required runtime changes deploy automatically.

The `azure-production` GitHub environment permits main and has no required
reviewers. Its `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`
variables identify the existing user-assigned deployment identity. Federation and
Azure role grants remain in Terraform.

A manual recovery of tested current main uses the same workflow:

```bash
gh workflow run deploy-azure.yml --repo KianBahasadri/clusterfork --ref main
```

## Linear webhook and first run

The existing webhook URL is retained through stateless signed forwarding; its
signing secret and selected resources remain unchanged. A webhook already pointed
directly at the controller uses the same intake contract. New webhook creation is
a workspace-admin operation, not a routine repository/task step. Select the mapped
team and Issue/Project resources, with Comment/IssueRelation notifications where
available. Active steering and periodic project observations also recover missed
notifications.

After verified configuration, add the project enrollment label
`clusterfork-symphony` last. The current mapping uses the Kian team's Todo UUID.
Enroll accounts using [remote account setup](remote-development-accounts.md), then
use ordinary Todo issues. Inspect **Clusterfork task** in GitHub Actions and the
controller's task comment in Linear. Health metadata alone does not prove a live
agent login or a complete coding/merge run. See [dated controller evidence](remote-development-controller.md#live-acceptance-2026-10-30).

## Local validation

The [repository suite](conventions.md#local-validation) covers task constraints,
signed roles, provider contracts, leases, publication, steering, reporting and
migration. It is separate from live cloud acceptance. Run:

```bash
python3 -m pip install -r automation/controller/requirements.txt
python3 -m unittest discover -s tests -q
terraform fmt -check -recursive infra
```
