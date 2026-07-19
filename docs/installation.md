# Installation

## Quick start

```bash
./install-clusterfork.sh
```

## What the installer writes

`install-clusterfork.sh` copies config from the repo into your home directory:

| Source (repo)                     | Destination                              | Contents                          |
|-----------------------------------|------------------------------------------|-----------------------------------|
| `.env`                            | `~/.config/clusterfork/.env`             | API keys (gitignored)             |
| `bash_profile.sh`                 | `~/.config/clusterfork/bash_profile.sh`  | Sourced on shell startup          |
| `shell/*.sh`                      | `~/.config/clusterfork/shell/*.sh`       | One module per agent              |
| `opencode.json`                   | `~/.config/opencode/opencode.json`       | OpenCode settings                 |
| `qwen.json`                       | `~/.qwen/settings.json`                  | Qwen Code settings                |
| `antigravity.json`                | `~/.gemini/antigravity-cli/settings.json`| Antigravity settings              |
| `grok.toml`                       | `~/.grok/config.toml`                    | Grok CLI settings                 |
| `skills/`                         | `~/.qwen/skills/` and `~/.grok/skills/`  | Shared skills for Qwen and Grok   |

The installer also appends a `source` line to `~/.bashrc` so `bash_profile.sh` is loaded in every new shell.

## Requirements

- A `.env` file in the repo root containing your API keys. The installer aborts if it's missing.

## Re-running

The installer is idempotent — running it again **overwrites** every mapped destination from the repo (full replace, not merge). It will not add a duplicate `source` line to `~/.bashrc`. See [Conventions](conventions.md) for the source-of-truth rule.
