# Auth Rotation

The six `rotate-*` functions call `scripts/rotate_auth.py`, using copy (Claude),
shared-store symlink (Codex/Cursor/Grok/OpenCode), or GNOME Keyring (Antigravity).

| Command | Behavior |
| --- | --- |
| `rotate-* --save NAME` | Save/overwrite a named profile |
| `rotate-* --delete NAME` | Confirm deletion of a detached saved profile |
| `rotate-* --unhook` | Detach credentials before a new login |
| `rotate-* --list` | List profiles; `*` marks current |
| `rotate-*` | Select the account with the lowest eligible quota usage |
| `rotate-* NAME` | Select a specific profile |
| `rotate-* --kickoff [names]` | Ping all saved profiles, or the named subset |

Names allow letters, numbers, dots, underscores and dashes. Deletion prompts
`delete saved profile 'NAME'? [y/N]`; only case-insensitive `y`/`yes` confirms.
Other input, EOF or Ctrl+C cancels with exit 1. Active profiles are refused before
prompting; switch or unhook first, including when deleting the last profile.
Invalid/unknown names change nothing. Deletion removes the saved file/keyring
entry and any matching detached `current` marker, preserving unrelated logins.

To add an account, unhook, log in, then `--save NAME`. Shared stores refuse
unhooking a regular `auth.json`; Claude requires a token-matching saved login;
Antigravity requires a saved current marker. Save first when necessary. Selecting
a saved name reattaches it if login is abandoned. Avoid reinstalling while
unhooked: installer repair can reconnect the previous profile.

## Automatic account selection

All six bare commands use Symphony's shared `automation/common/selection.py`
selector within their own provider's saved profiles. They read live quota before
switching, using the policy and Codex/Cursor profiles from
`automation/controller/projects.json`: currently at least 20% remaining in every
applicable window and measurements no older than 60 seconds. Codex ranks by
weekly usage when an identified short window is strictly below 70% used; at 70%
it includes that window in the most-depleted-window score. Other providers rank
by their most-depleted reported window. The current account participates and can
remain selected when it is best; one saved profile is sufficient. Aliases with
the same known identity share a quota probe, preferring the current login and
trying another saved login if its credentials are rejected.

Bare `rotate-codex` excludes accounts already used by live Codex panes in the
current tmux server, including detached sessions, before ranking quota. It reads
the `codex - NAME` launch labels created by `cc`; aliases of an occupied account
are excluded together. Dead panes and panes no longer running Codex do not count.
If every eligible account is occupied, rotation fails without changing selection.
Explicit profile selection still works. Detection uses existing tmux labels, so
unlabeled/direct sessions and other tmux servers or machines are outside its scope;
when tmux is unavailable, selection uses quota alone. It does not reserve accounts
against simultaneous launches.

| Command | Quota source |
| --- | --- |
| `rotate-claude` | OAuth usage, including reported model-specific weekly limits |
| `rotate-codex` | ChatGPT Codex usage and applicable model limits |
| `rotate-cursor-cli` | Dashboard included quota for Symphony's Cursor model |
| `rotate-grok` | Grok CLI billing credits |
| `rotate-opencode` | OpenCode Go's per-key rolling, weekly and monthly usage |
| `rotate-antigravity` | Code Assist quota groups, falling back to raw model buckets |

Quota requests read each saved login directly without temporarily activating it,
refreshing credentials or sending model prompts. When an account usage cache
(under `CONKY_CACHE_DIR`, `CLUSTERFORK_USAGE_CACHE_DIR`, or `~/conky-linear-HUP/cache`)
is present, fresh (within 300 seconds), and covers the eligible logins, automatic
selection reads the cached quota snapshots directly for sub-second speed; otherwise
independent accounts are probed concurrently via live HTTP with 15-second timeouts.
Codex/Cursor consider only eligible local logins; automatic selection never installs
remote credentials or reserves a cloud account. OpenCode profiles need an
`opencode-go` API key; other provider entries alone do not establish Go capacity.

Automatic selection excludes the personal profile `kian` and any alias sharing its
account identity (`ROTATE_EXCLUDE_PROFILES`), reserving it for explicit offline
choice. Unknown, rejected, stale, reset or depleted readings are ineligible. If no account
qualifies, the command exits 1 and retains the current credentials/selection.
Explicit `rotate-* NAME` remains an offline choice and can select any saved profile
without a quota check. Expired credentials must be renewed by their provider CLI.

## --kickoff

Kickoff installs and pings profiles sequentially because they share one active
credential slot; concurrent clients can read the wrong login or overwrite refreshes.
Each sends `hi` with detached stdin and a 120-second timeout:

| Provider | Ping |
| --- | --- |
| Claude | `claude -p` |
| Codex | `codex exec --skip-git-repo-check -m gpt-6-luna` |
| Cursor | `cursor-agent -p` |
| Grok | `grok -p` |
| OpenCode | `opencode run` |
| Antigravity | `agy --print` |

Only Codex pins a small model; others use configured defaults. Unknown/duplicate
names and dashed arguments fail before pings. Ping failures do not stop the sweep
but make its final exit code 1. Shared stores refuse regular `auth.json` files;
Antigravity refuses an active item without a recoverable current profile.

