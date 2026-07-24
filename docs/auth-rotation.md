# Auth Rotation

Several agents support switching between multiple saved accounts without re-logging-in.

## rotate-claude

Switches Claude Code accounts stored as `~/.claude/.credentials.json.*` files.

- `rotate-claude --list` — list saved profiles (`*` marks the current one)
- `rotate-claude` — rotate to the next account in sorted order
- `rotate-claude NAME` — switch to a specific account

Claude Code rewrites `.credentials.json` on every token refresh, so rotation copies the selected file over `.credentials.json` rather than symlinking. The active account is identified by matching the `accessToken` field.

## rotate-codex

Switches Codex accounts stored under
`~/.local/share/clusterfork-auth/codex/`.

- `rotate-codex --list` — list saved profiles (`*` marks the current one)
- `rotate-codex` — rotate to the next account
- `rotate-codex NAME` — switch to a specific account

Profiles live under `~/.local/share/clusterfork-auth/codex/`. The link chain
the installer established:

```text
~/.codex/auth.json
  → ../.local/share/clusterfork-auth/codex/current
  → auth.json.SUFFIX
```

Rotation changes only the shared `current` symlink.

## rotate-cursor-cli

Same pattern as `rotate-codex`, with profiles stored under
`~/.local/share/clusterfork-auth/cursor/` and this link chain:

```text
~/.config/cursor/auth.json
  → ../../.local/share/clusterfork-auth/cursor/current
  → auth.json.SUFFIX
```

- `rotate-cursor-cli --list` — list saved profiles (`*` marks the current one)
- `rotate-cursor-cli` — rotate to the next account
- `rotate-cursor-cli NAME` — switch to a specific account

## rotate-opencode

Same pattern as `rotate-codex`, with profiles stored under
`~/.local/share/clusterfork-auth/opencode/` and this link chain:

```text
~/.local/share/opencode/auth.json
  → ../clusterfork-auth/opencode/current
  → auth.json.SUFFIX
```

- `rotate-opencode --list` — list saved profiles (`*` marks the current one)
- `rotate-opencode` — rotate to the next account
- `rotate-opencode NAME` — switch to a specific account

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

Switches Antigravity accounts using `secret-tool` (GNOME Keyring). State is kept in `~/.gemini/antigravity-cli/rotate-auth/`.

- `rotate-antigravity --save NAME` — save the active keyring item as a named profile
- `rotate-antigravity --list` — list saved profiles (`*` marks the current one)
- `rotate-antigravity NAME` — switch to a specific profile
- `rotate-antigravity` — rotate to the next profile

The active account lives at keyring entry `service=gemini username=antigravity`. Saved profiles live at `service=rotate-antigravity username=NAME`. Before switching, the current keyring item is backed up to the outgoing profile.

## History: installer migration (removed)

The original `install-clusterfork.sh` contained a `configure_shared_auth`
function (~150 lines) that ran as a best-effort, non-fatal step at the end of
every install. It detected legacy `auth.json.*` profile files sitting directly
in each agent's own directory (before the shared store existed), moved them
into `~/.local/share/clusterfork-auth/<agent>/`, updated permissions, and
atomically repointed `auth.json` through the shared `current` symlink.

It was intentionally idempotent: if no suffixed profiles existed it was a
no-op, so it was safe to leave in place indefinitely. It was removed in July
2026 once the single user's setup was confirmed fully migrated — all three
agents (`codex`, `cursor`, `opencode`) had their `auth.json` already
symlinked through `clusterfork-auth/<agent>/current`, with no legacy files
remaining in the agent directories.

If you ever need to set up the link chain from scratch on a new machine, do it
manually:

```bash
STORE=~/.local/share/clusterfork-auth/<agent>
mkdir -p "$STORE"
chmod 700 ~/.local/share/clusterfork-auth "$STORE"
cp <your-auth-file> "$STORE/auth.json.NAME"
chmod 600 "$STORE/auth.json.NAME"
ln -s auth.json.NAME "$STORE/current"
ln -sfT "$(realpath --relative-to=<agent-dir> "$STORE/current")" <agent-dir>/auth.json
```
