# Auth Rotation

Several agents support switching between multiple saved accounts without re-logging-in.

The `rotate-*` shell functions call `scripts/rotate_auth.py`. One implementation,
three backends: copy (Claude), shared-store symlink (Codex / Cursor / OpenCode),
and GNOME Keyring (Antigravity). All five share the same flags:

- `rotate-* --save NAME` — save the active account as a named profile
- `rotate-* --unhook` — detach the active credentials so a new login does not
  overwrite the current profile
- `rotate-* --list` — list saved profiles (`*` marks the current one)
- `rotate-*` — rotate to the next account in sorted order
- `rotate-* NAME` — switch to a specific account
- `rotate-* --start [names]` (alias `--kickoff`) — for every saved profile of
  that provider, or just the named ones, temporarily install it and send one
  tiny one-shot message ("hi") via the agent CLI's non-interactive mode. Intent
  is only to kick off each account's usage ticker; the active credentials are
  restored afterwards.

`--save` overwrites an existing profile of that name. Profile names may only
contain letters, numbers, dots, underscores, and dashes.

To add an account: `rotate-* --unhook`, log in as the new account, then
`rotate-* --save NAME`. `--unhook` refuses if the active credentials are not
already saved as a profile (a regular-file `auth.json` on the shared-store
backends, a Claude active file whose token matches no profile, or an
Antigravity keyring item with no current profile marker). Saved profiles stay
put. A later `rotate-* NAME` reattaches a saved profile if you abort before
logging in. Do not re-run the installer in between: it repairs a missing
agent-side `auth.json` symlink and would hook the previous profile again.

The shell modules are thin wrappers. They resolve
`scripts/rotate_auth.py` from `BASH_SOURCE` so the same function works when
sourced from the repo or from `~/.config/clusterfork/shell/` after install
(the installer copies the script to `~/.config/clusterfork/scripts/`).

## --start (alias --kickoff)

Pings every saved profile by default, or only the named ones (`--start alice bob`),
once per profile with a one-shot non-interactive message (`hi`)
so the provider starts counting usage for each account. Per-agent ping
commands: `claude -p`, `codex exec --skip-git-repo-check`, `cursor-agent -p`,
`opencode run`, `agy --print`. Each invocation gets a 120s timeout; stdin is
detached so nothing blocks.

The sweep is strictly sequential: install a profile, run its ping to
completion, then move to the next profile.

State handling per backend:

- Claude: the active `.credentials.json` bytes are backed up and restored
  byte-identical after the sweep.
- Codex / Cursor / OpenCode: only the shared `current` symlink is repointed
  per profile; it is restored to its previous target afterwards. If the agent
  side was unhooked, the temporary `auth.json` link is removed again.
- Antigravity: each profile is installed into the active keyring slot in turn;
  the original profile is reinstalled at the end, and a fully unhooked state is
  restored by clearing the active item.

An unknown name fails fast before any pings run, with the known profiles listed; duplicates and dashed args are rejected. Exit code is 1 if any account's ping failed; failures are reported per line
and do not stop the sweep. The command refuses to run when the active
credentials are not recoverable through rotation machinery (a regular-file
`auth.json`, or an Antigravity active keyring item that matches no profile).

### Why not parallel

Parallel kicks were tried and reverted to serial. All backends have exactly
one active credential slot (the `current` symlink chain, `.credentials.json`,
or the keyring item), so concurrent pings cannot each get their own account:

- **Credential misattribution.** An agent process reads credentials only after
  CLI startup, not at spawn time. Spawning all pings against one slot means
  whichever profile happens to be installed when each process reads wins —
  some accounts never got kicked, others counted twice. A staggered variant
  (install → spawn → wait N seconds → next) needs a guess at each CLI's
  time-to-read and still misattributes on slow startups or cold caches.
- **Token-refresh races.** A long-running ping can refresh an OAuth token and
  write it back mid-sweep, clobbering or being clobbered by the next install.
  With one blocking ping at a time there is no other writer.
- **Env-var isolation is not portable.** The clean fix — give each process its
  own config dir via `CLAUDE_CONFIG_DIR` / `CODEX_HOME`-style relocations so
  all pings start truly simultaneously — works for Claude/Codex/OpenCode but
  not Cursor, which honors no such variable, so the sweep would need two
  different mechanisms with different failure modes for one best-effort job.

