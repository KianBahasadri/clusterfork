# Conventions

- **Bash:** scripts use `set -euo pipefail` where appropriate. Shell modules are plain bash sourced by `bash_profile.sh` — no shebang needed.
- **Secrets:** live in `.env` (gitignored). Never hardcode API keys.
- **Env isolation:** launch wrappers set environment variables inside a subshell so they don't leak into the parent session.
- **Idempotency:** `install-clusterfork.sh` can be re-run safely. It won't add duplicate `source` lines to `~/.bashrc`.
- **Testing changes:** re-run `./install-clusterfork.sh` and open a fresh shell to verify.

## Repo is the source of truth

Clusterfork is a **dotfile installer**. The files in this repo are authoritative for every destination they map to. The installer's job is to **overwrite** those destinations from the repo — full file (or full directory for `skills/` and `shell/`), every time.

Exception: `~/.codex/skills/.system` is Codex-managed. The installer replaces only non-dot skill directories under `~/.codex/skills/` from `skills/`, and leaves `.system` intact.

Do **not**:

- Merge fragments into an existing home-dir config
- Preserve "user customizations" outside the repo
- Use marker blocks, partial section replacement, or `agent mcp add`-style surgery to avoid clobbering a live file
- Treat `~/.grok/config.toml` (or any other mapped dest) as sacred local state the installer must carefully edit

If a setting belongs on the machine, it belongs **in the repo** first, then gets installed by overwrite. Local-only tweaks that aren't committed will be wiped on the next `./install-clusterfork.sh` — that is intentional.

Exceptions (key/line only — not full-file replace):

- `~/.bashrc`: appends a single `source` line if missing
- `~/.cursor/cli-config.json`: sets/updates only the `statusLine` key so the installed Cursor statusline script is wired up; session/auth caches in that file stay untouched
- `~/.claude.json`: upserts only the `mcpServers.ElevenLabs` entry; the rest of Claude's local state stays untouched
- `~/.cursor/mcp.json`: full overwrite from `agents/cursor-mcp.json`, but `${ENV}` placeholders are expanded from the clusterfork `.env` at install time so secrets are not committed
- Codex/Cursor multi-account auth: performs a non-fatal, state-aware migration
  instead of overwriting credentials. See [Auth Rotation](auth-rotation.md).
