# Auth Rotation

Several agents support switching between multiple saved accounts without re-logging-in.

The `rotate-*` shell functions call `scripts/rotate_auth.py`. One implementation,
three backends: copy (Claude), shared-store symlink (Codex / Cursor / Grok /
OpenCode), and GNOME Keyring (Antigravity). All six share the same flags:

- `rotate-* --save NAME` — save the active account as a named profile
- `rotate-* --delete NAME` — delete a saved profile; detach or switch away from
  the active profile first
- `rotate-* --unhook` — detach the active credentials so a new login does not
  overwrite the current profile
- `rotate-* --list` — list saved profiles (`*` marks the current one)
- `rotate-*` — rotate to the next account in sorted order
- `rotate-* NAME` — switch to a specific account
- `rotate-* --kickoff [names]` — for every saved profile of
  that provider, or just the named ones, temporarily install it and send one
  tiny one-shot message ("hi") via the agent CLI's non-interactive mode to
  start each account's usage ticker. See the restoration limits below.

`--save` overwrites an existing profile of that name. Profile names may only
contain letters, numbers, dots, underscores, and dashes.

To remove an account, run (for example) `rotate-codex --delete NAME`. It asks
`delete saved profile 'NAME'? [y/N]` before making changes; only `y` or `yes`
(case-insensitive) confirms. Enter, any other answer, EOF, or Ctrl+C cancels
without changing state and exits with status 1. Deleting an active profile is
refused before prompting; switch to another profile or run
`rotate-codex --unhook` first. This also permits deleting the
last saved profile. An unknown or invalid name fails without changing state.
Deletion removes the saved credential file (or Antigravity keyring entry and
profile-list entry). It also removes the shared `current` link or Antigravity
current marker when it names the deleted, detached profile. Other saved
profiles and an unrelated active login are preserved.

To add an account: `rotate-* --unhook`, log in as the new account, then
`rotate-* --save NAME`. Shared-store detach refuses a regular-file `auth.json`;
Claude refuses an unsaved regular-file login, identified by token matching;
Antigravity requires a saved current profile marker. Save the login first if
needed. Saved profiles stay put. A later `rotate-* NAME` reattaches a saved
profile if you abort before logging in. Do not re-run the installer in between:
it repairs a missing
agent-side `auth.json` symlink and would hook the previous profile again.

## --kickoff

Pings every saved profile by default, or only the named ones (`--kickoff alice bob`),
once per profile with a one-shot non-interactive message (`hi`)
so the provider starts counting usage for each account. Per-agent ping
commands: `claude -p`, `codex exec --skip-git-repo-check -m gpt-6-luna`, `cursor-agent -p`,
`grok -p`, `opencode run`, `agy --print`. Each invocation gets a 120s timeout; stdin is
detached so nothing blocks.

The sweep is strictly sequential: install a profile, run its ping to
completion, then move to the next profile.

After a normally completed sweep, Claude restores the previous active file's
bytes if it existed. The shared-store backends restore the previous `current`
link when one existed and remove a temporary agent link if the account started
unhooked. Antigravity reinstalls its current saved profile, or clears the active
item if it started fully unhooked.

Restoration has limits in the current implementation: Claude with no active
file and a shared store with no previous `current` remain on the last profile;
the latter prints a warning. Antigravity treats a current profile omitted from
an explicitly selected subset as unknown, so it cannot restore that profile.
An interrupted sweep has no general cleanup guarantee. Use a full sweep when
the current Antigravity profile must be restored.

Unknown names, duplicate names, and dashed arguments fail before pings run.
A ping failure is reported and the sweep continues; the final exit code is 1
if any ping failed. Shared-store kickoff refuses a regular-file `auth.json`;
Antigravity refuses an active keyring item with no recoverable current profile.

Pings run sequentially because every backend shares one active credential
slot. Concurrent launches can read the wrong account or overwrite refreshed
tokens. Per-process config isolation is not supported consistently across all
clients. Only Codex pins a small model (`gpt-6-luna`); the other pings use each
CLI's configured default.

## Implementation and directory overrides

All rotation logic lives in `scripts/rotate_auth.py`; shell modules resolve it
from `BASH_SOURCE`, so the repo and installed wrappers share one
implementation. Installer auth migration is separate from account selection.

Override directories (empty or unset uses the default, same as bash
`${VAR:-default}`): `ROTATE_CLAUDE_DIR`, `ROTATE_CODEX_CODEX_DIR`,
`ROTATE_CODEX_AUTH_STORE_DIR`, `ROTATE_CURSOR_DIR`,
`ROTATE_CURSOR_AUTH_STORE_DIR`, `ROTATE_GROK_DIR`,
`ROTATE_GROK_AUTH_STORE_DIR`, `ROTATE_OPENCODE_DIR`,
`ROTATE_OPENCODE_AUTH_STORE_DIR`, `ROTATE_ANTIGRAVITY_STATE_DIR`. Shared-store
`--save` chmods only the store directory itself (700), never its parent.