Normal completion restores Claude's previous file bytes, shared stores' previous
`current` link (removing a temporary agent link if initially unhooked), and
Antigravity's current saved profile or fully unhooked state. Limits: Claude with
no previous file and shared stores without `current` remain on the last profile
(the latter warns). Antigravity cannot restore a current profile omitted from an
explicit subset; use a full sweep when restoration matters. Interrupted sweeps
have no general cleanup guarantee.

## Implementation and directory overrides

Shell modules resolve the shared implementation from `BASH_SOURCE`; installation
migration is separate from selection. Empty/unset overrides use defaults:
`ROTATE_CLAUDE_DIR`, `ROTATE_CODEX_CODEX_DIR`, `ROTATE_CODEX_AUTH_STORE_DIR`,
`ROTATE_CURSOR_DIR`, `ROTATE_CURSOR_AUTH_STORE_DIR`, `ROTATE_GROK_DIR`,
`ROTATE_GROK_AUTH_STORE_DIR`, `ROTATE_OPENCODE_DIR`,
`ROTATE_OPENCODE_AUTH_STORE_DIR`, `ROTATE_ANTIGRAVITY_STATE_DIR`.
Shared-store save chmods the store itself to `700`, never its parent.

## rotate-claude

Profiles are `~/.claude/.credentials.json.NAME`. Save copies the active file;
selection copies it back because Claude rewrites credentials on refresh. Active
identity matches `accessToken`. Unhook deletes a matching saved regular-file
login or detaches a legacy symlink.

## Shared-store agents

| Command | Agent auth file | Store under `~/.local/share/clusterfork-auth/` |
| --- | --- | --- |
| `rotate-codex` | `~/.codex/auth.json` | `codex/` |
| `rotate-cursor-cli` | `~/.config/cursor/auth.json` | `cursor/` |
| `rotate-grok` | `~/.grok/auth.json` | `grok/` |
| `rotate-opencode` | `~/.local/share/opencode/auth.json` | `opencode/` |

Each agent file links relatively to its store's `current`, which links to
`auth.json.NAME`. Rotation changes only `current`. Save copies the active login,
selects it and rebuilds the chain even after login replaced the agent link with
a regular file. Unhook removes only the agent-side symlink, retaining store and
`current`. [Remote accounts](remote-development-accounts.md) owns remote scope
and reclaim rules for Codex/Cursor.

## Local and remote account commands

Codex and Cursor accept `--local` and `--remote` in any argument position. Omitted
scope means local for selection, deletion and kickoff. `--save` prompts interactively
to choose between local and remote scope using keyboard navigation (Up/Down arrow
keys and Enter); in non-interactive shells, `--save` requires an explicit `--local`
or `--remote` flag and otherwise exits with the command usage. `--list` without a
scope shows the host's saved profiles in both sets, labeling local/remote entries
when remote profiles exist; names present in both sets show `[local] [remote]`.

```bash
rotate-codex --local --save work
rotate-codex --remote --save work
rotate-codex --local --list
rotate-codex --remote --list
rotate-codex --local --unhook
rotate-codex --local --delete work
rotate-codex --remote --delete work
```

Replace `rotate-codex` with `rotate-cursor-cli` for Cursor. Sign in with the provider,
then use `--save NAME` to save that login in the selected set. For another login,
use `--unhook`, sign in again, then `--save NAME` with the desired scope.
Local save selects the saved profile. Remote save transfers the current login and
reattaches the previous local profile when one exists. These commands never start
a provider sign-in.
Remote jobs select their own account, so `--remote` accepts only
`--save`, `--list`, `--delete` and `--migrate`. It never installs remote credentials locally for
selection or kickoff. Remote operations use the existing Azure CLI operator login;
the installed helpers need only Python's standard library.

Local save registers a separate provider login automatically when the same account
also has remote credentials. No additional registration flag is required. The
[account ownership topic](remote-development-accounts.md#scoped-rotator-management)
owns independence checks, names, interrupted additions and remote deletion.
`rotate-codex --remote --migrate` (or the Cursor equivalent) transfers existing
single-login profiles into the remote set without another provider sign-in.

## Sharing with a container

Bind-mount `~/.local/share/clusterfork-auth` at the same home-relative path.
Directory mounts expose `current` changes immediately; mounting an individual
`auth.json` pins its resolved target. A read-only mount supports host-owned
rotation/refresh; container refresh cannot persist and may fail after token expiry.

## rotate-antigravity

Requires `secret-tool` (GNOME Keyring); state is
`~/.gemini/antigravity-cli/rotate-auth/`. Active credentials use
`service=gemini username=antigravity`; saved profiles use
`service=rotate-antigravity username=NAME`. Save copies the active item; switching
backs it up to the outgoing profile. Unhook backs it up, then clears the active item.

## Installer repair

`configure_shared_auth` is a best-effort, non-fatal installer step for shared-store
agents. When suffixed profiles exist, it migrates legacy `auth.json.*`, repairs
permissions and atomically reconnects `current` and agent links. Grok's
`auth.json.lock` is excluded and cannot opt a single-account install into migration.
No profiles means no change; existing profiles plus valid `current` allow repair
of a missing agent link.

Repair refuses regular-file agent credentials. Use the provider's `--save NAME`
to copy that login and rebuild the chain, also when importing a new machine's
credentials. Legacy remote handoff recovery belongs to [remote accounts](remote-development-accounts.md#enroll-and-reclaim).
