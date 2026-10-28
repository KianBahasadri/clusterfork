# Remote development

Eligible Linear issues route through one shared Azure Function App to a Codex
worker on a standard GitHub-hosted runner. The worker selects a leased account
using live quota, implements the issue, validates the repository, and opens a PR.
Worker PRs default to automatic squash merging after GitHub's required CI checks
pass, with no human approval required. Deployment and Linear status updates are separate.
This is Clusterfork's own execution adapter, not an installation of upstream Symphony.

## What exists

- `automation/azure/function_app.py`: signed HTTP webhook, queue consumer, health
  endpoint, and shared 15-minute reconciliation timer.
- `automation/azure/dispatch_core.py`: project enrollment, current-state rules,
  repository routing, and dispatch receipt handling.
- `automation/azure/dispatch_clients.py`: Linear GraphQL, GitHub App authentication,
  and Azure Table receipts.
- `automation/azure/projects.json`: non-secret project-to-repository mappings.
- `automation/azure/worker_core.py` and `worker_store.py`: trusted workflow
  authorization, account leases, persisted credentials, and worker outcomes.
- `automation/worker.py` and `codex_runtime.py`: Codex account inspection,
  execution, validation, branch push, PR publication, and auto-merge requests.
- `automation/accounts.py`: enroll saved Codex profiles for cloud use or reclaim
  their refreshed credentials for local use.
- `.github/workflows/clusterfork-worker.yml`: Codex CLI 0.165.0 on standard
  `ubuntu-24.04`, 45-minute job timeout, repository read and OIDC permissions.
- `.github/workflows/clusterfork-pilot.yml`: manual/API-triggered smoke workflow;
  retained for routing-only checks. It does not run a coding agent.
- `automation/pilot.py`: local configuration check, allowlisted source packaging,
  Azure deployment, and runtime secret configuration. It does not use or change
  Clusterfork's dotfile installer.

The shared Azure Function App is deployed.
The full Linear webhook → Azure queue → GitHub App → hosted runner path has passed
a live test. Credentials stay outside version control; the root `.env` is
gitignored, and the App private key lives outside the repo.

## Live pilot validation

The live smoke test used
[KIAN-891](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-891/verify-clusterforks-linear-azure-github-routing-pilot)
in the Clusterfork project on 2026-10-28 UTC. The project has the `symphony` project
label. The issue was eligible in Todo during testing and is now Done.

