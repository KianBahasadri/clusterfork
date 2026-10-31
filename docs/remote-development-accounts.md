# Remote account ownership

Enrollment reads `~/.local/share/clusterfork-auth/{codex,cursor}/auth.json.*`,
including the active profile by default, and deduplicates aliases by provider
identity. Existing cloud credentials win over stale local copies on re-enrollment.
`remote-profiles.json` records cloud ownership. Reserved saved files stay visible
in `rotate-codex --list`/`rotate-cursor-cli --list`, but cannot be selected,
overwritten, deleted or explicitly kicked off locally; automatic sweeps skip them.
Enrollment retains links/files and does not stop or lease an existing local
session. Cloud leases cannot synchronize local OAuth refreshes: reclaim before
further local use.

## Enroll and reclaim

Use the writable auth-store host and existing Azure CLI operator login. Transfers
use the authenticated controller, without storage keys or new login:

```bash
python3 -m venv ~/.config/clusterfork/automation/venv
~/.config/clusterfork/automation/venv/bin/pip install -r automation/requirements.txt
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py enroll
```

`--profile NAME` selects profiles, including active ones. Codex is default; add
`--provider cursor` to enroll/list/reclaim Cursor. `list` reports aliases and
stable IDs without tokens. Reclaim refuses busy accounts or changed local
credentials, saves the latest cloud login locally, then removes cloud ownership.
Normal enrollment requires no file moves, staging, probe or unblocking command.

Reclaim before local reuse:

```bash
~/.config/clusterfork/automation/venv/bin/python automation/accounts.py reclaim --profile NAME
```

Installer/rotator/account commands, including list, recover legacy local handoffs
such as Cursor `21kb60`. Recovery accepts `local_backup`/`backup_path`, copies only
within the provider store to `auth.json.<name>`, checks any source SHA-256 and
normalizes the reservation's ID/hash. It refuses changed credentials, replaced
destinations or outside paths. It is repeatable after interruption and preserves
active links/cloud ownership; it neither contacts Azure nor uploads the backup.
The unused backup may remain. Only reclaim replaces local credentials with cloud
refreshes. [Local rotation/container mounts](auth-rotation.md) have separate rules.

## Leases and refresh

Private SQLite `accounts` rows hold auth caches. Each account has one 120-second
lease with random fence; SQL atomically checks owner/fence/expiry. Jobs renew every
20 seconds and save refreshes during execution/exit. Lease loss stops the agent;
a crash before persisting a refreshed token can require login again.

Codex retains its `codex` identities. Cursor uses provider `cursor`, login format
`cursor-cli-auth-v1` (`accessToken`, `refreshToken`), and SHA-256 of access JWT `sub`
as stable ID, prefixed `cursor:` in broker references. Decoding establishes identity,
not authentication. Every refresh save checks provider identity and fence;
selection excludes staged records. The pinned Cursor CLI has no unattended saved-login
renewal path; expired/revoked logins fail without invented endpoints or local fallback.

## Worker credential boundary

The App private key stays in Azure. [Signed job roles](remote-development-controller.md#native-execution-graph)
authorize each phase. Review/coding select independently before target checkout.
Private probe homes are saved/released; atomic assignment reserves the chosen
lease and closes pool access. Heartbeats preserve it through checkout; execution
claims that same fence. Selection cannot execute target code, validate or publish.

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

Publication credentials are repository-scoped Contents/Pull requests write, plus
Workflows write when the installation grants it. The broker checks that grant at
start and before publication credentials. Checkout stays read-only; publication
tokens are reissued before publishing, kept in the trusted worker parent, never
workflow inputs. Auth caches/transcripts are not uploaded as artifacts.

### Publication credential checks

Before commit, scan staged changes; before push, scan full committed
`origin/<base>...HEAD`. Recovered branches/PRs pass the same guard before publish
or auto-merge. Binary changes are matched as text; external diff/textconv cannot
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
its disabled smoke workflow is outside deployment/admission. Migration preserved
staged/active metadata and refreshes without activating staged profiles or changing
local visibility. Reservations and restore/reclaim remain separate operations.