## rotate-claude

Switches Claude Code accounts stored as `~/.claude/.credentials.json.*` files.
`--save NAME` copies the active `.credentials.json` onto `.credentials.json.NAME`.
`--unhook` deletes a regular active file when its `accessToken` matches a saved
profile; legacy active symlinks can also be detached.

Claude Code rewrites `.credentials.json` on every token refresh, so rotation copies the selected file over `.credentials.json` rather than symlinking. The active account is identified by matching the `accessToken` field.

## rotate-codex

Cloud account ownership and reclaiming remote credentials are described in
[Remote accounts](remote-development-accounts.md).

Switches Codex accounts stored under
`~/.local/share/clusterfork-auth/codex/`. `--save NAME` writes the active
`auth.json` to `auth.json.NAME` and points `current` at it (and will rebuild
the link chain if a login replaced `auth.json` with a regular file).
`--unhook` removes the agent-side `auth.json` symlink and leaves the store
and `current` intact.

The link chain the installer established:

```text
~/.codex/auth.json
  → ../.local/share/clusterfork-auth/codex/current
  → auth.json.SUFFIX
```

Rotation changes only the shared `current` symlink.

## rotate-cursor-cli

Same pattern as `rotate-codex`, including `--save` and `--unhook`, with profiles stored under
`~/.local/share/clusterfork-auth/cursor/` and this link chain:

```text
~/.config/cursor/auth.json
  → ../../.local/share/clusterfork-auth/cursor/current
  → auth.json.SUFFIX
```

## rotate-grok

Same pattern as `rotate-codex`, including `--save` and `--unhook`, with profiles stored under
`~/.local/share/clusterfork-auth/grok/` and this link chain:

```text
~/.grok/auth.json
  → ../.local/share/clusterfork-auth/grok/current
  → auth.json.SUFFIX
```

## rotate-opencode

Same pattern as `rotate-codex`, including `--save` and `--unhook`, with profiles stored under
`~/.local/share/clusterfork-auth/opencode/` and this link chain:

```text
~/.local/share/opencode/auth.json
  → ../clusterfork-auth/opencode/current
  → auth.json.SUFFIX
```

## Sharing with a container

Mount `~/.local/share/clusterfork-auth` at the same home-relative path in the
container. A directory bind mount exposes changes to `current` immediately,
unlike mounting `auth.json` as a single file, which resolves and pins the
symlink target at mount time.

The mount can be read-only in the container when the host owns account
selection and token updates. In that mode, run rotation commands and token
refreshes on the host; the container follows the updated `current` link
automatically. A container-side token refresh cannot persist through a
read-only mount and can fail when the current access token expires.

## rotate-antigravity

Switches Antigravity accounts using `secret-tool` (GNOME Keyring). State is kept in `~/.gemini/antigravity-cli/rotate-auth/`. `--save NAME` copies the active keyring item to `service=rotate-antigravity username=NAME`. `--unhook` backs the active item up to the current profile, then `secret-tool clear`s `service=gemini username=antigravity`.

The active account lives at keyring entry `service=gemini username=antigravity`. Saved profiles live at `service=rotate-antigravity username=NAME`. Before switching, the current keyring item is backed up to the outgoing profile.

## Installer repair

`install-clusterfork.sh` runs `configure_shared_auth` as a best-effort,
non-fatal step for Codex, Cursor, Grok, and OpenCode. When multi-account profiles
exist under `~/.local/share/clusterfork-auth/<agent>/`, it:

- migrates any leftover `auth.json.*` files still in the agent directory into
  the shared store
- ensures permissions on the store and profile files
- atomically repoints `store/current` and the agent's `auth.json` through the
  shared link chain

Grok's runtime `~/.grok/auth.json.lock` sidecar is ignored during legacy
profile discovery. It is not an `auth.json.SUFFIX` account profile and does not
opt an otherwise single-account Grok installation into shared auth.

It is a no-op when no suffixed profiles exist (plain single-account
`auth.json`). Re-running the installer repairs a missing agent-side
`auth.json` symlink as long as the shared store still has profiles and a
valid `current` link.

If the agent's `auth.json` is a regular file (for example after a login that
replaced the symlink), repair refuses. Save it as a named profile and restore
the link chain with `rotate-codex --save NAME`, `rotate-cursor-cli --save NAME`,
`rotate-grok --save NAME`, or `rotate-opencode --save NAME`. That copies the regular file into the store,
points `current` at it, and replaces `auth.json` with the shared symlink.

For a new machine importing credentials, `rotate-* --save NAME` performs the
copy and relink.
