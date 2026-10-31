# Shell modules

`bash_profile.sh` sources `shell/*.sh`; agent CLIs must already be on `PATH`.

## Launch commands

| Command | Behavior |
| --- | --- |
| `cl` | `claude --dangerously-skip-permissions --effort max`; adds `--continue` for no arguments or first positional argument |
| `cmd` | Resolved binary with `--resume --yolo`; either caller `--yolo` or `--dangerously-skip-permissions` suppresses both defaults |
| `cc` | `codex resume --yolo` with shared statusline |
| `ca` | `cursor-agent --yolo` |
| `oc` | `opencode` without continue |
| `occ` | Claude Code on [OpenCode Go](opencode-go.md) |
| `ag` | `agy --dangerously-skip-permissions` |
| `gk` | Resolved Grok CLI; [config](agent-configs.md#grok) controls approvals |

All use `_cf_tmux` and forward arguments. `codex` also applies [statusline](statusline.md#codex)
overrides but runs directly; `command codex` bypasses it. `cl` adds Opus 4.8 via
launch-scoped `ANTHROPIC_CUSTOM_MODEL_OPTION*`; sourcing `shell/claude.sh` exports
`CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`. For `cmd` without existing sessions, use
`command cmd`; see [retained resume bug](archive/cmd-resume-bug.md).
The six rotation wrappers (`rotate-claude`, `rotate-codex`, `rotate-cursor-cli`,
`rotate-grok`, `rotate-opencode`, `rotate-antigravity`) call the shared [rotator](auth-rotation.md).

## tmux sessions and account labels

Each launch creates a session in `$PWD`, named from its basename: dots/colons become
`-`, leading `-` is guarded, `/` becomes `root`, collisions add `-1`, `-2`, etc.
It never attaches another agent to an existing session. Nonempty `CF_NO_TMUX`,
existing `$TMUX`, non-TTY stdin or missing tmux runs directly; internal
`-e NAME=value` variables reach either path.

`bin/clusterfork-tmux-label` names windows `<agent> - <account>` using Claude token
matches, shared auth links, Antigravity marker or Command Code `userName`.
Claude/Grok may fall back to single-account email; `occ` matches an overriding
`OPENCODE_API_KEY` to saved profiles. Missing/detached means `unknown`. Overrides
include `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `OCC_OPENCODE_AUTH_FILE` and rotation
directories. Labels are launch snapshots; rotation does not rename windows.
Automatic/terminal renaming is disabled; direct launches have no label.

## Grok confirmation

Interactive `grok` asks `did you mean to launch grok? y/n`; only `y` proceeds.
`gk`, non-TTY, nonempty `GROK_HEADLESS`/`CI`/`NONINTERACTIVE`, `agent`, utility/help
commands and headless flags (`-p`, `--single`, `--prompt-file`, `--prompt-json`,
`--output-format`) bypass it. Exact allowlist: `shell/grok.sh`.

## Browser helper

`chrome` uses `nohup prime-run google-chrome --ozone-platform=x11`, debug port 9222
and `$HOME/.config/chromium` data, without tmux; Chrome DevTools MCP targets it.