Serial costs wall time (sum of full pings instead of overlap) but every ping
provably runs against its own account with no concurrent writers. Since the
goal is a low-frequency ticker kick, that trade was taken deliberately.

Known limitation: the ping always uses whatever model the agent CLI defaults
to — it does not pick the cheapest/smallest model for the provider. Picking
per-provider smallest models would mean keeping a pinned model ID per provider
(and OpenCode additionally needs a `provider/model` pair), which has not been
built yet.

## Why it is one Python script

Until 2026-08-14 each `rotate-*` function was a self-contained bash copy in
its `shell/*.sh` module. Codex, Cursor, and OpenCode were the same program
after renaming (~256 lines × 3). Claude was the same control flow with
copy-and-token-match instead of a symlink. Antigravity was the same flags
against `secret-tool`. The next shared flag would have been a four-way paste.

They were collapsed into `scripts/rotate_auth.py` with three backends (copy,
shared-store symlink, keyring). Python was already how this repo does
standalone tools, and JSON / atomic replace / tests are less painful there
than in sourced bash.

`--save` existed only on `rotate-antigravity`. Adding it to the other four
started as bash, then was thrown away untested. The Python port was written
from the last committed bash (no `--save` on the file backends), and `--save`
was implemented only in Python. `configure_shared_auth` in the installer was
left as migrate/repair — it is not a second rotator.

`--save` on the shared-store backend is the user-facing fix for a login that
replaced `auth.json` with a regular file. The installer still refuses to
touch that case; the user names the new login and `--save` copies it into
the store, points `current` at it, and puts the relative symlink back.

The Antigravity bash wrapper used to `set +x` around the body so `set -x`
would not echo secrets. That is gone: bash only sees `python3 … antigravity`,
and the secret stays inside Python / `secret-tool`.

Override directories (empty or unset uses the default, same as bash
`${VAR:-default}`): `ROTATE_CLAUDE_DIR`, `ROTATE_CODEX_CODEX_DIR`,
`ROTATE_CODEX_AUTH_STORE_DIR`, `ROTATE_CURSOR_DIR`,
`ROTATE_CURSOR_AUTH_STORE_DIR`, `ROTATE_OPENCODE_DIR`,
`ROTATE_OPENCODE_AUTH_STORE_DIR`, `ROTATE_ANTIGRAVITY_STATE_DIR`. Shared-store
`--save` chmods only the store directory itself (700), never its parent.

## rotate-claude

Switches Claude Code accounts stored as `~/.claude/.credentials.json.*` files.
`--save NAME` copies the active `.credentials.json` onto `.credentials.json.NAME`.
`--unhook` deletes `.credentials.json` when its `accessToken` matches a saved
profile.

Claude Code rewrites `.credentials.json` on every token refresh, so rotation copies the selected file over `.credentials.json` rather than symlinking. The active account is identified by matching the `accessToken` field.

## rotate-codex

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
non-fatal step for Codex, Cursor, and OpenCode. When multi-account profiles
exist under `~/.local/share/clusterfork-auth/<agent>/`, it:

- migrates any leftover `auth.json.*` files still in the agent directory into
  the shared store
- ensures permissions on the store and profile files
- atomically repoints `store/current` and the agent's `auth.json` through the
  shared link chain

It is a no-op when no suffixed profiles exist (plain single-account
`auth.json`). Re-running the installer repairs a missing agent-side
`auth.json` symlink as long as the shared store still has profiles and a
valid `current` link.

If the agent's `auth.json` is a regular file (for example after a login that
replaced the symlink), repair refuses. Save it as a named profile and restore
the link chain with `rotate-codex --save NAME`, `rotate-cursor-cli --save NAME`,
or `rotate-opencode --save NAME`. That copies the regular file into the store,
points `current` at it, and replaces `auth.json` with the shared symlink.

Manual equivalent:

```bash
STORE=~/.local/share/clusterfork-auth/<agent>
mkdir -p "$STORE"
chmod 700 ~/.local/share/clusterfork-auth "$STORE"
cp <agent-dir>/auth.json "$STORE/auth.json.NAME"
chmod 600 "$STORE/auth.json.NAME"
ln -sfn auth.json.NAME "$STORE/current"
ln -sfn "$(realpath -ms --relative-to=<agent-dir> "$STORE/current")" <agent-dir>/auth.json
```

From-scratch setup on a new machine is the same pattern if you are importing an
auth file from elsewhere; `rotate-* --save NAME` does that copy and relink.
