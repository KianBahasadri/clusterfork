# `cmd` fails with "No conversations found to resume." (not clusterfork's install bug — investigated 2026-08-18)

Recorded so the next person who hits it does not re-derive the Command Code
CLI mechanics below. A fix was built and end-to-end validated, then reverted
by decision — see [Outcome](#outcome).

## Symptom

Running `cmd` in a directory that has no Command Code sessions for it prints
`No conversations found to resume.` and exits to the shell instead of starting
a session:

```text
$ cmd
No conversations found to resume.
```

The `cmd` function in `shell/cmd.sh` has unconditionally appended `--resume`
since commit `8a0cc78` ("Make cmd launch with --resume by default"):

```sh
command cmd --resume --yolo "$@"
```

That commit intended "resume the most recent conversation" but `--resume` is
the wrong flag for that (see below).

## What we learned about Command Code (v1.27.1)

- **`--resume` with no argument opens a session *picker*.** With zero sessions
  for the current directory it fails hard instead of falling back to a new
  session. Confirmed in the CLI bundle: `validateSessionAvailability` exits
  with `"No conversations found to resume."` when a resume/continue flag is
  present and the project session count is 0.
- **`--continue` hits the same guard.** It is the correct flag for
  "resume the most recent conversation", but with no sessions it exits with
  the same message. So there is **no flag combination** that auto-resumes and
  falls back to a fresh session; an existence check outside the CLI is
  required.
- **`--resume [name]` consumes a trailing positional.** `cmd "fix the bug"`
  would be read as "resume a session named `fix the bug`", not "start a
  session with message `fix the bug`". `--continue` takes no argument, so it
  does not swallow a positional.
- **Sessions live per project dir.** `~/.commandcode/projects/<slug>/` holds
  `<uuid>.jsonl` transcripts (plus `<uuid>.checkpoints.jsonl` and
  `<uuid>.meta.json` sidecars). The slug is `slugify(cwd) || "root"` via the
  bundled `@sindresorhus/slugify` v2 (same call the CLI makes internally:
  `getProjectDirName(cwd)`), verified against the real package output
  (`/home/kian/clusterfork` → `home-kian-clusterfork`, `/` → `root`,
  `CamelCase` → camel-case, `_` and `.` → `-`). Replicating v2's defaults in
  a ~8-line node snippet produced identical output on every case tested.
- **No non-interactive way to list sessions.** `cmd --help` has no
  list-sessions subcommand, and `--print`/`-p` refuses to touch resume state
  for the TUI (it manages headless print sessions separately).
- **Interactive mode requires a TTY.** `cmd ... | cat` exits with
  `Error: Interactive mode requires a TTY terminal.` So the "try `--continue`,
  catch the error, retry fresh" pattern cannot be tested or used from a
  pipeline/redirect, and a filesystem pre-check is the only clean gate.

## Fix that was built (then reverted)

A conditional gate in `cmd`:

- Bare `cmd` (no args) + a session exists for the current dir →
  `command cmd --continue --yolo`.
- Bare `cmd` + no sessions → `command cmd --yolo` (fresh session).
- `cmd "message"` → always fresh with the message (never resume-by-name).
- Existing `--yolo`/`--dangerously-skip-permissions` args pass through.

Session detection was a node one-liner: replicate `slugify` v2 defaults
(lowercase, decamelize, non-alphanumeric → `-`, collapse, trim, `|| "root"`),
then check `~/.commandcode/projects/<slug>/` for a `*.jsonl` that is not a
`*.checkpoints.jsonl`. Validated: real package vs replica slugs matched on
all tested paths; dispatch tested via a fake `cmd` in `PATH` for all four
argument shapes.

Complexity cost: ~8 lines of unavoidable conditional logic, plus ~15 lines
(the slug replica + session check) that only exist because Command Code has
no session-listing CLI. Importing the bundled slugify package instead of
replicating it would cut the lines but hardcodes an internal npm path that
breaks silently on update. The `--yolo` pass-through loop was pre-existing.

## Outcome

Decision 2026-08-18: **do not apply the fix.** `cmd` keeps the original
`--resume --yolo` behavior; start fresh from a session-free directory by
running `command cmd` directly (or `cmd "message"` plus `/clear`). If this
bites again, the fix above is validated and ready to re-apply — reinstall via
`install-clusterfork.sh` afterwards so `~/.config/clusterfork/shell/cmd.sh`
picks it up.
