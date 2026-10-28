# Shell modules

`bash_profile.sh` sources every `shell/*.sh`. Agent CLIs must already be on
`PATH`; clusterfork installs their settings and wrappers, not the CLIs.

## Launch commands

| Command | Launch behavior |
| --- | --- |
| `cl` | `claude --dangerously-skip-permissions --effort max`; adds `--continue` when there are no arguments or the first argument is positional |
| `cmd` | Resolved `cmd` binary with `--resume --yolo`; if any argument is `--yolo` or `--dangerously-skip-permissions`, forwards all arguments without adding either default flag |
| `cc` | `codex resume --yolo`, with shared statusline overrides |
| `ca` | `cursor-agent --yolo` |
| `oc` | `opencode` (starts without a continue flag) |
| `occ` | Claude Code against OpenCode Go; see [OpenCode Go](opencode-go.md) |
| `ag` | `agy --dangerously-skip-permissions` |
| `gk` | Resolved Grok CLI directly; approval behavior comes from [its config](agent-configs.md#grok) |

All commands in the table use `_cf_tmux` and forward caller arguments.
`codex` is also a shell function: it applies the same
[statusline settings](statusline.md#codex) as `cc` but runs directly.
`command codex` bypasses that function.

`cl` adds an Opus 4.8 picker row using launch-scoped
`ANTHROPIC_CUSTOM_MODEL_OPTION*` variables. `shell/claude.sh` also exports
`CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1` when sourced.

The `cmd` resume failure in directories without sessions is a known retained
behavior. Use `command cmd` for a fresh session; the
[dated investigation](archive/cmd-resume-bug.md) records the unshipped fix.

The six `rotate-*` functions are thin calls to `scripts/rotate_auth.py`:
`rotate-claude`, `rotate-codex`, `rotate-cursor-cli`, `rotate-grok`,
`rotate-opencode`, and `rotate-antigravity`. Their flags and state live in
[Auth Rotation](auth-rotation.md).

## tmux sessions and account labels

`_cf_tmux` creates a fresh session in `$PWD`. The name comes from the directory
basename (`.` and `:` become `-`, a leading `-` is guarded, `/` becomes `root`);
collisions get `-1`, `-2`, and so on. It does not attach another agent to an
existing session.

The wrapper runs directly when `CF_NO_TMUX` is nonempty, `$TMUX` is set, stdin
is not a TTY, or tmux is missing. Its internal `-e NAME=value` options carry
launch variables through both paths.

New windows are named `<agent> - <account>` by `bin/clusterfork-tmux-label`.
Claude matches saved tokens, shared-store agents follow the active auth link,
Antigravity reads its current marker, and Command Code reads `userName`.
Claude and Grok can fall back to a single-account email. `occ` uses the
OpenCode account, matching `OPENCODE_API_KEY` to saved profiles when supplied.
Missing or detached identities show `unknown`.

The label helper respects `CODEX_HOME`, `CLAUDE_CONFIG_DIR`,
`OCC_OPENCODE_AUTH_FILE`, and the rotation directory overrides. Labels are
snapshots taken before launch: rotating accounts does not rename existing
windows. Automatic window renaming and terminal rename requests are disabled
for those windows. Launches that bypass tmux do not get a label.

## Grok confirmation

Interactive `grok` asks `did you mean to launch grok? y/n`; only `y` starts it.
`gk` bypasses the question. So do non-TTY calls, nonempty `GROK_HEADLESS`,
`CI`, or `NONINTERACTIVE`, headless flags (`-p`, `--single`, `--prompt-file`,
`--prompt-json`, `--output-format`), the `agent` subcommand, and utility/help
commands. The exact allowlist is in `shell/grok.sh`.

## Browser helper

`chrome` backgrounds `prime-run google-chrome --ozone-platform=x11` with remote
debugging on port 9222 and `$HOME/.config/chromium` as its user-data directory.
It uses `nohup` and is not wrapped in tmux. This is the browser targeted by the
Chrome DevTools MCP entries.
