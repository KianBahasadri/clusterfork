# Command Code resume failure: retained behavior

Investigated 2026-08-18 with Command Code 1.27.1. A conditional fallback was
built and validated, then reverted by decision. The current launcher remains
documented in [Shell Modules](../shell-modules.md#launch-commands).

## Symptom

In a directory with no Command Code sessions, the wrapper's default
`--resume --yolo` printed `No conversations found to resume.` and exited.
The tested CLI had these semantics:

| Flag | Behavior in the investigated build |
| --- | --- |
| `--resume [name]` | Opened a session picker, or resumed the named session; could consume a positional prompt as the name |
| `--continue` | Continued the latest conversation, but also failed when none existed |
| No resume/continue flag | Started a fresh session |

There was no flag combining automatic resume with a fresh-session fallback.
`command cmd` bypasses the shell wrapper for a fresh session; `cmd --yolo`
also bypasses the wrapper's added resume flag.

## Unshipped fallback

The tested gate used `--continue --yolo` for a bare command only when a session
existed, otherwise `--yolo`; positional prompts always started fresh.
Existing permission-bypass flags were forwarded unchanged.

Sessions lived in `~/.commandcode/projects/<slug>/` as UUID JSONL files,
with checkpoints and metadata sidecars. A small Node check reproduced the
bundled `@sindresorhus/slugify` v2 rules (lowercase/decamelize, punctuation to
hyphens, `root` fallback) and looked for non-checkpoint transcripts.
The CLI offered no suitable headless session-listing path, and interactive
resume required a TTY. Fake-binary dispatch tests and real-package slug
comparisons validated that workaround.

It added version-sensitive session-layout/slug logic solely to avoid the
empty-directory failure. The decision was to retain the simpler wrapper.
If reconsidered, recheck the installed CLI rather than assuming those internals
still match 1.27.1.
