---
name: ask-opencode
description: >
  Get help from OpenCode on difficult work of any kind — analysis, plans,
  decisions, writing, research, design, ops, or code. The agent invokes
  `opencode run` headlessly and decides how to use it for the current task. Use
  when asked to "ask opencode", "opencode review", "second opinion from opencode",
  "/ask-opencode", or when a hard task needs OpenCode as an independent check or
  collaborator.
---

# Ask OpenCode

The user only runs `/ask-opencode` (optionally with a short note). **You**
decide how to use the OpenCode CLI from the conversation and task context. Do
not ask the user to pick models, variants, agents, or session options. A bare
call is not a preset — infer intent from what is going on.

Scope is general — not code-only. Use OpenCode for whatever the current work
needs: critique, open questions, tradeoffs, writing, research, design, ops,
debugging strategy, code, implementation, or continuation of prior OpenCode
work.

## Your job

1. Infer from the conversation what OpenCode should do for this moment.
2. Choose the right `opencode run` shape (new vs continue/resume, permissions,
   model/variant, agent) from context using the mechanics below.
3. Package a prompt that matches that choice and run OpenCode non-interactively.
4. Read the result, summarize for the user, surface disagreement when
   relevant, and continue the task when you still own finishing it.

If the user adds a note after the command (e.g. `/ask-opencode check the
migration plan`), treat that as focus guidance — not a skill submenu.

## CLI mechanics (always)

Preconditions:

- `command -v opencode` — if missing, tell the user and stop
- Non-interactive only: `opencode run` (never open the default TUI from this
  skill)

Shared settings every call:

```text
-m opencode-go/glm-5.2 --variant max
```

The pinned default is **GLM-5.2** at variant `max` (highest reasoning effort).
See **Models** below for how to list models/variants and switch when the user
asks.

I/O pattern — `opencode run` takes the message as positional args **or** reads
the prompt from stdin. Prefer stdin for substantial briefs (no ARG_MAX from
stuffing a large brief into argv):

```bash
REPO="$(pwd)"
PROMPT_FILE="$(mktemp /tmp/ask-opencode-prompt.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT
# write brief to $PROMPT_FILE
opencode run --dir "$REPO" -m opencode-go/glm-5.2 --variant max \
  < "$PROMPT_FILE"
# add --auto, --agent, -c/-s from context (see below)
```

For small prompts, pass the message as positional args instead:

```bash
opencode run --dir "$REPO" -m opencode-go/glm-5.2 --variant max "$MSG"
```

Permissions (headless): without `--auto`, pending permission asks are
**auto-rejected** (fail closed — no hang, but tools that need approval fail).
With `--auto`, any permission not explicitly denied is auto-approved
(`reply: "once"`) — the OpenCode analog of Codex's `approval_policy=never` for
the auto-approve case only. Use `--auto` when tools must actually run
unattended; omit it for judgment so writes/shell cannot sneak through. See
**Permissions** below. `--dir "$REPO"` sets the workspace root (defaults to
cwd otherwise). Default stdout is the final reply; use `--format json` when
you need the session id (JSON events carry `sessionID`) or machine-parsed
events.

Never put secrets, tokens, or credential file contents in the prompt.

## Decide how to invoke OpenCode

Make choices from context. Do not surface them as user menus. There is no
bare-call default — pick what the situation needs.

### A. New session vs continue / resume

| Context points to **continue/resume** when… | Context points to a **new** session when… |
| --- | --- |
| You already used OpenCode this task and need another turn on the same thread | First OpenCode call for this task, or prior thread is irrelevant |
| The user is clearly continuing prior OpenCode work in this repo | No useful prior session for this cwd |
| You need OpenCode to keep memory of earlier analysis/edits | Continue/resume fails |

```bash
# Continue the most recent session for this workspace
opencode run --dir "$REPO" -m opencode-go/glm-5.2 --variant max -c \
  < "$PROMPT_FILE"
# add --auto and/or --agent from context

# Resume a known session id (from a prior --format json run's sessionID)
opencode run --dir "$REPO" -m opencode-go/glm-5.2 --variant max \
  -s "$SESSION_ID" < "$PROMPT_FILE"
```

If continue/resume fails, start a new session with the same intent and note it
in the summary.

### B. Permissions and agent

OpenCode has no read-only sandbox flag on `run`; approval and write access are
gated by the agent's permission rules plus `--auto`. The default agent is
`build` (set in `agents/opencode.json` → `~/.config/opencode/opencode.json`),
which allows tool use. A built-in primary `plan` agent restricts edit/bash
(asks on write-oriented tools) and is the first-class read-oriented option.

