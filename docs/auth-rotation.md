# Auth Rotation

The six `rotate-*` functions call `scripts/rotate_auth.py`. Claude and Cursor use
credential copies, Codex, Grok and OpenCode use shared-store symlinks, and
Antigravity uses GNOME Keyring.

| Command | Behavior |
| --- | --- |
| `rotate-* --save NAME` | Save/overwrite a named profile |
| `rotate-* --delete NAME` | Confirm deletion of a detached saved profile |
| `rotate-* --unhook` | Detach credentials before a new login |
| `rotate-* --list` | List profiles; `*` marks current |
| `rotate-*` | Select the best eligible account using the shared provider rules |
| `rotate-* NAME` | Select a specific profile |
| `rotate-* --kickoff [names]` | Ping all saved profiles, or the named subset |

Names allow letters, numbers, dots, underscores and dashes. Deletion prompts
`delete saved profile 'NAME'? [y/N]`; only case-insensitive `y`/`yes` confirms.
Other input, EOF or Ctrl+C cancels with exit 1. Active profiles are refused before
prompting; switch or unhook first, including when deleting the last profile.
Invalid/unknown names change nothing. Deletion removes the saved file/keyring
entry and any matching detached `current` marker, preserving unrelated logins.

To add an account, unhook, log in, then `--save NAME`. Shared stores refuse
unhooking a regular `auth.json`. Claude and Cursor require a matching saved login
when active credentials are present. Antigravity requires a saved current marker.
Save first when necessary. Selecting
a saved name reattaches it if login is abandoned. Avoid reinstalling while
unhooked: installer repair can reconnect the previous profile.

## Automatic account selection

All six bare commands rank saved profiles using live quota and provider-specific
eligibility rules. `scripts/rotate_usage.py` holds the local model and quota policy.
The current account participates and can remain selected when it is best; one
saved profile is sufficient. Aliases with
the same known identity share a quota probe, preferring the current login and
trying another saved login if its credentials are rejected.

Bare `rotate-codex` ranks saved profiles with readable credentials, excluding
`kian` and its account aliases. Fresh usable readings share one ranking: the
configured five-hour reserve preference comes first, then idle over busy, then
the shared untouched-account and weekly-reset ranking. A healthy busy account
therefore beats a nearly depleted idle account. Weekly limits still need only
positive capacity; they do not need the five-hour reserve.

Idle accounts are checked first. A winner meeting the reserve can be selected
without probing busy accounts or requiring their cache coverage, because no busy
reading can outrank it. Otherwise, busy readings join the same candidate pool
before selection; below-reserve accounts remain usable fallbacks.

When no fresh usable reading exists, the last resort includes exhausted, unknown,
rejected, stale, reset or incomplete readings. Complete known percentages rank
ahead of unknown/rejected readings, then highest worst-window remaining percent
(including zero), idle over busy, current login alias, and stable profile name.
Provider exhaustion flags count as zero available quota.

An eligible saved Codex profile is always selected, even when every account is
busy or depleted. A fallback line names the profile and tier 2 or 3. An initial
line lists accounts in open Codex sessions. No saved profiles, or
no readable non-excluded profiles, still fail without changing selection.

Occupancy comes from live Codex panes in the current tmux server, including
detached sessions, using the `codex - NAME` launch labels created by `cc`. Aliases
of an occupied account are busy together. Dead panes and panes no longer running
Codex do not count. Unlabeled/direct sessions and other tmux servers or machines
are outside detection's scope; when tmux is unavailable, all accounts count as
idle. This does not reserve accounts against simultaneous launches.

| Command | Quota source |
| --- | --- |
| `rotate-claude` | OAuth usage, including reported model-specific weekly limits |
| `rotate-codex` | ChatGPT Codex usage and applicable model limits |
| `rotate-cursor-cli` | Dashboard included quota for the configured Cursor model |
| `rotate-grok` | Grok CLI billing credits |
| `rotate-opencode` | OpenCode Go's per-key rolling, weekly and monthly usage |
| `rotate-antigravity` | Code Assist quota groups, falling back to raw model buckets |

Quota requests read each saved login directly without temporarily activating it,
refreshing credentials or sending model prompts. When an account usage cache
(under `CONKY_CACHE_DIR`, `CLUSTERFORK_USAGE_CACHE_DIR`, or `~/conky-linear-HUP/cache`)
is present, fresh (within 300 seconds), and covers the eligible logins, automatic
selection reads the cached quota snapshots directly for sub-second speed; otherwise
independent accounts are probed concurrently via live HTTP with 15-second timeouts.
Codex checks coverage separately for idle and busy accounts and also requires the
snapshot age limit (currently 60 seconds); incomplete Codex windows
trigger a live probe instead of establishing fresh capacity.
OpenCode profiles need an `opencode-go` API key to establish Go capacity.

Automatic selection excludes the personal profile `kian` and any alias sharing its
account identity (`ROTATE_EXCLUDE_PROFILES`), reserving it for explicit offline
choice. For the other five providers, unknown, rejected, stale, reset or depleted
readings are ineligible. If none qualifies with the required reserve, the command
exits 1 and retains the current credentials/selection. Codex uses the local ladder
above.
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

