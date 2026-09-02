# Codex rewind is broken in 0.147.0 (not clusterfork)

Investigated 2026-08-16. Not a clusterfork bug — recorded here so the next
person who hits it does not re-derive it.

## Symptom

In the Codex TUI, pressing Esc-Esc with an empty composer, selecting an
earlier prompt, and pressing Enter fails instead of branching:

```text
■ Failed to branch before the selected prompt: the selected prompt was not found in the persisted thread
```

The prompt text is restored to the composer, but no branch is created. It
fails for every prompt, not just old ones.

## Why it is not clusterfork

Clusterfork now writes the model default, MCP roster, and hook tables in
`~/.codex/config.toml`, installs skills, manages shared auth links, and owns
the `cc` wrapper. None of those paths writes history, thread items, or
compaction state, and the error string is compiled into the `codex` binary
itself:

```bash
strings ~/.codex/packages/standalone/current/bin/codex | grep 'persisted thread'
```

## Upstream cause

A regression in **codex-cli 0.147.0**, introduced by the "paginated threads"
rework that speeds up resume on long sessions. Reported as
[openai/codex#37421](https://github.com/openai/codex/issues/37421); an OpenAI
maintainer confirmed the cause and said it would be fixed in the next release.
There is no workaround.

The mechanism, confirmed locally and matching the duplicate report
[openai/codex#38871](https://github.com/openai/codex/issues/38871): the
transcript overlay identifies a prompt by the `msg_...` id in the rollout
JSONL under `~/.codex/sessions/`, while the persisted thread stores that same
prompt under a *different* id in `~/.codex/thread_history_1.sqlite`
(`thread_items.item_id`). The branch lookup uses the visible id, so it never
resolves.

Sample from a local thread (`019ffa34-2cfb-7a80-91d0-854912349088`):

| prompt | rollout JSONL id | persisted `thread_items` id |
|---|---|---|
| `k go ahead` | `msg_01a00caa-01ed-7ee0-a87a-b8676c036c87` | `01a00caa-01ef-7783-96ed-8b1583b3b36b` |
| `whichever is easier dude` | `msg_01a00ca1-dc48-7af3-90c2-01c697c19cbb` | `01a00ca1-dc49-78a3-90b6-8123e488f55d` |
| `k, whats next` | `msg_01a00cb4-f66c-7871-bbc9-87f16be01840` | `01a00cb4-f66d-7f93-921b-178e3ec38ad3` |

Every prompt mismatches, which is why the failure is total rather than
occasional. Affected threads carry `history_mode = 'paginated'` in
`~/.codex/state_5.sqlite` (`threads` table); the pre-0.147 mode was `legacy`,
and there is no supported config or feature flag to switch back —
`codex features list` exposes none, and `paginated_history` is internal.

Two things that look like causes but are not:

- **Auto-compaction.** A thread that has compacted many times still lists all
  its turns over the app-server (`thread/turns/list` returned all 34 turns of
  a 9-compaction thread), and its user messages still carry valid turn ids.
- **Resuming rather than starting fresh.** `cc` resumes, so a single thread
  can run for days and every rewind attempt in it hits the bug, but a fresh
  `codex` session on 0.147.0 fails the same way.

## Fix

Fixed upstream in **0.148.0**. At investigation time on 2026-08-16 the latest
stable release was still 0.147.0, so `codex update` did not help; the fix was
only in the `0.148.0-alpha.*` line (alpha.12 or later, per the maintainer).

The standalone install lays out as
`~/.codex/packages/standalone/releases/<version>-x86_64-unknown-linux-musl/bin/`
with a `current` symlink beside it, so an alpha can be dropped in from the
release's `codex-x86_64-unknown-linux-musl.tar.gz` and selected by repointing
`current` — leaving 0.147.0 on disk to roll back to. npm publishes the same
build under the `alpha` dist-tag.

Decision on 2026-08-16: **do not install the alpha, wait for 0.148.0 stable.**

## Investigation notes

`codex app-server` speaks JSON-RPC over stdio and is the fastest way to read
thread state without touching the model. `initialize` must declare
`capabilities.experimentalApi = true` or `thread/turns/list` and
`thread/items/list` are rejected. `codex app-server generate-json-schema
--experimental --out <dir>` dumps every method's params. Read-only calls are
safe to run against a thread that has a live TUI attached.
