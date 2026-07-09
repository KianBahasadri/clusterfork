---
name: ask-grok
description: >
  Get help from Grok on difficult work of any kind — analysis, plans,
  decisions, writing, research, design, ops, code, or image generation. The
  agent invokes the Grok CLI headlessly and decides how to use it for the
  current task. Use when asked to "ask grok", "grok review", "second opinion
  from grok", "/ask-grok", or when a hard task needs Grok as an independent
  check, collaborator, or image generator.
---

# Ask Grok

The user only runs `/ask-grok` (optionally with a short note). **You** decide
how to use the Grok CLI from the conversation and task context. Do not ask the
user to pick modes, sandboxes, or session options. A bare call is not a
preset — infer intent from what is going on.

Scope is general — not code-only. Use Grok for whatever the current work
needs: critique, open questions, tradeoffs, writing, research, design, ops,
debugging strategy, code, implementation, image generation/editing, or
continuation of prior Grok work.

## Your job

1. Infer from the conversation what Grok should do for this moment.
2. Choose the right headless `grok` shape (new vs continue/resume, sandbox,
   tool/permission posture, output format) from context using the mechanics
   below.
3. Package a prompt that matches that choice and run Grok non-interactively.
4. Read the result, summarize for the user, surface disagreement when
   relevant, and continue the task when you still own finishing it.

If the user adds a note after the command (e.g. `/ask-grok focus on the API
shape`), treat that as focus guidance — not a skill submenu.

## CLI mechanics (always)

Preconditions:

- `command -v grok` — if missing, tell the user and stop
- Non-interactive / headless only (never open the interactive TUI from this
  skill)

Headless is triggered by any of: `-p` / `--single`, `--prompt-file`, or
`--prompt-json`. Prefer a prompt file for long briefs (no shell-escaping pain).

Neutral skeleton (add flags from context — this is not a bare-call preset):

```bash
REPO="$(pwd)"   # real workspace root for the task
PROMPT_FILE="$(mktemp /tmp/ask-grok-prompt.XXXXXX)"
trap 'rm -f "$PROMPT_FILE"' EXIT
# write brief to $PROMPT_FILE
grok --prompt-file "$PROMPT_FILE" --cwd "$REPO" --effort xhigh \
  --output-format plain
# attach --always-approve, --sandbox, --tools, -c/-r, etc. only when context needs them
```

Useful flags (pick from context; none of these is a bare-call default):

| Flag | Role |
| --- | --- |
| `--prompt-file PATH` | Prompt from file (preferred for large briefs) |
| `-p` / `--single "..."` | Inline single-turn prompt |
| `--cwd PATH` | Workspace root for the session |
| `--always-approve` | Auto-approve tool use (needed when Grok must edit, run commands, or generate images unattended). Alias often documented as `--yolo` |
| `--effort` / `--reasoning-effort` | e.g. `xhigh`, `high`, `medium`, `low`, `max` |
| `--output-format plain\|json\|streaming-json` | `json` when you need `sessionId` or machine parsing |
| `-c` / `--continue` | Continue the most recent session for this cwd |
| `-r` / `--resume ID` | Resume a specific session |
| `--sandbox PROFILE` | e.g. `read-only`, `workspace`, `strict`, `off` |
| `--tools` / `--disallowed-tools` | Restrict which built-in tools Grok may use |
| `-m` / `--model` | Model id if you need a non-default model |

I/O notes:

- Headless does **not** read piped stdin as the prompt — use `--prompt-file` or
  `-p`, not `grok < file`.
- Default stdout is the final reply (`plain`). Use `json` and parse `.text` /
  `.sessionId` when you need resume or scripting.
- Capture session id when a follow-up is likely:
  `grok ... --output-format json` → `sessionId`.

Never put secrets, tokens, or credential file contents in the prompt.

## Decide how to invoke Grok

Make choices from context. Do not surface them as user menus. There is no
bare-call default — pick what the situation needs.

### A. New session vs continue / resume

| Context points to **continue/resume** when… | Context points to a **new** session when… |
| --- | --- |
| You already used Grok this task and need another turn on the same thread | First Grok call for this task, or prior thread is irrelevant |
| The user is clearly continuing prior Grok work in this repo | No useful prior session for this cwd |
| You need Grok to keep memory of earlier analysis, edits, or images | Continue/resume fails |

```bash
# Continue most recent session for this cwd
grok --prompt-file "$PROMPT_FILE" --cwd "$REPO" -c --effort xhigh
# add --always-approve when the continued work must use tools unattended

# Resume a known session id (from a prior --output-format json run)
grok --prompt-file "$PROMPT_FILE" --cwd "$REPO" --resume "$SESSION_ID" \
  --effort xhigh
```

If continue/resume fails, start a new session with the same intent and note
that in the summary.

### B. Permissions, tools, and sandbox

