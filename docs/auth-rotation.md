# Auth Rotation

The six `rotate-*` functions call `scripts/rotate_auth.py`, using copy (Claude),
shared-store symlink (Codex/Cursor/Grok/OpenCode), or GNOME Keyring (Antigravity).

| Command | Behavior |
| --- | --- |
| `rotate-* --save NAME` | Save/overwrite a named profile |
| `rotate-* --delete NAME` | Confirm deletion of a detached saved profile |
| `rotate-* --unhook` | Detach credentials before a new login |
| `rotate-* --list` | List profiles; `*` marks current |
| `rotate-*` | Select next profile in sorted order |
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
`current`. [Remote accounts](remote-development-accounts.md) owns cloud reservation
and reclaim rules for Codex/Cursor.

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
credentials. Cloud-reserved legacy handoff recovery belongs to [remote accounts](remote-development-accounts.md#enroll-and-reclaim).