| Context points to… | Consider… |
| --- | --- |
| Judgment only — critique, planning, Q&A, second opinion | `--agent plan` **without** `--auto` (permission asks auto-reject → no writes), or fully inline the brief and omit `--auto`; soft “do not modify” alone is not enough with `--auto` + default `build` |
| Implementation, fixes, multi-step edits, tool-heavy handoff | Default `build` (or leave agent unset) **with** `--auto` so tools actually run unattended; instruct to keep edits inside the workspace |
| Untrusted tree / tight containment | Prefer a non-`build` agent with tighter rules (`opencode agent list`); omit `--auto` unless tools must run, and scope the ask narrowly |

List available agents with `opencode agent list`; select one with
`--agent <name>`. When unsure for judgment, use `--agent plan` without
`--auto`; for implementation, leave default `build` and pass `--auto`.

```bash
# Judgment-oriented (plan agent; no auto-approve — asks fail closed)
opencode run --dir "$REPO" -m opencode-go/glm-5.2 --variant max \
  --agent plan \
  "Read the brief below and respond. Do not modify any files.

$BRIEF"

# Implementation handoff (tools auto-approved)
opencode run --dir "$REPO" -m opencode-go/glm-5.2 --variant max --auto \
  < "$PROMPT_FILE"
```

### C. What to ask OpenCode to do

| If context suggests… | Shape the prompt toward… |
| --- | --- |
| Stress-test a proposed answer / plan / analysis | Candid critique: strengths, risks, corrections, clear verdict |
| Open question, no finished work yet | Recommendation with reasoning and alternatives |
| High-stakes or high uncertainty | Challenge assumptions; include discarded alternatives |
| Large repo / long docs | Summary + map of paths; let OpenCode read files with `--agent plan` (omit `--auto`); use `--auto` only if tool-heavy work is intended |
| Implementation handoff | Concrete deliverable + constraints + definition of done |
| Follow-up after prior OpenCode work | Delta since last turn + next ask (shorter prompt is fine on resume) |

Prefer evidence over rubber-stamp agreement whenever you are asking for
judgment.

## Models

Default: `-m opencode-go/glm-5.2 --variant max` (GLM-5.2, `max` reasoning
effort). Keep this unless the user asks for a different model/effort or
context clearly calls for it.

List available models:

```bash
opencode models                      # all providers, simple id list
opencode models opencode-go          # one provider
opencode models opencode-go --verbose   # includes variants, context limit, cost
```

Model and reasoning effort are separate flags:

```bash
-m <provider/model> --variant <effort>
```

`--variant` is provider-specific reasoning effort — commonly `low`, `medium`,
`high`, `max`, and sometimes `minimal`. **Not every model supports every
variant**; check the `variants` block in the `--verbose` output before
switching. Examples available on this host's `opencode-go` provider:

- `opencode-go/glm-5.2` — variants: `high`, `max`
- `opencode-go/grok-4.5` — Grok 4.5
- `opencode-go/kimi-k3` — Kimi K3
- `opencode-go/qwen3.7-max` — Qwen 3.7 Max
- `opencode-go/deepseek-v4-pro` — DeepSeek V4 Pro

Switch when the user requests a different model or effort tier. If the user
names a model you cannot resolve, run `opencode models` and pick the closest
match; confirm only when the choice is genuinely ambiguous.

## Brief to send

Always self-contained enough for the chosen call:

1. **Task / goal** and success criteria
2. **Current work product** (answer, plan, draft, design, decision, code) — or
   the open question if there is none yet
3. **Your reasoning** — assumptions, alternatives, uncertainties
4. **Constraints** — hard requirements, non-goals, environment facts
5. **What you want back** — match the ask to the situation (critique, verdict,
   recommendation, alternative design, draft, implementation, etc.)

On continue/resume, the prompt can be shorter (prior session context exists):
say what changed and what to do next.

When a structured second opinion fits, ask OpenCode to end with something
parseable, e.g.:

```text
VERDICT: AGREE|REVISE|REJECT
```

with concrete fixes under REVISE/REJECT. For implementation asks, request
what changed, remaining risks, and something like
`VERDICT: DONE|BLOCKED|PARTIAL`. Skip rigid verdict lines when a free-form
answer fits better.

## After OpenCode responds

1. Read stdout (or parse JSON if you used `--format json`; resume id field is
   `sessionID`). On CLI failure (auth, network), report it honestly — do not
   invent a second opinion.
2. Summarize the useful findings for the user (not an unedited dump unless they
   want raw output).
3. **Surface disagreement** if you still think you are right; do not silently
   drop either view.
4. If you are still finishing the task and next steps are clear, take them
   (including adopting OpenCode's edits after an `--auto` run). If direction is
   ambiguous, present options and proceed.
5. Another OpenCode turn is fine after big revisions; use `-c` / `-s` when
   that prior session is the one you want. Avoid endless ping-pong.