| Context points to… | Consider… |
| --- | --- |
| Judgment only (critique, planning, Q&A) | Omit `--always-approve` unless tools are required; narrower `--tools` and/or `--sandbox read-only`; pass “do not modify files” when you want pure opinion |
| Implementation, fixes, multi-step edits | `--always-approve` so tools can run; sandbox `workspace` or off as appropriate |
| Image generation or editing | `--always-approve` so `image_gen` / `image_edit` can run; do **not** strip those tools |
| Untrusted tree or tight containment | `--sandbox strict` or `read-only` plus explicit instructions |

Example tool narrowing for a pure read-side review (only when that matches
intent):

```bash
grok --prompt-file "$PROMPT_FILE" --cwd "$REPO" --effort xhigh \
  --always-approve \
  --tools "read_file,grep,list_dir,web_search,web_fetch"
```

Any invocation that expects tools (even a read-only allowlist) needs
`--always-approve` so unattended runs do not hang on approval prompts.

### C. What to ask Grok to do

Shape the brief to the need, for example:

| If context suggests… | Shape the prompt toward… |
| --- | --- |
| Stress-test a proposed answer / plan / analysis | Candid critique: strengths, risks, corrections, clear verdict |
| Open question, no finished work yet | Recommendation with reasoning and alternatives |
| Implementation handoff | Concrete deliverable, constraints, paths, definition of done |
| Image / visual work | Explicit image brief (see **Image generation** below) |
| Follow-up after prior Grok work | Delta since last turn + next ask (shorter prompt is fine) |

When a structured second opinion fits, ask Grok to end with something
parseable, e.g. `VERDICT: AGREE|REVISE|REJECT`. Skip rigid formats when
free-form is better.

## Brief to send

Always self-contained enough for the chosen call:

1. **Task / goal** and success criteria  
2. **Current work product** or open question  
3. **Your reasoning** — assumptions, alternatives, uncertainties  
4. **Constraints** — hard requirements, non-goals, environment facts  
5. **Instruction matching the call** — critique, recommend, implement,
   generate images, or continue from last turn  

On continue/resume, the prompt can be shorter: what changed and what to do
next.

## Image generation

Grok can create and edit images via its Imagine tools. When the task needs
visuals — or the user asks Grok for images — put an explicit image brief in
the prompt and run headless with tool approval enabled (`--always-approve`).

### Tell Grok which tool posture to use

| Need | Instruct Grok to… |
| --- | --- |
| Brand-new image, no source | Use `image_gen` with a full prose prompt and a suitable `aspect_ratio` |
| Edit / restyle / iterate on an existing image | Use `image_edit` with the source path(s) and a prompt that states what changes and what must stay |
| Named real person / likeness | Prefer `image_edit` with a real reference image; do not invent a likeness from a name alone |
| Multiple variations | Multiple separate generations with distinct prompts (no batch `n` count) |
| Exact text, numbers, charts, diagrams | Prefer building the asset in code (HTML/CSS etc.) rather than image models — say so in the brief when accuracy of labels/data matters |

### What to put in the image brief

Include:

1. **Subject and action** — who/what, doing what  
2. **Setting** — place, time, environment  
3. **Style** — photo, illustration, 3D, editorial, etc.  
4. **Composition / framing** — camera angle, crop, layout  
5. **Lighting and mood**  
6. **Aspect ratio** — e.g. `1:1` avatar, `16:9` banner, `9:16` story  
7. **Output expectations** — report saved path(s); keep files in the workspace  
8. **References** — absolute paths to source images for edits, if any  

Write prompts as short natural prose (about 2–5 sentences), front-loading the
subject. Describe what to include, not long negative lists.

Example headless ask (image-focused — note `--always-approve` here because
tools must run):

```bash
cat > "$PROMPT_FILE" <<'PROMPT'
Generate one image with image_gen.

Prompt: A ceramic coffee mug on a sunlit oak desk beside an open notebook,
shallow depth of field, warm morning light, quiet product-photo style.

Aspect ratio: 1:1

Do not modify other project files. After generation, reply with the saved
image path(s) and a one-line description of what you produced.
PROMPT

grok --prompt-file "$PROMPT_FILE" --cwd "$REPO" --always-approve --effort xhigh
```

Example edit ask:

```text
Use image_edit on /absolute/path/to/source.png.
Change only the background to a soft gradient dusk sky; keep the subject,
pose, and framing identical. Reply with the output path.
```

### After images come back

1. Note the paths Grok reports (often under a session `images/` area — use
   whatever paths the reply gives).
2. If the user needs the files in a specific project location, copy or move
   them there yourself after the run.
3. If generation failed (moderation, auth, tool error), report that honestly;
   do not claim an image exists.

## After Grok responds

1. Read stdout (or `.text` from JSON). On CLI failure, report it — do not
   invent a review or image.
2. Summarize useful findings (or paths of generated assets). Raw dump only if
   asked.
3. **Surface disagreement** when you still disagree; keep both views visible.
4. If you still own the task and next steps are clear, take them (including
   adopting Grok’s edits after an implementation run). If direction is
   ambiguous, present options and continue.
5. Another Grok turn is fine after big revisions; use `-c` / `--resume` when
   that prior session is the one you want. Avoid endless ping-pong.
