# Conventions

- **Bash:** scripts use `set -euo pipefail` where appropriate. Shell modules are plain bash sourced by `bash_profile.sh` — no shebang needed.
- **Secrets:** live in `.env` (gitignored). Never hardcode API keys.
- **Env isolation:** launch wrappers set environment variables inside a subshell so they don't leak into the parent session.
- **Idempotency:** `install-clusterfork.sh` can be re-run safely. It won't add duplicate `source` lines to `~/.bashrc`.
- **Testing changes:** re-run `./install-clusterfork.sh` and open a fresh shell to verify.