- A real Linear issue update triggered a successful
  [routing workflow](https://github.com/KianBahasadri/clusterfork/actions/runs/40607700052)
  as `clusterfork[bot]`, with the correct issue and project IDs.
- A second Linear update and a signed duplicate delivery produced no additional
  workflow: one successful bot-triggered run, one `dispatched` Azure receipt,
  and an empty event queue after processing.
- [Repository CI and the Python 3.12 runtime check](https://github.com/KianBahasadri/clusterfork/actions/runs/40607363546)
  passed. An earlier direct
  [routing smoke job](https://github.com/KianBahasadri/clusterfork/actions/runs/40606446755)
  also passed on a standard GitHub-hosted runner.
- Azure resources are in `rg-clusterfork-automation`: Function App
  `clusterfork-dispatch-7604cac329` and storage account `cfdispatch7604cac329`.
  The app uses the Flex settings described below.
- Webhook endpoint:
  `https://clusterfork-dispatch-7604cac329.azurewebsites.net/api/linear`.
  Its `/api/health` endpoint returns 200 with `"missing": []`. Live requests with
  forged signatures or expired signed timestamps returned 401.
- Real Azure Table claim/duplicate-claim/update operations passed, as did a
  queue send/receive/delete round trip. Temporary test records and the temporary
  storage-test queue were removed afterwards.
- The GitHub App is installed on Clusterfork, and the Linear webhook is configured
  for the Kian team's Issues and Projects. The Linear API key has read access and
  correctly refuses webhook administration; the webhook was created in the UI.

The results above validate the original routing-only milestone.

The first full Codex coding test also passed on 2026-10-28 UTC:

- Creating [KIAN-892](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-892/report-the-configured-codex-worker-mode-in-the-setup-checker)
  triggered the [worker run](https://github.com/KianBahasadri/clusterfork/actions/runs/40743398281)
  automatically as `clusterfork[bot]`.
- The runner inspected five accounts, selected one with 100% remaining in its
  limiting quota window, and ran `gpt-6.1-sol` at `max` effort.
- Codex implemented the checker fix, added tests, and updated its owning doc.
  All 300 tests in that branch passed. The worker opened
  [PR #1](https://github.com/KianBahasadri/clusterfork/pull/1), initially for human review.
- Both [push CI](https://github.com/KianBahasadri/clusterfork/actions/runs/40744114226)
  and [PR CI](https://github.com/KianBahasadri/clusterfork/actions/runs/40744127352)
  started automatically and passed.
- Refreshed credentials for all five cloud accounts were persisted; all account
  leases were released after execution. The active local profile was preserved.
- The deployed health endpoint reports `codex-worker` with HTTP 200. Missing and
  forged workflow credentials were rejected with HTTP 401.
- A subsequent Linear update produced no second worker run or dispatch receipt.

## Automatic merges

Project mappings default `auto_merge` to `true`; set it to `false` for a project
that should stop at an open PR. The broker sends this policy to the runner.
New PRs and recovered open PRs request native GitHub auto-merge using squash,
bound to the observed head commit. The runner verifies the repository, base,
worker branch, bot author, and dispatch marker before requesting a merge.

Clusterfork enables repository auto-merge and deletion of merged PR branches.
Its `main` protection requires `test` and `routing-runtime` from GitHub Actions
(App ID 15368), with zero review approvals. Existing administrator bypass remains
available for repository maintenance; worker App tokens have no such bypass.
Checks use non-strict mode so concurrent PRs do not wait for manual base updates.
An update to `main` alone does not force another CI run on an older PR branch.

The runner refuses auto-merge if repository auto-merge or required PR checks are
missing. It reads PR-specific required checks using its existing App permissions;
no Administration grant is needed. GitHub handles pending/failed CI and merge conflicts; the
worker never uses `--admin` or force-merges. A completed worker outcome means its
coding/publication work finished; the PR can still be waiting for CI. The Azure
outcome also records whether auto-merge was requested. Recovery recognizes merged
PRs so a retry does not repeat work if merging preceded outcome persistence.
Reference: [GitHub auto-merge](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request).

## Agent profiles

`automation/azure/projects.json` currently enables only Codex:

| Agent | Model ID | Reasoning effort |
| --- | --- | --- |
| Codex | `gpt-6.1-sol` | `max` |

The registry requires an explicit Codex profile and supported effort value. Each
issue uses one account selected by available quota. There is no complexity
classifier or model fallback. Antigravity is deferred. The runner verifies that
the pinned model and effort are in each account's live model catalog before use.

`quota_selection.py` implements the decision over provider-normalized snapshots:
choose the account with the lowest used percentage in its **most depleted**
applicable window. Both short and weekly/model limits matter. Skip busy, blocked,
unknown, stale, and nearly exhausted accounts. Configuration currently requires
at least 20% remaining, a snapshot at most 60 seconds old, and one active task per
account. Those are initial configurable operating values, not a prediction of a
task's consumption. If no account qualifies, return no selection and wait.

The runner calls Codex app-server `account/read`, `model/list`, and
`account/rateLimits/read` in isolated temporary Codex homes. It probes available
accounts under leases, selects by the worst applicable quota window, then
reserves and rechecks the chosen account before starting `codex exec`.
Unknown or denied usage never counts as unused quota. A lack of capacity records
`waiting_for_quota` and allows reconciliation to retry after 15 minutes.
References: [Codex app-server](https://learn.chatgpt.com/docs/app-server),
[Sol model and efforts](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

## Accounts and token refresh

The root `.env` and GitHub App PEM are not agent credentials. Saved Codex profiles
come from `~/.local/share/clusterfork-auth/codex/auth.json.*`. Enrollment includes
**all saved profiles by default, including the currently active account**, and
deduplicates aliases by provider account identity. The live pool now contains all
six saved accounts. Existing cloud credentials are retained when enrollment runs
again, so stale local copies cannot overwrite refreshed cloud tokens.

The local `remote-profiles.json` manifest records cloud ownership. The Codex
rotator skips those profiles for new local selections. Enrollment preserves the
current symlink and local credential files; it does not stop or lease an already
running local session. Remote leases coordinate cloud jobs only. Simultaneous
local and cloud OAuth refreshes are not synchronized and can still conflict;
reclaim a profile before starting further local use of its credentials.

Use an isolated Python environment with the Azure dependencies:

```bash
python3 -m venv ~/.config/clusterfork/automation/venv
~/.config/clusterfork/automation/venv/bin/pip install -r automation/azure/requirements.txt
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py enroll
```

Use `--profile NAME` to enroll particular profiles, including the active one.
`list` reports the number reserved. Reclaim a profile before using it locally again:

```bash
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py reclaim --profile NAME
```

Reclaim refuses busy accounts or changed local credentials, persists the latest
cloud credentials locally, then removes cloud ownership. Keep the installed
rotator updated when enabling this feature.

Private Azure Table `ClusterforkAccounts` holds auth caches. Each provider account
has one 120-second lease with a random fencing token and ETag updates. Jobs renew
every 20 seconds and persist refreshed auth during execution and on exit. A lost
lease stops the agent. A crash between OAuth refresh and persistence can require
re-login; no system can recover a refresh token that was never saved.

The App private key stays in Azure. The broker verifies GitHub's OIDC signature,
issuer, audience, expiry, repository, main-branch workflow path, hosted-runner type,
run ID/attempt, and a durable dispatch receipt. It also checks the run through
GitHub's API and requires dispatch by `clusterfork[bot]`. PR workflows, forks,
other workflows, and unmapped repositories cannot obtain account credentials.
Publication tokens are scoped to one repository with Contents and Pull requests
write permission. They are issued again before publication and never passed in
workflow inputs. Account caches and agent transcripts are not uploaded as artifacts.

The user selected subscription-account auth. OpenAI recommends API keys for
automation and advises against using account-auth CI on public repositories;
the broker restricts access to the trusted dispatcher workflow, but does not change
that guidance. References: [Codex authentication](https://learn.chatgpt.com/docs/auth),
[non-interactive account auth](https://learn.chatgpt.com/docs/non-interactive-mode),
[GitHub OIDC](https://docs.github.com/en/actions/reference/security/oidc).

The project scan fetches enrollment labels once per page, outside the issues
connection. Repeating the label connection for each issue exceeds Linear's query
complexity limit at a 50-issue page size.

## Fill the local configuration

Edit `/home/kian/clusterfork/.env`. Keep existing entries; fill the blank variables
added for this pilot. Do not paste secret values into chat or commit them.

| Variable | Value |
| --- | --- |
| `LINEAR_API_KEY` | Personal API key with read access to the Kian team |
| `GITHUB_APP_ID` | Numeric **App ID**, not Client ID or installation ID |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Absolute path to the downloaded GitHub App PEM |
| `LINEAR_WEBHOOK_SECRET` | Signing secret from the Linear webhook; filled after initial deployment |
| `COMMAND_CODE_API_KEY` | Unused by the current Antigravity/Codex configuration |

Use quoted values if needed. The helper reads literal dotenv values; it never
executes the file as shell code. Environment variables override the file.

Create the Linear key in **Settings → Account → Security & access → Personal API
keys**. Use read permission and limit team access to Kian if offered. The pilot
only reads issues, states, project labels, and project issue lists. Creating the
webhook below is a separate workspace-admin action in the UI.

## Register and install the GitHub App

1. Open [New GitHub App](https://github.com/settings/apps/new) under your personal
   account. Suggested name: `Clusterfork Kian Dispatcher` (must be globally unique).
2. Set Homepage URL to `https://github.com/KianBahasadri/clusterfork`.
3. Leave user authorization/callback and setup URLs empty. This app authenticates
   as an installation; it does not need a user OAuth flow.
4. Uncheck **Active** under **Webhook**. This app does not receive GitHub webhooks;
   Linear sends events directly to Azure.
5. Under **Repository permissions**, set **Actions**, **Contents**, and
   **Pull requests** to **Read and write**. **Metadata: Read-only** is automatic.
   Leave other permissions at No access. Approve expanded permissions for any
   existing installation. These grants have been verified on Clusterfork.
6. Select **Only on this account**, then create the app.
7. Copy its numeric **App ID** into `GITHUB_APP_ID` in `.env`.
8. Under **Private keys**, click **Generate a private key**. Move the downloaded
   PEM outside the repo, for example into `~/.config/clusterfork/automation/`, and
   restrict its permissions with `chmod 600 /absolute/path/to/private-key.pem`.
   Put that absolute path in `GITHUB_APP_PRIVATE_KEY_FILE`. Keep the PEM separate
   from `.env`; the helper reads its contents only when configuring Azure.
9. In the app's sidebar, choose **Install App**, install on `KianBahasadri`, choose
   **Only select repositories**, select **clusterfork**, and click Install.

For another repository later, extend this installation's selected repositories
and add its registry entry and caller workflow. One GitHub App and Azure deployment
can serve all those repositories. The dispatcher looks up the installation ID;
you do not need to copy that ID into `.env`. It requests a short-lived token
limited to the selected repository. Dispatcher tokens have Actions write;
worker publication tokens have Contents and Pull requests write.
Also enable repository auto-merge and branch protection with the relevant
required CI checks on its target branch, or explicitly set `auto_merge: false`.

References: [registration](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app),
[installation](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app),
[installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).

## Deploy after credentials are filled

First commit/review and land the pilot workflow on Clusterfork's default branch
(`main`). GitHub needs the workflow on that branch before it can accept dispatches.
The deploy helper does not publish workflow files to GitHub.

Check local configuration:

```bash
python3 automation/pilot.py check
```

This prints only set/blank status and routing metadata, never secret values. A
blank webhook secret is allowed for initial deployment. Required missing values
make the check and deployment fail before creating any Azure resources.

With Azure CLI authenticated to the intended student subscription:

```bash
az account show --query '{name:name,state:state}'
python3 automation/pilot.py deploy
```

The helper creates `rg-clusterfork-automation` in East US, a deterministic unique
StorageV2 account, the event queue, and a Python 3.12 Function App on **Flex
Consumption**, with 512 MB instances, no always-ready instances, and no Application
Insights resource. It configures credentials through a temporary mode-0600 JSON
file, not secret-bearing command arguments, and deploys the allowlisted source
package with remote dependency build. It never uploads the root `.env`, PEM file,
or `COMMAND_CODE_API_KEY` in that package. Runtime Linear/GitHub credentials are
sent separately to Azure app settings. Compute and storage still use Azure's
normal allowances/pricing; this is not a promise of zero cost.

Runtime dependencies pin `azure-functions==1.25.0`, which supports Python 3.12;
the 2.x SDK requires Python 3.13 or later. CI installs these dependencies and
indexes all five functions on Python 3.12, in addition to the repository tests.
Azure CLI responses may expose the hostname directly or inside `properties`;
the deploy helper accepts both shapes and rejects a missing hostname.

It prints the actual webhook and health URLs. Until the webhook secret is set,
health returns 503, webhook requests are rejected, and reconciliation stays idle.
The current pilot has both the webhook and GitHub App access configured and has
passed the live validation recorded above.

## Connect Linear and run the smoke test

1. In Linear's workspace settings, open **API → Webhooks** and create a webhook
   targeting the printed `https://…azurewebsites.net/api/linear` URL.
2. Select the **Kian** team and the **Issues** and **Projects** resource types.
   Workspace admin access is required. No webhook is needed per repository.
3. Copy its signing secret into `LINEAR_WEBHOOK_SECRET` in `.env`, then run:

   ```bash
   python3 automation/pilot.py configure
   ```

4. Check the printed `/api/health` URL returns 200 with `"missing": []`. Re-enable
   the Linear webhook if it was disabled during initial setup.
5. Add a **project label** named `symphony` to the **Clusterfork project**. This is
   different from an issue label. Enrollment can trigger smoke runs for existing
   Todo issues, so inspect that list before applying it.
6. Create a test issue in that project with status **Todo**, or move a selected
   issue into Todo. The mapping uses the existing Kian team's Todo UUID; it does
   not invent or create a Ready status.
7. Open [Clusterfork Actions](https://github.com/KianBahasadri/clusterfork/actions)
   and look for **Clusterfork Codex worker**. Its summary names the issue, pinned
   model/effort, outcome, and PR link when changes were published. The worker
   receives issue text from the authenticated broker, not dispatch inputs.
8. Edit that same issue again. The dispatcher receipt should suppress another
   smoke run. The shared timer also reconciles missed events every 15 minutes.

The HTTP function verifies the HMAC-SHA256 signature over the raw body, rejects
timestamps more than five minutes away, and responds 200 only after a queue send
succeeds. Only Issue/Project create/update events are used. The queue stores entity
IDs, not whole webhook bodies. The consumer reads **current** Linear data before
checking enrollment, mapping, archive flags, and ready status. Removing the project
label prevents subsequent dispatches; it does not cancel runs already submitted.

## Pilot limits and troubleshooting

Completed issues dispatch once per registry `generation`. Editing an issue or
moving it out of Todo and back does not reset completion. Capacity waits retry
through reconciliation; execution failures require inspection and a GitHub job
rerun. Existing open PRs are recovered by their deterministic branch name.
Changing generation permits **all** eligible issues to dispatch again.

The agent has a 30-minute execution timeout inside the 45-minute job. Configured
validation commands run after it finishes. Publication rechecks current project
enrollment and status, pushes a separate branch, opens a PR, and requests the
configured merge policy. GitHub waits for required checks before automatic merging.
Workflow-file edits are refused because the App has no Workflows permission.
Durable agent-session resume and automatic retries after a mid-task usage limit
are not implemented. Linear API access remains read-only.

Azure Table `ClusterforkDispatches` records `dispatching`, `dispatched`, or
`uncertain`. `dispatched` means GitHub accepted the request; verify actual runner
success in Actions. Atomic receipt creation prevents concurrent delivery duplicates.
Definite GitHub rejections release the receipt so queue retries can recover. A
timeout/server error may have occurred after GitHub accepted a request: that receipt
is held as `uncertain`, and a crash may leave `dispatching`. Check Actions before
manually removing such a receipt in Azure Storage Explorer. This prioritizes avoiding
duplicates over automatically retrying an ambiguous request. It does not claim
exactly-once execution. Inspect `clusterfork-events-poison` after repeated failures.

Common causes: missing App installation, incorrect numeric App ID/PEM, workflow
not yet on `main`, wrong webhook signing secret, missing project label, or an issue
outside the mapped project/Todo state. The registry and app installation must both
allow the destination repository.

## Local validation

```bash
python3 -m unittest discover -s tests -p 'test_linear_dispatch.py' -v
python3 automation/pilot.py package --output /tmp/clusterfork-pilot.zip
```

Routing tests use recorded-shape Linear objects and in-memory receipts; they make
no cloud writes and use no actual credentials. The deployment package has an
explicit file allowlist. Function decorators and SDK integration can be checked
with dependencies from `automation/azure/requirements.txt` in an isolated venv.

Validation for the routing milestone: 27 routing/setup tests and six quota-selection tests
pass; all dispatcher functions index
with the pinned Azure SDK, a signed request passes through the native HTTP handler
and queue consumer to the dispatch adapter, and queue failure returns 503. A
temporary RSA key verifies GitHub App JWT signing and repository-scoped token
requests. The full 278-test repository suite passes with `NO_COLOR` removed from
the subprocess environment (the notification color tests otherwise inherit it).
The worker adds 31 checks for workflow authorization, credential fencing, local
account ownership, live quota shapes, and real Git branch/PR publication against
a temporary bare repository, revoked-login cleanup, and broker error handling.
The full suite has 309 tests, including auto-merge policy and merged-PR recovery. Real RSA verification
also rejects forged signatures, wrong issuers/audiences, expired and future tokens.

Protocol references: [Linear webhooks](https://linear.app/developers/webhooks),
[GitHub dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event),
[Azure queue triggers](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-storage-queue-trigger),
[Flex Consumption](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan).
