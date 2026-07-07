# Auth Rotation

Several agents support switching between multiple saved accounts without re-logging-in.

## rotate-claude

Switches Claude Code accounts stored as `~/.claude/.credentials.json.*` files.

- `rotate-claude` — rotate to the next account in sorted order
- `rotate-claude NAME` — switch to a specific account

Claude Code rewrites `.credentials.json` on every token refresh, so rotation copies the selected file over `.credentials.json` rather than symlinking. The active account is identified by matching the `accessToken` field.

## rotate-codex

Switches Codex accounts stored as `~/.codex/auth.json.*` files.

- `rotate-codex` — rotate to the next account
- `rotate-codex NAME` — switch to a specific account

Uses a symlink: `auth.json` → `auth.json.SUFFIX`. Fails if `auth.json` is a regular file (instructions are printed to fix it).

## rotate-cursor-cli

Same pattern as `rotate-codex` but for Cursor CLI, stored under `~/.config/cursor/auth.json.*`.

- `rotate-cursor-cli` — rotate to the next account
- `rotate-cursor-cli NAME` — switch to a specific account

## rotate-antigravity

Switches Antigravity accounts using `secret-tool` (GNOME Keyring). State is kept in `~/.gemini/antigravity-cli/rotate-auth/`.

- `rotate-antigravity --save NAME` — save the active keyring item as a named profile
- `rotate-antigravity --list` — list saved profiles (`*` marks the current one)
- `rotate-antigravity NAME` — switch to a specific profile
- `rotate-antigravity` — rotate to the next profile

The active account lives at keyring entry `service=gemini username=antigravity`. Saved profiles live at `service=rotate-antigravity username=NAME`. Before switching, the current keyring item is backed up to the outgoing profile.
