---
name: ask-cursor
description: >
  Get help from Cursor Agent on difficult work of any kind — analysis, plans,
  decisions, writing, research, design, ops, or code. The agent invokes the
  Cursor Agent CLI headlessly (`cursor-agent --print`) and decides how to use
  it for the current task. Use when asked to "ask cursor", "cursor review",
  "second opinion from cursor", "/ask-cursor", or when a hard task needs
  Cursor as an independent check or collaborator.
---

# Ask Cursor

The user only runs `/ask-cursor` (optionally with a short note). **You** decide
how to use the Cursor Agent CLI from the conversation and task context. Do not
ask the user to pick modes, approval flags, models, or session options. A bare
call is not a preset — infer intent from what is going on.

Scope is general — not code-only. Use Cursor Agent for whatever the current
work needs: critique, open questions, tradeoffs, writing, research, design,
ops, debugging strategy, code, implementation, or continuation of prior
Cursor work.

## Your job

1. Infer from the conversation what Cursor should do for this moment.
2. Choose the right headless `cursor-agent` shape (new vs continue/resume,
   mode, approval posture, model) from context using the mechanics below.
3. Package a prompt that matches that choice and run the agent non-interactively.
4. Read the result, summarize for the user, surface disagreement when
   relevant, and continue the task when you still own finishing it.

If the user adds a note after the command (e.g. `/ask-cursor check the API
shape`), treat that as focus guidance — not a skill submenu.

## CLI mechanics (always)

Preconditions:

- `command -v cursor-agent` — if missing, try `agent` or `cursor agent` (all
  resolve to the same Cursor Agent binary on a clusterfork-managed host). If
  none are present, tell the user and stop.
- Non-interactive only: always `--print` (never open the interactive TUI from
  this skill).

Shared settings every call:

```text
--print --model cursor-grok-4.5-high --trust
```

The pinned default is **Grok 4.5 High** (`cursor-grok-4.5-high`). See
**Models** below for how to list models and switch when the user asks.

I/O pattern (file-first — the prompt is a positional arg, and `--print` mode
has read tools so a workspace brief can be read rather than stuffed into
argv). Write the brief **inside the workspace** so read-side judgment runs can
read it without hitting an external-path trust prompt:

```bash
REPO="$(pwd)"
PROMPT_FILE="$(mktemp "${REPO}/.ask-cursor-brief.XXXXXX")"
trap 'rm -f "$PROMPT_FILE"' EXIT
# write the brief to $PROMPT_FILE
cursor-agent --print --model cursor-grok-4.5-high --trust \
  --workspace "$REPO" \
  "Read the brief at $PROMPT_FILE and respond to it fully."
# add --yolo, --mode ask|--mode plan|--plan, --resume|--continue,
# --sandbox enabled|disabled from context
```

For small prompts, skip the file and pass the text inline as the positional
arg. Use `--output-format json` when you need the session id for `--resume`
(JSON success field is `session_id`) or machine parsing; default `text` is
fine otherwise.

Never put secrets, tokens, or credential file contents in the prompt.

## Decide how to invoke Cursor

Make choices from context. Do not surface them as user menus. There is no
bare-call default — pick what the situation needs.

### A. New session vs continue / resume

| Context points to **continue/resume** when… | Context points to a **new** session when… |
| --- | --- |
| You already used Cursor this task and need another turn on the same thread | First Cursor call for this task, or prior thread is irrelevant |
| The user is clearly continuing prior Cursor work in this repo | No useful prior session for this cwd |
| You need Cursor to keep memory of earlier analysis/edits | Continue/resume fails |

```bash
# Continue previous session (most recent chat for this workspace)
cursor-agent --print --model cursor-grok-4.5-high --trust \
  --workspace "$REPO" --continue "Read the brief at $PROMPT_FILE and respond."

# Resume a known session id (from a prior --output-format json run's session_id)
cursor-agent --print --model cursor-grok-4.5-high --trust \
  --workspace "$REPO" --resume "$SESSION_ID" "Read the brief at $PROMPT_FILE and respond."
```

If continue/resume fails, start a new session with the same intent and note it
in the summary.

### B. Mode and permissions

Cursor Agent has two read-only modes plus the default action mode:

