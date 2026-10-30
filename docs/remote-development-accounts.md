# Remote account ownership

Saved Codex and Cursor CLI profiles come from
`~/.local/share/clusterfork-auth/{codex,cursor}/auth.json.*`. Enrollment includes
**all saved profiles by default, including the currently active account**, and
deduplicates aliases by provider account identity. Existing cloud credentials
are retained when enrollment runs again, so stale local copies cannot overwrite
refreshed cloud tokens.

The provider's local `remote-profiles.json` manifest records cloud ownership. Both
providers keep reserved profiles at `auth.json.<name>`, visible in their normal
`rotate-codex --list` or `rotate-cursor-cli --list` output with a reservation notice.
The manifest blocks explicit local selection, overwrite, deletion and named
kickoff; automatic rotation and default kickoff skip reserved profiles. Enrollment
preserves the current symlink and local credential files; it does not stop or
lease an already running local session. Remote leases coordinate cloud jobs only.
Simultaneous local and cloud OAuth refreshes are not synchronized and can still conflict;
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
Codex remains the default; add `--provider cursor` for Cursor enrollment, listing,
or reclaim. `list` reports reserved aliases and stable account IDs, without tokens.
Reclaim a profile before using it locally again:

```bash
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py reclaim --profile NAME
```

Reclaim refuses busy accounts or changed local credentials, persists the latest
cloud credentials locally, then removes cloud ownership. Codex and Cursor use
the same enrollment and reclaim lifecycle; Cursor only adds `--provider cursor`.
Neither provider requires moving files, staging credentials, a validation probe,
or a separate unblocking command for normal enrollment/reservation.

Reinstalling `install-clusterfork.sh` updates the installed rotator for both
providers and automatically restores legacy local handoffs such as Cursor
`21kb60`. Normal account commands and rotators perform the same recovery, including
when listing. Both old `local_backup` and `backup_path` metadata are understood.
Recovery copies a backup within the provider's store back to `auth.json.<name>`,
checks its recorded source SHA-256 when present, and normalizes the reservation to
the ordinary account ID and source hash. The backup may remain as an unused copy;
subsequent enrollment and reclaim use the normal saved file. Recovery refuses
changed credentials, replaced destinations and paths outside the store. It is
repeatable after interruption, leaves active links and cloud ownership intact,
and never contacts Azure, claims/deletes an account, or uploads the backup.
Existing cloud credentials always win over stale local copies during enrollment;
only normal reclaim writes the latest cloud login back to the local profile.

## Leases and refresh

Private Azure Table `ClusterforkAccounts` holds auth caches. Each provider account
has one 120-second lease with a random fencing token and ETag updates. Jobs renew
every 20 seconds and persist refreshed auth during execution and on exit. A lost
lease stops the agent. A crash between OAuth refresh and persistence can require
re-login if the refreshed token was never saved.

Codex keeps its existing `codex` partition and account references. Cursor uses
the `cursor` partition, `cursor-cli-auth-v1` login JSON (`accessToken` and
`refreshToken`), and SHA-256 of the access JWT's stable `sub` as its row key.
Broker references qualify Cursor IDs with `cursor:` to prevent identity collisions
between providers. Decoding the subject establishes identity, not authentication;
live provider authentication and profile/quota checks remain required. Normal
selection excludes staged records. Every refresh save checks the same provider
identity and the current fencing token. The pinned Cursor CLI has no unattended
saved-login OAuth renewal path: expired/revoked logins fail visibly; the worker
does not invent a refresh endpoint or replace the cloud login with a local copy.

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
403. An unbound agent job cannot list or claim accounts before coding. Preflight
alone binds the initial account and cannot replace an existing assignment.
Releasing an account lease, losing the lease, or repeating `start` preserves
the binding unless Codex coding enters the controlled failover below. A finished development
attempt cannot restart itself to clear it. A new run/attempt selects independently.
When rerunning a failed GitHub attempt, rerun **all jobs** so preflight executes
for the new attempt. Rerunning only the agent job cannot inherit the previous
attempt's assignment and stops before execution.

Binding gives the worker a 15-minute lease for the job handoff.
Accounts retain their normal 120-second leases and are not reserved while the
next VM is queued. If the assigned account becomes busy, depleted, or unavailable,
the agent job waits for quota and the existing 15-minute reconciliation retry;
it does not fail over before coding. Readiness starts its bounded review deadline on
the agent job's first start, without letting repeated starts extend that budget.

Deploy matching broker/worker sources, setup actions, and worker/preflight workflows to each mapped
repository. The start protocol includes `codex-first-cursor-grok-high-single-account-v3`; incompatible
workers/brokers stop before agent execution. The automatic deployment drain still
applies, and no new credentials, storage tables, or App grants are required.

