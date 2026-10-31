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

The existing webhook delivers directly to the controller. The administrator has
completed the URL change; the relay is removed. The current endpoint and intake
contract belong to [direct Linear ingress](remote-development-deployments.md#direct-linear-webhook-ingress).
Future URL maintenance must retain the existing subscription and signing secret.
Active steering and periodic project observations also recover missed notifications.

### URL administration investigation (KIAN-938, 2026-10-31)

**Result:** URL updates exist in Linear's API, but the available Clusterfork
credentials cannot administer the existing configuration. Use the administrator
procedure below until separate operator authorization and ownership are verified.
No update helper is installed or invoked by setup, enrollment or deployment.

The following are the earlier outcomes recorded in
[KIAN-938](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-938/investigate-automating-linear-webhook-url-configuration),
not new probes. This investigation used official documentation, the public SDK
schema and repository evidence. No Linear credentials were available in the task
checkout; no authenticated query, mutation, token issuance or permission change
was performed.

| Credential / operation | Recorded result and permission boundary |
| --- | --- |
| Existing personal API key / webhook administration | `FORBIDDEN`. The documented fallback grants are Read, Write and Create comments, without Admin. A personal key is a user credential constrained by both its grants and that user's access. The issue provides no actor-role/key-grant snapshot to distinguish a non-admin user from a restricted key; neither failure nor successful issue access proves webhook authority. |
| Normal client-credentials token / webhook administration | `FORBIDDEN`. `automation/common/clients.py` requests exactly `read,write,comments:create,issues:create`. This is an app actor token without `admin`; issue/comment access does not include webhook administration. |
| Client credentials requesting `admin` | `invalid_scope`. That grant/scope combination was rejected for this application; no admin token was obtained. This does not establish that user OAuth authorization with `admin` is unsupported. |
| Token issued with `oauth:create` / `oauthApplicationUpdate` on Clusterfork itself | `Access denied`. Accepting a scope does not confer ownership of the target application. The public management API is restricted to applications created by the calling OAuth application; authenticating as Clusterfork is not that parent/child relationship. |

[Linear's API administration guide](https://linear.app/docs/api-and-webhooks)
requires admin permissions to manage webhooks and describes personal-key
restrictions. The [webhook guide](https://linear.app/developers/webhooks) permits
workspace administrators or OAuth applications with `admin` to create/read them.
[OAuth authentication](https://linear.app/developers/oauth-2-0-authentication)
documents user consent for `admin` and the app actor used by client credentials;
[actor authorization](https://linear.app/developers/oauth-actor-authorization)
distinguishes user authorization from app installation.

Do not repeat scope experiments against the running app. Linear documents that
requesting different client-credentials scopes revokes existing app actor tokens.
KIAN-938 records that normal scopes were restored; the runtime scope string stays
unchanged.

### Establish which configuration owns the URL

Linear has two URL settings. Its
[official SDK schema, revision `8397645`](https://github.com/linear/linear/blob/8397645bc0a496f68896b18ec6453dddd5e51927/packages/sdk/src/schema.graphql)
defines these separately:

| Existing configuration | URL update surface | Required authority |
| --- | --- | --- |
| Workspace `Webhook` with its own URL | `webhookUpdate(id: ..., input: {url: ...})` | Effective workspace webhook-admin access; see the user OAuth verification path below. `creator` identifies the creating user, not an ownership grant to the controller app. |
| OAuth application's webhook; `Webhook.url` is null and delivery inherits the client setting | Application settings, or `oauthApplicationUpdate(id: ..., input: {webhookUrl: ...})` for an API-managed application | An administrator in the application's owning workspace can use settings. The API is marked ALPHA and limited to applications created by the calling application; `oauth:create` is not self-management permission. |

The existing failure evidence and successful administrator edit do **not** identify
the webhook UUID, creator, application association or managing parent. Do not infer
them from the name Clusterfork, a delivery URL, or possession of its client secret.
Application ownership in a workspace and the API's creating-application relationship
are distinct. There is no documented adoption mechanism here for making the current
application its own managed child. A user OAuth token with `admin` must not be
assumed to bypass that API restriction.

An already authorized administrator can identify the existing entry in **Settings
> Administration > API**, including the matching OAuth application when applicable.
For a known webhook ID, an authorized operator may read the following from
`POST https://api.linear.app/graphql`; it intentionally omits `secret`:

```graphql
query ClusterforkWebhookConfiguration($id: String!) {
  webhook(id: $id) {
    id label url enabled resourceTypes allPublicTeams
    team { id }
    creator { id }
  }
}
```

Record only non-secret identity and configuration metadata in the approved operator
record. Confirm a null URL against the owning application's settings. A forbidden
read is a permission failure, not evidence that the webhook is absent; stop rather
than creating a replacement.

### Smallest administrator procedure for an ingress change

No new persistent credential is needed for the existing dashboard path:

1. An administrator opens **Settings > Administration > API** in the webhook's
   workspace. Locate the existing webhook; for an application-controlled URL,
   open the existing **Clusterfork** OAuth application in its owning workspace.
   Confirm its identity and record the current URL, enabled state, selected
   resources, team selection and label without copying the signing secret.
2. Set **only the webhook URL** to the verified controller HTTPS endpoint ending
   in `/api/linear`. Use the endpoint in the ingress contract linked above for the
   current deployment, or the explicitly authorized replacement for a future move.
   If it already matches, make no edit. Save on the existing configuration.
   Leave subscriptions, team scope, enabled state, label and signing-secret controls
   untouched; do not recreate, reinstall, reauthorize or rotate secrets.
3. Reopen the configuration and confirm the same identity, the intended URL and
   unchanged selections. Keep the controller's existing `linear-webhook-secret`
   secret reference/version unchanged. Observe the next ordinary subscribed
   Linear event reaching the controller with a valid signature and durable inbox
   receipt. Use existing authorized evidence, not a new issue/comment mutation for
   this check. Locally generated signed requests or health responses alone do not
   prove Linear retained the signing secret. Record only the receipt outcome and
   non-secret metadata, not raw payloads, headers or secrets.

The completed cutover does not need another administrator edit. Initial installation
of a genuinely new webhook remains a separate admin task: select the mapped team
and Issue/Project events, adding Comment events when required by steering. Do not
apply these initial defaults over an existing subscription during URL maintenance.

### Future authorized API verification

For a workspace webhook, the supported candidate is a separately authorized
workspace administrator using the OAuth authorization-code flow with `actor=user`
and explicit `admin` consent (plus the required read/write access). This is a new
operator authorization requirement, not a change to the controller credentials.
An admin's appropriately permitted personal key is another administrator credential;
the existing fallback key is not evidence of such access. Keep either credential
outside the controller and worker configuration.

After authorization, verify the actor/workspace with
`query { viewer { id admin } organization { id } }`, check the issued token's
granted scopes privately, and read the exact webhook with the query above. A user
role alone does not prove the token's scope or the target's ownership. For a
confirmed workspace-owned URL, compare it first and skip the mutation if equal:

```graphql
mutation ClusterforkWebhookUrl($id: String!, $url: String!) {
  webhookUpdate(id: $id, input: {url: $url}) {
    success
    webhook { id url }
  }
}
```

Omit every other input, particularly `secret` and `resourceTypes`; never send null
defaults or call a secret-rotation mutation. Require GraphQL success and read back
the same ID, URL and unchanged settings. After an ambiguous response, read again
before retrying. Repeating the procedure should skip an already matching URL.
Verify native delivery with the existing signing secret as above. Stop on denied
access, missing/ambiguous identity or unexpected changes; never fall back to create.

This path has **not** been verified against the existing webhook. If its URL is
application-controlled, the workspace mutation is not an established substitute
for updating the owning application. Keep the dashboard procedure unless an
existing managing application and its authorization are evidenced; evaluate that
ALPHA API separately before promising automation.
[Application manifests](https://linear.app/developers/oauth-app-manifests) can
prefill new-app webhook configuration for administrator confirmation, but do not
update/adopt the existing subscription or establish signing-secret continuity.

Any future helper needs local tests for equality/no-op, URL-only mutation,
preserved settings, GraphQL/permission failures, ambiguous-response recovery and
secret redaction, followed by explicit operator-authorized verification. Keep
tokens in private process memory; exclude them from command arguments, shell
history, debug traces, raw response logging and repository files. Never select
signing-secret fields for URL maintenance.

### First run

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