| Context points to… | Consider… |
| --- | --- |
| Judgment only — critique, planning, Q&A, second opinion | `--mode ask` (read-only Q&A) or `--mode plan` / `--plan` (read-only planning/analysis, no edits); omit `--yolo`; instruct not to modify files |
| Implementation, fixes, multi-step edits, tool-heavy handoff | `--yolo` (alias `--force`: Run Everything / force-allow) so write and shell tools run unattended; `--sandbox disabled` if the sandbox blocks needed ops |
| Untrusted tree / tight containment | `--sandbox enabled` plus explicit instructions; avoid `--yolo` unless writes are actually intended |

CLI approval knobs for unattended runs are `--yolo`/`--force` vs omit (plus
read-only `--mode`). There is no `--auto-review` flag — `auto-review` is only
a config `approvalMode` value in `~/.cursor/cli-config.json` and can still
prompt, so it is unsuitable for this skill.

`--print` is designed for non-interactive use and has access to all tools;
`--trust` skips the workspace-trust prompt. For pure read-side judgment,
`--mode ask`/`--plan` plus `--trust` is enough to read a brief without further
approval. For any run that must write or run shell unattended, add `--yolo`.

```bash
# Judgment-oriented (read-only; no force-approve)
cursor-agent --print --model cursor-grok-4.5-high --trust --mode ask \
  --workspace "$REPO" \
  "Read the brief at $PROMPT_FILE and respond. Do not modify files."

# Implementation handoff (write/shell auto-approved)
cursor-agent --print --model cursor-grok-4.5-high --trust --yolo \
  --workspace "$REPO" \
  "Read the brief at $PROMPT_FILE and implement the requested changes."
```

### C. What to ask Cursor to do

| If context suggests… | Shape the prompt toward… |
| --- | --- |
| Stress-test a proposed answer / plan / analysis | Candid critique: strengths, risks, corrections, clear verdict |
| Open question, no finished work yet | Recommendation with reasoning and alternatives |
| High-stakes or high uncertainty | Challenge assumptions; include discarded alternatives |
| Large repo / long docs | Summary + map of paths; let Cursor read files with `--mode plan` or `--mode ask` (reserve `--yolo` for implementation) |
| Implementation handoff | Concrete deliverable + constraints + definition of done |
| Follow-up after prior Cursor work | Delta since last turn + next ask (shorter prompt is fine on resume) |

Prefer evidence over rubber-stamp agreement whenever you are asking for
judgment.

## Models

Default: `cursor-grok-4.5-high` (Cursor Grok 4.5, high reasoning effort). Keep
this unless the user asks for a different model/effort or context clearly
calls for it.

List available models for this account:

```bash
cursor-agent --list-models      # flag form, exits after printing
cursor-agent models             # subcommand form, same output
```

Switch the model with `--model <id>`. Cursor model ids carry the effort tier
in the name, e.g.:

- `cursor-grok-4.5-medium` / `cursor-grok-4.5-low` — same Grok 4.5, lower effort
- `gpt-5.6-sol-high` / `gpt-5.6-sol-xhigh` / `gpt-5.6-sol-max` — GPT-5.6 Sol tiers
- `claude-opus-5-thinking-high` / `claude-opus-5-thinking-xhigh` — thinking models
- `kimi-k3-high` — Kimi K3

Parameterized models also accept quoted bracket overrides for finer control:

```bash
cursor-agent --print --model 'claude-opus-4-8[context=1m,effort=high,fast=false]' --trust ...
```

If the user names a model you cannot map to an id, run `--list-models` and pick
the closest match; confirm only when the choice is genuinely ambiguous.

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

When a structured second opinion fits, ask Cursor to end with something
parseable, e.g.:

```text
VERDICT: AGREE|REVISE|REJECT
```

with concrete fixes under REVISE/REJECT. For implementation asks, request
what changed, remaining risks, and something like
`VERDICT: DONE|BLOCKED|PARTIAL`. Skip rigid verdict lines when a free-form
answer fits better.

## After Cursor responds

1. Read stdout (or parse JSON if you used `--output-format json`). On CLI
   failure (auth, network), report it honestly — do not invent a second opinion.
2. Summarize the useful findings for the user (not an unedited dump unless they
   want raw output).
3. **Surface disagreement** if you still think you are right; do not silently
   drop either view.
4. If you are still finishing the task and next steps are clear, take them
   (including adopting Cursor's edits after a `--yolo` run). If direction is
   ambiguous, present options and proceed.
5. Another Cursor turn is fine after big revisions; use `--continue` /
   `--resume` when that prior session is the one you want. Avoid endless ping-pong.
