# Remote account ownership

Saved Codex profiles come from
`~/.local/share/clusterfork-auth/codex/auth.json.*`. Enrollment includes
**all saved profiles by default, including the currently active account**, and
deduplicates aliases by provider account identity. Existing cloud credentials
are retained when enrollment runs again, so stale local copies cannot overwrite
refreshed cloud tokens.

The local `remote-profiles.json` manifest records cloud ownership. The Codex
rotator skips those profiles for new local selections. Enrollment preserves the
current symlink and local credential files; it does not stop or lease an already
running local session. Remote leases coordinate cloud jobs only. Simultaneous
local and cloud OAuth refreshes are not synchronized and can still conflict;
reclaim a profile before starting further local use of its credentials.

## Enroll and reclaim

Run these commands from the checkout on the writable auth-store host, with
Azure CLI authenticated to the configured subscription. Use an isolated Python
environment with the Azure dependencies:

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

## Leases and refresh

Private Azure Table `ClusterforkAccounts` holds auth caches. Each provider account
has one 120-second lease with a random fencing token and ETag updates. Jobs renew
every 20 seconds and persist refreshed auth during execution and on exit. A lost
lease stops the agent. A crash between OAuth refresh and persistence can require
re-login if the refreshed token was never saved.

## Worker credential boundary

The broker keeps its App private key in Azure and does not send it to the runner.
It verifies GitHub's OIDC signature, issuer, audience, expiry, repository,
main-branch workflow path, hosted-runner type,
run ID/attempt, and a durable dispatch receipt. It also checks the run through
GitHub's API and requires dispatch by `clusterfork[bot]`. PR workflows, forks,
other workflows, and unmapped repositories cannot obtain account credentials.

Both readiness and development run quota selection in
`.github/workflows/clusterfork-select-account.yml`, a reusable preflight job on a
separate ephemeral VM from the agent job. Only this job can inspect the enrolled
pool before assignment. Its role comes from GitHub's signed `job_workflow_ref`
claim, with `job_workflow_sha` matching the dispatched revision; a request field
or a changed script on the agent VM cannot grant that role. The broker still
requires the mapped main-branch caller workflow and bot-dispatched run.
See [GitHub's reusable-workflow OIDC claims](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows).

Preflight runs account/model/quota RPCs without starting an agent turn, executing
repository validation, or obtaining checkout/publication tokens from the broker.
It persists refreshed credentials and releases each probe lease, then binds one
account to the dispatch's run ID/attempt with an ETag update. Temporary auth homes
are removed on exit. The only workflow output is a `selected` boolean; credentials
and quota-probe files never cross the job boundary.

The agent job requires that binding before execution and rechecks only its
assigned account's live quota. For either job, `accounts` returns only the assigned
identity after binding, and `claim`/`save` requests for other identities fail with
403. An unbound agent job cannot list or claim accounts. Only preflight can bind,
and it cannot replace an existing assignment. Releasing an account lease, losing
the lease, or repeating `start` preserves the binding; a finished development
attempt cannot restart itself to clear it. A new run/attempt selects independently.
When rerunning a failed GitHub attempt, rerun **all jobs** so preflight executes
for the new attempt. Rerunning only the agent job cannot inherit the previous
attempt's assignment and stops before execution.

Binding gives the worker a 15-minute lease for the job handoff.
Accounts retain their normal 120-second leases and are not reserved while the
next VM is queued. If the assigned account becomes busy, depleted, or unavailable,
the agent job waits for quota and the existing 15-minute reconciliation retry;
it never tries another identity. Readiness starts its bounded review deadline on
the agent job's first start, without letting repeated starts extend that budget.

Deploy matching broker/worker sources and both workflow files to each mapped
repository. The start protocol includes `single-account-preflight-v1`; incompatible
workers/brokers stop before agent execution. The automatic deployment drain still
applies, and no new credentials, storage tables, or App grants are required.

Publication tokens are scoped to one repository with Contents and Pull requests
write permission, plus Workflows write when the installation currently grants it.
The broker checks that grant at worker start and again before issuing publication
credentials; checkout tokens stay read-only. Tokens are issued again before
publication and never passed in workflow inputs. Account caches and agent
transcripts are not uploaded as artifacts.

### Publication credential checks

Before committing staged work and immediately before pushing, the worker scans
the staged diff and the full committed `origin/<base>...HEAD` diff, respectively.
Recovered branches and existing PRs pass the same checks before publication or
an auto-merge request. Binary changes are read as text for matching; external
diff helpers and text conversions cannot hide changed content.

An in-memory guard retains every non-empty string `access_token`, `refresh_token`,
`account_id`, and `id_token` seen in leased auth caches, including quota probes,
lease saves, and refreshes. It rereads live caches before each scan and retains
values after probe homes are deleted. The guard also retains job GitHub/OIDC
credentials and every checkout, publication, and refreshed broker OIDC token.
Credentials stay within their job; preflight still never transfers pool auth to
the agent VM. Exact runtime values block in every file, including `tests/**`,
and in PR metadata.

Static checks share only the PEM private-key and concrete token-prefix/JWT
patterns from `readiness_policy.py`. They inspect added lines outside `tests/`
and PR metadata. Synthetic fixtures under `tests/**` are exempt from static
patterns; generic assignments, authorization headers, and URL user/password
forms do not independently block publication. Checks include the PR title,
generated body, full untruncated agent summary and issue title, and the current
title/body of any existing PR. A match or unreadable auth cache aborts before
push or PR creation with a bounded diagnostic reason, without matched values,
file contents, or credential-bearing paths.

The storage resource is currently named in `automation/accounts.py`; see
[remote setup](remote-development-setup.md#azure-deployment) before relocating it.
Local rotation commands and container mounts are covered by
[Auth Rotation](auth-rotation.md).