Cursor gets a fresh private home containing only its assigned CLI login, with
private config/data paths and no inherited provider keys or job tokens. It never
receives Codex logins. Likewise, a Codex agent VM cannot probe or claim Cursor
credentials. Preflight exports only the existing selection boolean, and each new
run/attempt independently applies the provider policy in the
[runtime profile section](remote-development.md#agent-profiles).

### Coding account failover

Development uses persistent app-server threads, without `ephemeral: true`.
Only a failed coding turn explicitly classified as `usageLimitExceeded` permits
account rotation. Generic HTTP 429s, transient rate limits, authentication errors,
transport errors, interruptions, timeouts, and lease loss stop normally.

The worker stops the old app-server, retains its thread ID, and copies only
`sessions/**/rollout-*.jsonl` into a private task session directory outside every
temporary `CODEX_HOME`. It saves refreshed credentials with `release=True` and
deletes the old home, including `auth.json`, before requesting `exhaust_account`.
The broker requires that successful fenced release, the active coding stage,
and the original thread ID. It records `exhausted_account_ids` on the
`ClusterforkWorkers` row and unlocks replacement access for that develop attempt.
Neither a repeated start nor preflight can clear or use this failover authority.

Replacement probes are fenced one identity at a time, including an ambiguous
claim response. Each probe releases its lease and deletes its auth home before
the next; binding waits until the probe is released. Initial and replacement
selection share the [runtime quota policy](remote-development.md#agent-profiles).
The selected replacement is leased and rechecked before restoring the retained
rollout files into its fresh `CODEX_HOME/sessions/`. No account caches or
credentials accompany session transfer, and session symlinks are rejected.
The publication guard retains tokens from every probed or leased account.

Conversation resume remains within Codex. Replacement access cannot claim Cursor
or transfer conversation data across providers. If no Codex replacement qualifies,
the attempt reports quota unavailability; a later independently admitted attempt
can select Cursor on a fresh VM. A running Cursor session continues on its assigned
identity when Codex capacity returns.

The worker calls `thread/resume` with the same thread ID, then `turn/start` to
continue the original task with a new turn ID. The broker updates steering's
turn ID only within that same thread. The runner, checkout, and existing edits
stay in place; the original coding deadline also covers replacement selection
and resumed turns. Exhausted identities cannot be selected again in that attempt.
If no replacement qualifies, it finishes with explicit quota unavailability and
the normal capacity retry; it does not wait or rotate indefinitely. Conversation
rollouts remain local to the runner. Guarded
[WIP preservation](remote-development.md#retries-and-troubleshooting) can retain
unfinished repository changes on failure.

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
`account_id`, `id_token`, `accessToken`, and `refreshToken` seen in leased auth caches, including quota probes,
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

## KIAN-922 post-deployment validation

The one-off KIAN-922 cloud record for Cursor `21kb60` uses a staged cloud state
until its integration probe passes. This historical probe is independent of
ordinary reservations and local saved-profile visibility; local recovery does
not alter that cloud state. The resumed, health-verified deployment event
automatically asks the App to dispatch `clusterfork-cursor-smoke.yml` on main,
bound to that broker/worker revision and digest. A durable once-only receipt in `ClusterforkOperations`'s
`validation` partition precedes dispatch. Repeated events, timer ticks, deployments,
lost dispatch responses and manual reruns cannot authorize another agent turn.

This workflow runs on a fresh hosted VM. Its signed main-workflow OIDC claims,
revision, App actor, run ID/attempt and receipt are verified before binding it to
the reserved Cursor identity. Its broker role can claim only that account, save
its refreshed login, and report validation. It cannot list the pool, claim another
identity, obtain checkout/publication tokens, or admit repository work. Azure
storage credentials remain broker-side; the coding VM cannot assume this role.

The probe rechecks authentication, live usage, eligibility and the exact profile,
then requests permission for one turn with a 120-second deadline. It verifies a
harmless file creation and a completed read-tool result in a disposable workspace.
There is no turn retry, repository publication, or spending-setting change. A
successful fenced release preserves the latest tokens before activating the staged
record for normal selection. Reclaim can also restore the latest staged cloud login
before validation; no stale credential upload is needed.

One stable KIAN-922 comment records the credential-free pending/pass/fail result,
revision, provider/account/profile, model/effort, actual usage when available, and
run link after discovery. Missing credentials, unknown usage, unavailable profiles,
setup failures, ambiguous dispatch and missing finish callbacks remain visible
failures or pending discovery. Reporting failures retain durable retry intent.
Unit tests and CI use synthetic credentials; they are separate evidence from this
post-deployment live result. Production Codex capacity is never altered by the probe.
