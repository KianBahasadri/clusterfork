# Remote account ownership

An account is identified by `(provider, account_id)`, where `account_id` is a
64-character lowercase SHA-256: Codex hashes `tokens.account_id`; Cursor hashes
the access JWT's `sub`. Broker references prefix Cursor IDs with `cursor:`;
the consumer contract below always uses the separate provider and unprefixed ID.
Aliases are display labels, never quota identities. Each account has one cloud
credential record and at most one cloud job lease, regardless of alias or login
count. Independent local and cloud logins share the underlying provider quota.

`remote-profiles.json` in `~/.local/share/clusterfork-auth/{codex,cursor}/` records
local/remote scope. Remote entries retain the internal value `ownership: cloud`;
registered local sessions use `ownership: local` with a persistent `session_id`.
Controller metadata separately retains the cloud session ID, known token/session
fingerprints and registered local-session fingerprints. Local credential contents
stay on their host; registration transmits hashes, never the local auth cache.

## Scoped rotator management

Use the [scoped rotate commands](auth-rotation.md#local-and-remote-account-commands)
for ordinary account management. Sign in with the provider, then save the current
login in the chosen local or remote set. Remote save checks for reuse of local access/refresh
tokens and provider session claims before uploading anything. When the same
account is already local, it preserves the saved local files, selection and
session IDs, registering only their fingerprints with the controller. A new
remote login cannot replace an already enrolled login, including refreshed cloud
tokens. Delete that remote account explicitly before saving a replacement.

Every profile belongs to the local or remote set. Remote entries use manifest key
`remote:NAME` and `display_name: NAME`; local credentials use `auth.json.NAME`.
The colon is outside the valid local-name alphabet, so neither set occupies names
in the other. Legacy entries, including the earlier `remote.NAME` keys, normalize
into this namespace during rotate operations and installer repair. A remote name
can immediately be reused for a fresh local login. Consumer aliases use display
names and remain sorted/unique.

`--migrate-from-local-to-remote` groups saved aliases by provider identity and
known token/session fingerprints. With one login, it transfers that login to the
remote set, detaches its local selection and removes the local profile files.
Duplicate aliases and refreshes do not create independent logins. An already
enrolled remote login is kept, including refreshed tokens and its active/staged
state. Registered independent local logins remain local; ambiguous unregistered
copies fail without discarding them. This is an operator migration, not the
default for future saves: an explicit local save stays local.

That transfer journals source hashes before archiving old local copies. Retrying
after interruption never overwrites the controller's current credentials. After
verifying remote enrollment, it deletes matching legacy handoff archives and
backups and removes their metadata. It requires no provider sign-in and never
copies an archive back to local.

`--migrate-from-remote-to-local` transfers remote-only accounts recorded on this
host back into local profiles. Each account is reclaimed under the same exclusive
[reclaim](#enroll-and-reclaim) lease, writing the current cloud credentials,
including refreshes, to local files named by the remote display names, then
removing the remote account. Accounts with an independent local login stay
remote. An unregistered local copy of the same account fails without removing
the remote login. Accounts enrolled only on other hosts stay in the pool.
Restored profiles are not selected. Interrupted reclaims retry through the
existing journal; a pending remote deletion must finish first. No provider
sign-in is required.

The interactive menu moves only the checked profiles. Aliases of one remote
account are one choice and move together. The explicit direction flags move
every eligible profile. Moving a local alias also detaches selection links to
unchecked copies of the same login, preventing further local refreshes of the
transferred session. An unsaved regular active login of a transferred account
must be saved before migration; its credentials are never discarded.

Pending remote transfers live under the private sibling directory
`.<store-directory-name>-logins/{local,remote}/NAME/`, outside profile enumeration.
Remote save moves the detached regular auth file into this journal before upload,
reattaching the previous independent local profile when available. It refuses to
upload a selected local profile or a login that reuses local tokens/session claims.
Successful save removes the pending home; failure retains it so repeating the
same save can finish without another sign-in. Scoped `--delete NAME` can discard
an unfinished transfer when no saved remote account exists. Legacy pending local
logins can also be discarded. No provider login happens during rotation or installation.

Remote deletion confirms once per invocation and removes the entire provider
account from the pool, including its remote aliases. It claims the existing
exclusive transfer lease, refuses a busy account, and journals the transfer ID so
an interrupted deletion can retry under the same owner. It discards the remote
login instead of reclaiming it locally, clears its host metadata/legacy archives and
marks independent local registrations `cloud_enrolled: false`. Saved local files
and selection remain intact. Remote listing uses only the controller's cached
snapshot and includes accounts added on other hosts.

<a id="enroll-and-reclaim"></a>

## Enroll, migrate and choose local availability

Use the writable auth-store host and existing Azure CLI operator login. Transfers
use the authenticated controller, without storage keys or a new Azure login:

```bash
python3 -m venv ~/.config/clusterfork/automation/venv
~/.config/clusterfork/automation/venv/bin/pip install -r automation/requirements.txt
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py enroll
```

Codex is the default; add `--provider cursor` for every Cursor command. Enrollment
includes the active saved profile by default; repeat `--profile NAME` to select
profiles. It deduplicates provider identities, preserves existing cloud tokens
(including refreshes), transfers other saved aliases of that login and leaves
registered independent local logins intact. Re-enrollment never uploads a
registered local session as the cloud login. `list` reports IDs, aliases and
ownership without tokens.

Stop clients using the old shared login before enrollment or migration. Existing
processes may retain tokens in memory; a cloud lease cannot fence their refreshes.
Mutating account commands, rotator commands and installer repair migrate legacy
handoffs locally without Azure access. For explicit legacy handoff recovery:

```bash
python3 automation/accounts.py migrate --provider codex
python3 automation/accounts.py migrate --provider cursor
```

Legacy handoff records identify remote logins, even when token bytes differ.
Recovery temporarily archives their old `auth.json.NAME` copies under the historical
`cloud-reserved/NAME.json` path, outside local enumeration. These are recovery
copies, not local profiles. The scoped migration verifies enrollment and removes
them. Remote entries appear as `[remote]` in listings; local selection and kickoff
use only local credentials. Unregistered copies of remote credentials cannot be
used as local logins.

Legacy `local_backup`/`backup_path` handoffs (including Cursor `21kb60`) are
recovered only within the provider store, checked against their recorded SHA-256,
then archived. Changed files, symlinks, replaced archives and outside paths fail
closed. Interrupted/repeated migration is safe, leaves cloud tokens untouched and
requires no provider sign-in. Backup/archive copies are never uploaded on
re-enrollment. For accounts that cannot sign in again, keep the default or record
the choice explicitly, without any new provider login:

```bash
python3 automation/accounts.py cloud-only --provider codex --profile NAME
```

To add local availability, unhook the provider, perform an actual fresh provider
sign-in, then save its regular auth file. The rotate command registers the separate
session automatically; the direct utility below remains available for maintenance:

```bash
rotate-codex --unhook
# Operator step: codex login, signing into the SAME provider account anew.
python3 automation/accounts.py register-local --provider codex --profile NAME \
  --local-profile NAME-local --auth-file ~/.codex/auth.json --fresh-login
rotate-codex --save NAME-local
```

For Cursor use `rotate-cursor-cli --unhook`, the normal interactive Cursor CLI
login, `--provider cursor` and `--auth-file ~/.config/cursor/auth.json`, followed
by `rotate-cursor-cli --save NAME-local`. Registration itself does not sign in or
select a profile. It accepts only a regular source outside the saved store, checks
account identity, requires an idle cloud account, and refuses occupied destinations.
An interrupted registration can be retried with the same source and destination;
it cannot replace a subsequently refreshed or different local login.

The direct utility's `--fresh-login` attests that the operator completed a new sign-in. Copying an auth
file or refreshing the existing chain does not satisfy it. Known access/refresh
token history and provider `sid`/`session_id` claims reject reuse, including known
rotated copies. Providers do not always expose a stable session claim: differing
token bytes alone cannot prove independence, so this attestation is required.
Local rotation, save and kickoff can then use the registered session; saving
another alias preserves its session ID. Saving another account or a cloud login
over it fails. Replacing it with another fresh sign-in requires a new profile
name. To return an account to cloud-only, unhook/switch away, delete its detached
local profiles with the rotator, then run `cloud-only`.

## Reclaim

Reclaim transfers the latest cloud login out of the pool under an exclusive lease;
it refuses busy accounts, changed local files and occupied destinations:

```bash
python3 automation/accounts.py reclaim --provider codex --profile NAME
```

With no independent local login, this transfers the remote aliases into local. If an
independent local login exists, an explicit **separate unused** destination is
mandatory; reclaim never replaces that login:

```bash
python3 automation/accounts.py reclaim --provider codex --profile NAME \
  --destination-profile NAME-reclaimed
```

A transfer journal protects destinations until fenced cloud deletion
finishes. Retrying a completed/interrupted transfer validates the written bytes
and preserves later local changes. Cloud ownership is removed only by this
explicit transfer; registration and cloud-only choices retain it. Reclaim removes
the old transfer archives after success; unused legacy backups may remain.
Existing local-session registrations stay host-local with `cloud_enrolled: false`.
Re-enrolling the reclaimed cloud profile preserves and re-registers those local
sessions, including their refreshed credentials, without another sign-in.

## Read-only consumer contract (schema version 1)

Conky should use these interfaces instead of enumerating/refreshing `auth.json.*`:

```bash
# Host-local availability only; offline, never mutates/migrates files.
python3 automation/accounts.py availability --provider codex
# Host availability joined to controller usage using the existing Azure operator login.
python3 automation/accounts.py snapshot --provider codex
# Repeat either command with --provider cursor.
```

Both accept `--store PATH`. `availability` returns `schema_version: 1`,
`generated_at` (host Unix seconds), `provider`, `local_scope: "host"` and
`accounts`. Its account fields are `provider`, `account_id`, `aliases` (sorted,
unique display names), `cloud_available` (the host records cloud enrollment),
`local_available`, `local_profiles` (sorted eligible local aliases) and
`selected_local_profile` (eligible store `current` alias, otherwise `null`).
Availability means an independent configured login exists on this host; it does
not require selection or perform a live authentication check. A cloud-only
selection marker never makes local availability true. Ordinary unenrolled local
profiles are also included, with `cloud_available: false`.

`snapshot` adds `cloud_error` (`null`, `operator_authentication_unavailable` or
`controller_unavailable`), merges aliases by `(provider, account_id)`, and adds
`cloud_state` (`active`, `staged`, `unknown`, or `null` for unenrolled local
accounts) and `usage` to each account. Accounts known only to the controller
have `local_available: false`, `local_profiles: []` and
`selected_local_profile: null`. Failure to fetch cloud data produces explicit
unknown usage while retaining the host availability result. Invalid local
ownership data exits nonzero: consumers must treat that as unknown availability
and show no local-availability icon.

The underlying read-only HTTPS endpoint is:

```text
GET https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/accounts/snapshot?provider=codex
Authorization: Bearer <existing Azure management access token>
```

Use `az account get-access-token --resource https://management.azure.com/ --query
accessToken --output tsv --only-show-errors` through the existing authorized
operator identity. Cursor uses `provider=cursor`. Missing/unauthorized operator
access returns 401/403; unsupported, missing or repeated provider query parameters
return 400. Responses are JSON with `Cache-Control: no-store`. This endpoint
returns only the controller's `schema_version`, `generated_at`, `provider` and
account fields `provider`, `account_id`, `aliases`, `cloud_available: true`,
`cloud_state` and `usage`; overlay the offline availability result for local
fields. GET never claims a lease, calls a provider, loads an auth cache or wakes
quota collection. Consumers must never use the credential-transfer POST operations.

A joined account's usage object is exactly:

```json
{
  "observed_at": 1800000000,
  "attempted_at": 1800000060,
  "age_seconds": 60,
  "max_age_seconds": 300,
  "freshness": "fresh",
  "error": "busy",
  "windows": [
    {"name": "codex/primary", "used_percent": 23,
     "resets_at": 1800010000, "duration_minutes": 300}
  ]
}
```

All timestamps are UTC Unix **seconds**, including Cursor billing-cycle resets;
percentages are measured used capacity, not remaining capacity. Window fields
are always `name`, `used_percent`, `resets_at` and `duration_minutes`; the last
two may be `null`. Codex names are `codex/primary`, `codex/secondary` or a
`gpt-…/primary|secondary` model limit; Cursor names are `cursor/auto`, `cursor/api` and optionally
`cursor/included`. These describe the configured cloud model's applicable quota,
shared with the independent local session; never sum local/cloud or alias rows.

`observed_at` is the last successful provider measurement; `attempted_at` is the
last collection attempt. Either may be `null`; `age_seconds` is `null` without a
measurement. `freshness` is `fresh` only within 300 seconds with no elapsed reset,
`stale` for an old/reset/future-dated measurement, or `unknown` with no usable
measurement (`windows: []`). `error` is `null`, `busy`, `inspection_failed` or
`lease_lost`; CLI cloud-fetch failure can additionally use its `cloud_error`
value. A failed/busy attempt retains the last measurement and its original time;
a still-fresh cached measurement may coexist with `error: busy`. Recompute age
and reset validity as time passes; an old cached `fresh` string never extends
freshness. Unknown/stale data is never zero usage or extra capacity.

The controller's background collector attempts each account at most once per
60-second cycle, skips collection during controller holds, and uses the same
120-second account/fence boundary as jobs. Busy accounts are never probed or
refreshed; Conky polling cannot initiate a probe. Codex inspection uses the pinned
app-server in a private temporary home (bounded 15-second RPCs); Cursor uses the
existing dashboard RPC without an invented refresh endpoint. The collector saves
any refreshed cloud auth under its original fence before publishing a reading,
including after inspection failure; lease loss discards the reading and cannot
write over another owner's tokens. Errors are fixed codes, never provider text.
The consumer response contains no credentials, token/session fingerprints, caches
or lease authority. Independent local credentials are never collected centrally.

## Leases and refresh

Private SQLite `accounts` rows hold auth caches. Each account has one 120-second
lease with random fence; SQL atomically checks owner/fence/expiry. Jobs renew every
20 seconds and save refreshes during execution/exit. Lease loss stops the agent;
a crash before persisting a refreshed token can require login again.
Live quota-probe leases are exclusive too: workers, reclaim and deletion refuse
them until the probe saves its refreshed credentials and releases the lease.
Taking over a probe could otherwise transfer stale tokens and discard their
replacement. Retrying a transfer after a busy response reads the latest login.

Codex retains its `codex` identities. Cursor uses provider `cursor`, login format
`cursor-cli-auth-v1` (`accessToken`, `refreshToken`), and SHA-256 of access JWT `sub`
as stable ID, prefixed `cursor:` in broker references. Decoding establishes identity,
not authentication. Every refresh save checks provider identity and fence;
selection excludes staged records. The pinned Cursor CLI has no unattended saved-login
renewal path; expired/revoked logins fail without invented endpoints or local fallback.

## Worker credential boundary

The App private key stays in Azure. [Signed job roles](remote-development-controller.md#controller-owned-execution)
authorize each phase. Review/coding select independently before target checkout.
Private probe homes are saved/released; atomic assignment reserves the chosen
lease and closes pool access. Background collection pauses while tasks are active;
workers respect any probe already holding an account lease. Heartbeats preserve
the lease through checkout; execution
claims that same fence. Selection cannot execute target code, validate or publish.

Quota inspection is a snapshot, not a reservation. If another worker wins the
selected account before binding, selection tries its remaining eligible identities,
then rechecks once for provider fallback while excluding the raced accounts.
Exhausting current capacity records a normal quota wait rather than failing setup.
Binding acknowledgements are idempotent before agent start, including after a
lost response; transport retries cannot switch the committed identity or create a
second lease. Other authorization failures still reject execution.

After binding, listing returns only that account and unrelated claims/saves fail.
Agent start rechecks live quota. Release/repeated start cannot reset the binding;
manual/partial reruns cannot inherit it. Saves/releases retain original task/run/
phase authority after edits or mapping revocation so cleanup remains possible.
The [review deadline](remote-development.md#worker-execution) cannot be extended
by repeated starts. Runtime/target isolation follows the native graph.

Provider homes contain only assigned auth, private config/data and no inherited
keys/job tokens. A Codex phase cannot probe Cursor credentials or vice versa.
Each attempt independently applies [provider/quota policy](remote-development.md#agent-profiles).

### Coding account failover

Codex development uses persistent app-server threads, without `ephemeral: true`.
Only a coding-turn `usageLimitExceeded` permits rotation. Generic 429s, transient
limits, auth/transport errors, interruptions, timeouts and lease loss stop normally.

Stop the old app-server, retain its thread ID and copy only
`sessions/**/rollout-*.jsonl` to a private runner directory outside temporary
`CODEX_HOME`s. Save auth with `release=True`, delete the old home/auth, then request
`exhaust_account`. The broker requires verified fenced release, active coding and
the original thread. It records `exhausted_account_ids` and permits replacement;
start/preflight cannot create or consume that authority.

Probe replacements one fenced identity at a time, including ambiguous claims;
release/delete each home before the next probe or binding. Lease/recheck the
selection before restoring rollouts into fresh `CODEX_HOME/sessions/`; reject
symlinks and transfer no caches/credentials. Publication guards retain all observed
tokens. `thread/resume` keeps the thread; `turn/start` creates a new turn and
steering may update only its turn ID. Runner, checkout, edits and original deadline
remain. Exhausted identities cannot recur in that attempt.

No qualified replacement yields explicit quota unavailability and normal capacity
retry, without indefinite waiting. Resume stays within Codex; a later independently
admitted attempt may choose Cursor on a fresh VM. Rollouts remain runner-local;
guarded [WIP preservation](remote-development.md#retries-and-validation) can retain
unfinished repository edits. Running Cursor sessions do not switch providers.

Worker push credentials are repository-scoped Contents write, plus Workflows write
when the installation grants it. Only an active controller publication or WIP
command authorizes them; they never include Pull requests write. The broker checks
workflow grants at start and before push credentials. Checkout stays read-only;
push tokens stay in the trusted worker parent, never workflow inputs. The
controller separately obtains PR creation and exact-head merge credentials, after
verified worker stop. Auth caches/transcripts are not uploaded as artifacts.

### Publication credential checks

Before commit, scan staged changes; before push, scan full committed
`origin/<base>...HEAD`. Recovered branches/PRs pass the same guard before publish
or controller publication. Binary changes are matched as text; external diff/textconv cannot
hide content.

The in-memory guard retains nonempty `access_token`, `refresh_token`, `account_id`,
`id_token`, `accessToken`, `refreshToken` from every probe/lease/save/refresh, plus
job GitHub/OIDC, checkout, publication and refreshed broker tokens. It rereads live
caches before scans and retains values after deleting homes. Exact values block
in every file, including `tests/**`, and PR metadata. Agents receive only assigned
auth; credentials stay within their phase.

Static PEM/token-prefix/JWT patterns from `common/policy.py` apply to added lines
outside `tests/` and PR metadata; synthetic test fixtures are exempt only from
static patterns. Generic assignments, auth headers and URL user/password forms
do not independently block. Scan new/existing PR title/body, full untruncated agent
summary and issue title. Matches or unreadable auth abort before push/creation
with bounded reasons excluding values, contents and credential-bearing paths.

## KIAN-922 post-deployment validation

The [once-only Cursor probe](archive/cursor-validation-20261030.md) is historical;
its disabled smoke workflow is outside deployment/admission. That migration preserved
staged/active metadata and refreshes without activating staged profiles or changing
local visibility. Scope changes and restore/reclaim remain separate operations.
