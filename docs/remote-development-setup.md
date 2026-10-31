# Remote development setup

Cloud automation is independent of dotfile installation. Read [runtime](remote-development.md)
and [infrastructure](azure-infrastructure.md) before enrollment.

## Local configuration

Helpers parse root `.env` literally, without execution; quote spaces. Environment
variables take precedence; `--env-file PATH` selects another file.

| Variable | Purpose |
| --- | --- |
| `LINEAR_OAUTH_CLIENT_ID` | OAuth2 Application client ID; preferred with matching secret |
| `LINEAR_OAUTH_CLIENT_SECRET` | Application secret for `client_credentials` |
| `LINEAR_API_KEY` | Fallback when the OAuth pair is incomplete |
| `GITHUB_APP_ID` | Numeric App ID, not Client ID or installation ID |
| `GITHUB_APP_PRIVATE_KEY_FILE` | External PEM path; relative paths resolve against the checkout |
| `LINEAR_WEBHOOK_SECRET` | Signing secret, filled after initial deployment |

Enable **Client credentials tokens** on the `Clusterfork` OAuth2 Application.
The controller POSTs form data to `https://api.linear.app/oauth/token` with
`grant_type=client_credentials` and `scope=read,write,comments:create,issues:create`.
Bearer tokens remain in memory, refresh five minutes before expiry (half-lifetime
when under ten minutes), and refresh/retry once on GraphQL HTTP 401. Configured
OAuth failures never fall back to the key. See [Linear's protocol](https://linear.app/developers/oauth-2-0-authentication#client-credentials-tokens).

Without both OAuth values, `LINEAR_API_KEY` is used unchanged. Its fallback grants
are Read, Write and Create comments for the mapped team. Webhook administration
is separate. Keep the PEM outside the checkout, mode `0600`. Configuration errors
show set/blank status and metadata, excluding secrets, tokens and response bodies.
`automation/common/configuration.py` validates credentials.

Before app-actor enrollment, an existing label operator must provision the team
labels in [queue feedback](remote-development-queue.md#linear-feedback), including
`clusterfork: auto-repair`. The app may apply labels without permission to create
them. Label repair must retain existing labels and task/repair identities.

## GitHub App and mappings

The existing App installation needs Actions, Contents and Pull requests access on
the central engine and targets. Workflows write is required for permitted edits
under `.github/workflows/`; approve installation grants separately from registration
changes. No Administration grant is needed; credentials stay outside targets.

`automation/controller/projects.json` maps explicit Linear project UUIDs to target
repositories/branches, ready/lifecycle states, validation argv arrays, merge policy
and required checks. Use central `clusterfork-task.yml`; the controller selects
its verified runtime. Generation/retry rules belong to [runtime](remote-development.md#retries-and-validation).

Central Actions variable `CLUSTERFORK_BROKER_URL` is
`https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker`.
Workers independently allowlist the host. Relocation must update both contracts
and Terraform; creating a new host alone does not relocate workers.

## Repository onboarding

The [setup-remote-development skill](../skills/setup-remote-development/SKILL.md)
owns enrollment details. Run its helper from central source against an explicit
target checkout with existing access; no per-target workflow, runtime pin or new secret.

Validation commands run in the worker's cleaned environment. Reuse an existing
CI check by its actual check name and bootstrap any repository tools absent from
the shared runtime. The Conky binding installs uv, Lua 5.4 and ShellCheck when
needed, syncs locked dependencies, and sets `CI=1` for pytest so missing Lua
fails validation rather than skipping renderer coverage.

| Action | Purpose |
| --- | --- |
| `discover` | Find real GitHub identity and existing Linear project/team/state UUIDs |
| `configure` | Preview/write mapping and optional validation CI; preserve unrelated files |
| `merge` | Add required checks and automatic squash merge; retain protections |
| `verify` | Check deployed mapping/runtime, workflow/tag, App grants, current CI, states/labels |
| `enroll` | Repeat verification; add existing enrollment project label last |

Read-only `/api/health?setup_project_id=UUID` returns `repository_setup` target/ref,
current branch, App grants, workflow/tag evidence, never tokens. Enrollment requires
committed runtime inputs/exact registry digest, grants, active workflow and passing
current CI; preview Todo candidates before labeling. Bind by UUID, not names.
Idempotent repair preserves enrollment, completed identities, queue and credentials.

## Deployment and identity

[Deployment](remote-development-deployments.md) owns rollout/holds. Its main-only
`azure-production` GitHub environment has no required reviewers.
Its `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID` identify the
existing deployment identity; Terraform owns federation and grants. Manual
recovery of tested main uses the same workflow:

```bash
gh workflow run deploy-azure.yml --repo KianBahasadri/clusterfork --ref main
```

## Linear webhook and first run

The existing webhook uses [direct ingress](remote-development-deployments.md#direct-linear-webhook-ingress).
Its administrator URL change is complete. Future changes must preserve the
subscription and signing secret; steering/project observations recover missed events.

### URL administration investigation (KIAN-938, 2026-10-31)

Available credentials cannot administer URL updates; no helper runs during
setup/enrollment/deployment. Earlier [KIAN-938 findings](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-938/investigate-automating-linear-webhook-url-configuration)
were reviewed against docs/schema without credentials or new authenticated probes:

| Credential / operation | Recorded result and boundary |
| --- | --- |
| Existing personal key / webhook administration | `FORBIDDEN`; actor role versus restricted key grants was not established |
| Normal client-credentials token / webhook administration | `FORBIDDEN`; runtime scopes omit `admin` |
| Client credentials requesting `admin` | `invalid_scope` for this application/grant; does not rule out user OAuth |
| `oauth:create` token / updating Clusterfork itself | `Access denied`; scope does not establish creating-application ownership |

[Webhook administration](https://linear.app/docs/api-and-webhooks) requires effective
admin authority. [User OAuth](https://linear.app/developers/oauth-2-0-authentication)
with `admin` is distinct from the [app actor](https://linear.app/developers/oauth-actor-authorization).
Do not repeat scope experiments: different client-credentials scopes revoke existing
app tokens. Normal scopes were restored; leave the runtime scope string unchanged.

### Establish which configuration owns the URL

The [SDK schema at `8397645`](https://github.com/linear/linear/blob/8397645bc0a496f68896b18ec6453dddd5e51927/packages/sdk/src/schema.graphql)
distinguishes two configurations:

| Configuration | Update surface | Authority |
| --- | --- | --- |
| Workspace `Webhook` with its own URL | `webhookUpdate(id: ..., input: {url: ...})` | Effective workspace webhook-admin access; `creator` alone grants nothing |
| OAuth app webhook with null `Webhook.url`, inheriting client settings | App settings; `oauthApplicationUpdate(..., input: {webhookUrl: ...})` for API-managed apps | Owning-workspace admin for settings; ALPHA API only for apps created by the calling app |

Neither failures nor the successful dashboard edit established the webhook UUID,
creator, association or managing parent. Client-secret possession, app name and
workspace ownership do not prove the API's parent/child relationship. No adoption
mechanism or user-admin bypass is established. An authorized admin can inspect
**Settings > Administration > API** and, for a known ID, query
`POST https://api.linear.app/graphql` without selecting `secret`:

```graphql
query ClusterforkWebhookConfiguration($id: String!) {
  webhook(id: $id) {
    id label url enabled resourceTypes allPublicTeams
    team { id }
    creator { id }
  }
}
```

Record non-secret metadata privately; confirm null URLs in the owning app settings.
A forbidden read does not prove absence and must not trigger replacement creation.

### Smallest administrator procedure for an ingress change

1. In **Settings > Administration > API**, locate the existing webhook or its
   owning **Clusterfork** OAuth app. Record identity, URL, enabled state,
   resources, teams and label without copying the signing secret.
2. Change only the URL to the verified controller HTTPS `/api/linear` endpoint,
   or skip if equal. Preserve subscription, teams, enabled state, label and secret;
   do not recreate, reinstall, reauthorize or rotate.
3. Reopen and verify the same identity and unchanged selections. Retain the exact
   `linear-webhook-secret` reference/version. Observe the next ordinary subscribed
   event's valid signature and durable inbox receipt through existing authorized
   evidence. Do not create an issue/comment merely to test. Local signed requests
   or health alone do not prove secret continuity; record no raw payloads/headers.

No repeat edit is needed for the completed cutover. A genuinely new webhook is a
separate admin task: mapped team, Issue/Project events, plus Comment for steering.
Never apply initial defaults over an existing subscription during URL maintenance.

### Future authorized API verification

For a workspace URL, separately authorize an administrator through authorization-code
OAuth with `actor=user`, explicit `admin` consent and required read/write access,
or an appropriately permitted admin personal key. Keep this operator credential
outside controller/worker configuration. Verify actor/workspace with
`query { viewer { id admin } organization { id } }`, privately check issued scopes,
and read the exact webhook above. Role alone proves neither scope nor ownership.
Compare a confirmed workspace-owned URL; skip if equal, otherwise send only:

```graphql
mutation ClusterforkWebhookUrl($id: String!, $url: String!) {
  webhookUpdate(id: $id, input: {url: $url}) {
    success
    webhook { id url }
  }
}
```

Require GraphQL success and read back identity, URL and unchanged settings. On an
ambiguous response, read before retrying. Omit null defaults, `secret`,
`resourceTypes` and rotation mutations. Verify native delivery with the existing
secret. Stop on denied access, ambiguous identity or changed settings; never create.
This path is unverified for the existing webhook and cannot substitute for an
application-controlled URL. Retain the dashboard path unless a managing app's
ownership/authorization is evidenced. [Manifests](https://linear.app/developers/oauth-app-manifests)
prefill new apps; they neither adopt/update this subscription nor prove continuity.

Future helpers need no-op, preservation, GraphQL/permission, ambiguity and redaction
tests, then authorized live verification. Keep tokens in private memory, outside
argv/history/traces/logged responses/files; never select signing-secret fields.

### First run

Add project enrollment label `clusterfork-symphony` last; the current mapping uses
the Kian Todo UUID. [Enroll accounts](remote-development-accounts.md), then use
ordinary Todo issues. Inspect **Clusterfork task** in Actions and its Linear comment.
Health alone proves neither login nor coding/merge; see [dated acceptance](remote-development-controller.md#live-acceptance-2026-10-30).

## Local validation

The [repository suite](conventions.md#local-validation) is separate from live acceptance:

```bash
python3 -m pip install -r automation/controller/requirements.txt
python3 -m unittest discover -s tests -q
terraform fmt -check -recursive infra
```

CI also imports `controller_main` and `azure.identity.ManagedIdentityCredential`
with `automation` on `sys.path` on Python 3.12, checks
`bash -n automation/controller/host/install.sh`, and runs
`python3 automation/check_cursor_sandbox.py` after installing its CLI dependencies.