Each ping preserves refreshed credentials even when the model request fails.
Claude and Cursor copy updates back to the saved profile. Shared stores capture
regular-file auth replacements before rebuilding their links. Codex and Cursor
reject a replacement belonging to another account and preserve that active file.

Completion restores Claude's previous saved login with its updated credentials
(or the original bytes for an unsaved login), Cursor's prior login or unhooked
state, shared stores' previous `current` link (removing a temporary agent link
if initially unhooked), and Antigravity's
current saved profile or fully unhooked state. Limits: Claude with
no previous file and shared stores without `current` remain on the last profile
(the latter warns). Antigravity cannot restore a current profile omitted from an
explicit subset; use a full sweep when restoration matters. Claude, Cursor and
shared stores also run their preservation and restoration steps on interruption. Antigravity
has no general interrupted-sweep cleanup guarantee.

## Scheduled Codex kickoff

`clusterfork-codex-kickoff` checks saved local Codex accounts against the same
`codex-usage.json` cache and directory overrides as automatic rotation. It sends
the existing `rotate-codex --local --kickoff` ping only when the five-hour window
has exactly 0% used and either no pending reset or the provider's full five-hour
countdown. Unused accounts report a moving reset five hours from each reading;
that placeholder is eligible. A running window rounded to 0% with a shorter
countdown is left alone. Each account gets one ping even when several profile
names share it. All local profiles participate, including `kian`; cloud-only
accounts are excluded.

Readings must be at most 300 seconds old, matching Conky's local cache validity,
and respect any shorter age limit advertised by the source.
This scheduled check does not use rotation's stricter live-selection age limit.
Missing, stale, failed, incomplete or exhausted readings are skipped;
there is no live quota request or credential refresh just to check eligibility.
Kickoff preserves refreshed credentials and restores the previous selection as
described above. Failed pings continue through the selected accounts and make
the service fail for visibility in the journal.

The installer copies the helper and `systemd/clusterfork-codex-kickoff` service
and timer into `~/.config/clusterfork/`. When the current user's systemd manager
is available for that home, it also links the units and enables the timer.
Other environments receive the files without activating a service. Reinstalling
updates the units and reloads the available user manager.

The timer runs at :00 and :30, catches up once after downtime and never overlaps
an already-running invocation. The service reads the installed `.env` for the
same cache/auth overrides used by the shell commands. To inspect or manage it:

```bash
~/.config/clusterfork/bin/clusterfork-codex-kickoff --dry-run
systemctl --user status clusterfork-codex-kickoff.timer
journalctl --user -u clusterfork-codex-kickoff.service -n 30
systemctl --user start clusterfork-codex-kickoff.service
systemctl --user disable --now clusterfork-codex-kickoff.timer
```

For manual activation after installing without an available user manager:

```bash
systemctl --user link ~/.config/clusterfork/systemd/clusterfork-codex-kickoff.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now clusterfork-codex-kickoff.timer
```

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

## rotate-cursor-cli

Saved OAuth profiles are `~/.local/share/clusterfork-auth/cursor/auth.json.NAME`.
Cursor updates its regular `auth.json` in `${XDG_CONFIG_HOME:-~/.config}/cursor/`
by atomic replacement. Before selection, unhook or kickoff, the rotator
copies an updated login back to the saved profile with the same OAuth subject.
The store's `current` link identifies the preferred name when profiles share a
subject. Account labels verify the actual login against the saved profiles.
An unrecognized active login stays in place until saved explicitly. Save
validates the token pair and preserves the full credential record.

Close Cursor sessions that use the active file before switching accounts,
unhooking, running kickoff or reinstalling. The rotator serializes changes to
the profile store. For named profiles, the installed shell exports
`AGENT_CLI_CREDENTIAL_STORE=file` and `ca` passes it through tmux. Kickoff sets it
on each ping. For a first login or a process launched outside that shell, use
the setting explicitly.

```bash
AGENT_CLI_CREDENTIAL_STORE=file cursor-agent login
rotate-cursor-cli --save NAME
```

## Shared-store agents

| Command | Agent auth file | Store under `~/.local/share/clusterfork-auth/` |
| --- | --- | --- |
| `rotate-codex` | `~/.codex/auth.json` | `codex/` |
| `rotate-grok` | `~/.grok/auth.json` | `grok/` |
| `rotate-opencode` | `~/.local/share/opencode/auth.json` | `opencode/` |

Each agent file links relatively to its store's `current`, which links to
`auth.json.NAME`. Rotation changes only `current`. Save copies the active login,
selects it and rebuilds the chain even after login replaced the agent link with
a regular file. Unhook removes only the agent-side symlink, retaining store and
`current`.

## Sharing with a container

Bind-mount `~/.local/share/clusterfork-auth` at the same home-relative path.
For symlink-based agents, directory mounts expose `current` changes immediately
and mounting an individual `auth.json` pins its resolved target. A read-only mount
supports host-owned rotation and refresh. Cursor selects a saved profile into a
local active copy in each environment. Syncing updated Cursor credentials back
to a saved profile requires a writable store.

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
credentials.

Cursor repair materializes the active credential file from a managed symlink
or restores the selected saved profile when the active file is missing or
contains Cursor's OS keyring marker. A regular login is matched and backed up
by OAuth subject. With no saved profiles, the installer leaves Cursor auth
to the CLI.
