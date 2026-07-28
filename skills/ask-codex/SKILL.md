---
name: ask-codex
description: >
  Get help from Codex on difficult work of any kind — analysis, plans,
  decisions, writing, research, design, ops, or code. The agent invokes
  `codex exec` and decides how to use it for the current task. Use when asked
  to "ask codex", "codex review", "second opinion from codex", "/ask-codex",
  or when a hard task needs Codex as an independent check or collaborator.
---

# Ask Codex

The user only runs `/ask-codex` (optionally with a short note). **You** decide
how to use the Codex CLI from the conversation and task context. Do not ask
the user to pick modes, sandboxes, or session options. A bare call is not a
preset — infer intent from what is going on.

Scope is general — not code-only. Use Codex for whatever the current work
needs: critique, open questions, tradeoffs, writing, research, design, ops,
debugging strategy, code, implementation, or continuation of prior Codex work.

## Your job

1. Infer from the conversation what Codex should do for this moment.
2. Choose the right `codex exec` shape (new vs resume, sandbox, approval) from
   context using the mechanics below.
3. Package a prompt that matches that choice and run Codex non-interactively.
4. Read the output file, summarize for the user, surface disagreement when
   relevant, and continue the task when you still own finishing it.

If the user adds a note after the command (e.g. `/ask-codex check the
migration plan`), treat that as focus guidance — not a skill submenu.

## CLI mechanics (always)

Preconditions:

- `command -v codex` — if missing, tell the user and stop
- Non-interactive only: `codex exec` (never open the interactive TUI)

Shared settings every call:

```text
-m gpt-5.6-sol -c model_reasoning_effort=xhigh -c approval_policy=never
```

I/O pattern:

- Prompt on **stdin**: `- < "$PROMPT_FILE"`
- Last agent message to a file: `-o "$OUT_FILE"`
- After exit, read `"$OUT_FILE"` (and the process exit code)

```bash
REPO="$(pwd)"   # real workspace root for the task
PROMPT_FILE="$(mktemp /tmp/ask-codex-prompt.XXXXXX)"
OUT_FILE="$(mktemp /tmp/ask-codex-out.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT   # do not trap OUT_FILE — read it after the run
# write brief to $PROMPT_FILE, then run the chosen codex exec form
# read "$OUT_FILE", then: rm -f "$OUT_FILE"
```

**Approval for unattended runs:** non-interactive agent calls that may execute
tools should pass `-c approval_policy=never` so Codex does not hang waiting for
a human prompt this skill cannot answer.

Do **not** pass `-a never` / `--ask-for-approval never` as options *after*
`exec` — on current Codex CLI those flags are top-level only
(`codex -a never exec …`) and `codex exec -a never` fails. Prefer the
`-c approval_policy=never` form so the same shared flags work on both
`codex exec` and `codex exec resume`. Pair approval with the sandbox you
chose — they are separate knobs.

Never put secrets, tokens, or credential file contents in the prompt.

## Decide how to invoke Codex

Make the session, sandbox, and approval choices from context. Do not surface
them as user choices. There is no bare-call default — pick what the situation
needs.

### A. New session vs resume

| Context points to **resume** when… | Context points to a **new** session when… |
| --- | --- |
| You already used Codex this task and need another turn on the same thread | First Codex call for this task, or prior thread is irrelevant |
| The user is clearly continuing prior Codex work in this repo | No useful prior session for this cwd |
| You need Codex to keep memory of its earlier analysis/edits | Resume fails |

Resume constraints (verified on current `codex exec resume`):

- Prefer not to pass `-s` / `-C` on resume; if the CLI rejects them, set
  sandbox only via `-c 'sandbox_mode="..."'` and take cwd from the shell
  (`cd "$REPO"` first). `--last` is filtered by cwd.
- Still pass `-c approval_policy=never` when the run must not block on
  approval prompts.

```bash
# Resume (sandbox via -c; shell cwd = repo)
cd "$REPO"
codex exec resume --last -m gpt-5.6-sol -c model_reasoning_effort=xhigh \
  -c approval_policy=never -c 'sandbox_mode="read-only"' \
  -o "$OUT_FILE" - < "$PROMPT_FILE"
# or sandbox_mode="workspace-write" / "danger-full-access" from context
```

If resume fails, fall back to a new session with the same intent and note that
in the summary.

### B. Sandbox level

Codex sandboxes (new session: `-s`; resume: `-c 'sandbox_mode="..."'`):

| Context points to… | Sandbox |
| --- | --- |
| Judgment only: critique, planning, Q&A | `read-only` |
| Implementation / edits inside the workspace | `workspace-write` (usual pick for handoffs that should change the tree) |
| Needs broader FS/network or unrestricted shell | `danger-full-access` (only when the task actually requires it) |

New session forms:

```bash
# Read-only
codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh \
  -c approval_policy=never -s read-only \
  -C "$REPO" -o "$OUT_FILE" - < "$PROMPT_FILE"

# Workspace writes (typical implementation)
codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh \
  -c approval_policy=never -s workspace-write \
  -C "$REPO" -o "$OUT_FILE" - < "$PROMPT_FILE"

# Full access (only when broader access is required)
codex exec -m gpt-5.6-sol -c model_reasoning_effort=xhigh \
  -c approval_policy=never -s danger-full-access \
  -C "$REPO" -o "$OUT_FILE" - < "$PROMPT_FILE"
```

## Brief to send

Always self-contained enough for the chosen call:

1. **Task / goal** and success criteria  
2. **Current work product** or open question  
3. **Your reasoning** — assumptions, alternatives, uncertainties  
4. **Constraints** — hard requirements, non-goals, environment facts  
5. **Instruction matching the call** — critique, recommend, implement, or
   continue from last turn with this delta  

On **resume**, the prompt can be shorter (prior session context exists): say
what changed and what to do next.

When asking for structured critique, you can request strengths, risks,
concrete corrections, confidence, and a close such as:

```text
VERDICT: AGREE|REVISE|REJECT
```

When asking for implementation, you can request what changed, remaining
risks, and something like `VERDICT: DONE|BLOCKED|PARTIAL`.

Skip rigid verdict lines when a free-form answer fits better.

## After Codex responds

1. Read `"$OUT_FILE"`. On CLI failure, report it — do not invent a review.
2. Summarize useful findings (or what was implemented). Raw dump only if asked.
3. **Surface disagreement** when you still disagree; keep both views visible.
4. If you still own the task and next steps are clear, take them (including
   adopting Codex’s edits after a write-capable run). If direction is
   ambiguous, present options and continue.
5. Another Codex turn is fine after big revisions; use resume when that prior
   session is the one you want. Avoid endless ping-pong.
