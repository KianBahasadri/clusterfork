---
name: ask-claude
description: >
  Get help from Claude on difficult work of any kind — analysis, plans,
  decisions, writing, research, design, ops, or code. The agent invokes the
  Claude Code CLI and decides how to use it for the current task. Use when
  asked to "ask claude", "second opinion from claude", "claude review",
  "/ask-claude", or when a hard task needs Claude as an independent check or
  collaborator.
---

# Ask Claude

The user only runs `/ask-claude` (optionally with a short note). **You** decide
how to use the Claude CLI from the conversation and task context. Do not ask
the user to pick modes, flags, or templates. A bare call is not a preset —
infer intent from what is going on.

Scope is general — not code-only. Use Claude for whatever the current work
needs: critique, open questions, tradeoffs, writing, research, design, ops,
debugging strategy, code, drafting, or redo.

## Your job

1. Infer from the conversation what Claude should do for this moment.
2. Package enough context that Claude can act without the chat history.
3. Invoke Claude non-interactively with flags and a prompt shaped to that need.
4. Bring the result back: summarize, surface disagreement when relevant, and
   continue the task when you still own finishing it.

If the user adds a note after the command (e.g. `/ask-claude focus on
tradeoffs`), treat that as focus guidance, not as a skill submenu.

## CLI mechanics

Preconditions:

- `command -v claude` — if missing, tell the user and stop
- Non-interactive only: always `-p` / `--print` (never open the TUI)

Shared settings when high effort is appropriate:

```text
--effort xhigh
```

I/O pattern (file-first — avoids `ARG_MAX` from stuffing large briefs into
argv). Write the brief **inside the workspace** so judgment-only runs (no
permission bypass) can still read it without hanging on path approval:

```bash
REPO="$(pwd)"
PROMPT_FILE="$(mktemp "${REPO}/.ask-claude-brief.XXXXXX")"
trap 'rm -f "$PROMPT_FILE"' EXIT
# write the brief to $PROMPT_FILE
claude -p "Read the brief at $PROMPT_FILE and respond to it fully." \
  --effort xhigh
# add permission / session flags from context (see below)
```

Only use `claude -p "$(cat "$PROMPT_FILE")"` for small prompts when you want
zero file reads. For substantial briefs, keep the workspace file path.

Never put secrets, tokens, or credential file contents in the prompt.

## Decide how to invoke Claude

Make choices from context. There is no bare-call default.

### A. New session vs continue / resume

| Context points to **continue/resume** when… | Context points to a **new** session when… |
| --- | --- |
| You already used Claude this task and need another turn | First Claude call, or prior thread is irrelevant |
| User is clearly continuing prior Claude work in this repo | No useful prior session |

```bash
# Continue most recent conversation in this directory
claude -p "..." --effort xhigh -c

# Resume a known session
claude -p "..." --effort xhigh -r "$SESSION_ID"
```

If continue/resume fails, start a new session with the same intent and note it.

### B. Permissions

| Context points to… | Consider… |
| --- | --- |
| Judgment only (critique, planning, Q&A) | Omit `--dangerously-skip-permissions`; instruct Claude not to modify files; let it read the workspace as the CLI allows |
| Implementation, drafts, multi-step edits, or workspace tool use that would otherwise prompt | `--dangerously-skip-permissions` (same idea as the `cl` alias) so unattended runs do not hang on permission prompts |

```bash
# Judgment-oriented (no permission bypass; brief must live under $REPO)
claude -p "Read the brief at $PROMPT_FILE and respond. Do not modify files." \
  --effort xhigh

# Implementation / tool-heavy handoff
claude -p "Read the brief at $PROMPT_FILE and respond." \
  --effort xhigh --dangerously-skip-permissions
```

### C. What to ask Claude to do

| If context suggests… | Shape the prompt toward… |
| --- | --- |
| A proposed answer / plan / analysis needs stress-testing | Candid critique: strengths, risks, corrections, clear verdict |
| An open question with no finished work yet | Recommendation with reasoning and alternatives |
| High-stakes or high uncertainty | Challenge assumptions; include discarded alternatives |
| Large repo / long docs | Summary + map of paths; let Claude read files |
| Claude should draft or redo something | Clear deliverable + constraints; less “reviewer” frame |
| You already revised after Claude | Follow-up with the updated work; avoid endless loops |

Prefer evidence over rubber-stamp agreement whenever you are asking for
judgment.

## Brief to send

Always self-contained. Include what Claude needs for *this* ask:

1. **Task / goal** and success criteria  
2. **Current work product** (answer, plan, draft, design, decision, code) — or
   the open question if there is none yet  
3. **Your reasoning** — assumptions, alternatives, uncertainties  
4. **Constraints** — hard requirements, non-goals, environment facts  
5. **What you want back** — match the ask to the situation (critique, verdict,
   recommendation, alternative design, draft, etc.)

When a structured second opinion fits, ask Claude to end with something
parseable, e.g.:

```text
VERDICT: AGREE|REVISE|REJECT
```

with concrete fixes under REVISE/REJECT. Skip rigid verdict formats when a
free-form answer fits better.

## After Claude responds

1. Read the full reply; note CLI failures (auth, network) and report them
   honestly — do not invent a second opinion.
2. Summarize the useful findings for the user (not an unedited dump unless
   they want raw output).
3. **Surface disagreement** if you still think you are right; do not silently
   drop either view.
4. If you are still finishing the task and next steps are clear, take them.
   If direction is ambiguous, present options and proceed.
