---
name: ask-antigravity
description: >
  Get help from Antigravity (agy) on difficult work of any kind — analysis,
  plans, decisions, writing, research, design, ops, code, or image generation.
  The agent invokes the Antigravity CLI headlessly (`agy --print`) and decides
  how to use it for the current task. Use when asked to "ask antigravity",
  "ask agy", "antigravity review", "second opinion from antigravity",
  "/ask-antigravity", or when a hard task needs Antigravity as an independent
  check, collaborator, or image generator.
---

# Ask Antigravity

The user only runs `/ask-antigravity` (optionally with a short note). **You**
decide how to use the Antigravity CLI (`agy`) from the conversation and task
context. Do not ask the user to pick modes, permission flags, models, or
session options. A bare call is not a preset — infer intent from what is going
on.

Scope is general — not code-only. Use Antigravity for whatever the current work
needs: critique, open questions, tradeoffs, writing, research, design, ops,
debugging strategy, code, implementation, image generation/editing, or
continuation of prior Antigravity work.

## Your job

1. Infer from the conversation what Antigravity should do for this moment.
2. Choose the right headless `agy` shape (new vs continue/resume, mode,
   permissions, model) from context using the mechanics below.
3. Package a prompt that matches that choice and run Antigravity non-interactively.
4. Read the result, summarize for the user, surface disagreement when
   relevant, and continue the task when you still own finishing it.

If the user adds a note after the command (e.g. `/ask-antigravity focus on the
API shape`), treat that as focus guidance — not a skill submenu.

## CLI mechanics (always)

Preconditions:

- `command -v agy` — if missing, tell the user and stop. On a clusterfork host
  the shell alias `ag` is `agy --dangerously-skip-permissions`; prefer calling
  `agy` explicitly so this skill can choose permission posture per call.
- Non-interactive only: always `--print` / `-p` / `--prompt` (never open the
  interactive TUI from this skill).

Shared settings every call:

```text
--print ... --model gemini-3.6-flash-high --effort high
```

The pinned default is **Gemini 3.6 Flash (High)** (`gemini-3.6-flash-high`)
with reasoning effort `high`. See **Models** below for listing and switching.
Settings may also store human-readable names like `Gemini 3.6 Flash (High)`;
the hyphenated ids from `agy models` are preferred in scripts.

### Headless footguns (do not skip)

1. **Detach stdin.** Always end the command with `< /dev/null` (or otherwise
   detach stdin). Since 1.1.2, truly headless runs fail fast with an actionable
   message instead of blocking, but detaching stdin keeps scripts deterministic
   and avoids the OAuth-code terminal fallback.
2. **File-first briefs.** Prefer a brief file inside the workspace and a short
   “read this path” print prompt — avoids `ARG_MAX` and shell-escaping pain.
3. **Cwd is the workspace.** There is no `--cwd` flag. `cd` into the real
   workspace root before launching. Use `--add-dir PATH` (repeatable) only for
   extra directories beyond that root.
4. **Unattended tools need permission bypass.** Print mode cannot show approval
   prompts. Any call that must use tools (writes, shell, web, **image gen**)
   needs `--dangerously-skip-permissions`. Without it, permissioned tools are
   soft-denied headless (fail closed — no hang, but the work does not happen).

I/O pattern:

```bash
REPO="$(pwd)"   # real workspace root for the task
PROMPT_FILE="$(mktemp "${REPO}/.ask-antigravity-brief.XXXXXX")"
trap 'rm -f "$PROMPT_FILE"' EXIT
# write the brief to $PROMPT_FILE
cd "$REPO"
agy --print "Read the brief at $PROMPT_FILE and respond to it fully." \
  --model gemini-3.6-flash-high --effort high \
  --print-timeout 10m \
  < /dev/null
# add --dangerously-skip-permissions, --mode plan|accept-edits,
# --continue / --conversation, --add-dir, --output-format from context
```

For small prompts, skip the file and pass the text as the `--print` value.

Use `--output-format json` when you need the conversation id for resume
(JSON field is `conversation_id`) or machine parsing; default `text` is fine
otherwise. Default print timeout is `5m0s` — raise `--print-timeout` (e.g.
`10m` / `15m`) for implementation or image work that may run longer.

Never put secrets, tokens, or credential file contents in the prompt.

## Decide how to invoke Antigravity

Make choices from context. Do not surface them as user menus. There is no
bare-call default — pick what the situation needs.

### A. New session vs continue / resume

| Context points to **continue/resume** when… | Context points to a **new** session when… |
| --- | --- |
| You already used Antigravity this task and need another turn on the same thread | First Antigravity call for this task, or prior thread is irrelevant |
| The user is clearly continuing prior Antigravity work in this repo | No useful prior session for this cwd |
| You need Antigravity to keep memory of earlier analysis, edits, or images | Continue/resume fails |

```bash
# Continue the most recent conversation for this workspace
cd "$REPO"
agy --print "Read the brief at $PROMPT_FILE and respond." \
  --model gemini-3.6-flash-high --effort high --continue \
  < /dev/null
# add --dangerously-skip-permissions when the continued work must use tools

# Resume a known conversation id (from a prior --output-format json run)
cd "$REPO"
agy --print "Read the brief at $PROMPT_FILE and respond." \
  --model gemini-3.6-flash-high --effort high \
  --conversation "$CONVERSATION_ID" \
  < /dev/null
```

`-c` is the short alias for `--continue`. If continue/resume fails, start a
new session with the same intent and note it in the summary.

### B. Mode and permissions

| Context points to… | Consider… |
| --- | --- |
| Judgment only — critique, planning, Q&A, second opinion | `--mode plan` (strategize; no edits); omit `--dangerously-skip-permissions`; instruct not to modify files |
| File edits that should auto-apply without full tool YOLO | `--mode accept-edits` (auto-apply workspace file edits; narrower than full skip-permissions) plus instructions to keep edits inside the tree |
| Implementation, shell, web, multi-step tools, **image generation** | `--dangerously-skip-permissions` so tools actually run unattended (same idea as the `ag` alias) |
| Untrusted tree / tight containment | `--sandbox` (terminal restrictions) plus explicit instructions; avoid skip-permissions unless tools are actually required |

```bash
# Judgment-oriented (plan mode; no permission bypass)
cd "$REPO"
agy --print "Read the brief at $PROMPT_FILE and respond. Do not modify files." \
  --model gemini-3.6-flash-high --effort high --mode plan \
  < /dev/null

# Implementation / tools / images (auto-approve)
cd "$REPO"
agy --print "Read the brief at $PROMPT_FILE and respond." \
  --model gemini-3.6-flash-high --effort high \
  --dangerously-skip-permissions \
  --print-timeout 15m \
  < /dev/null
```

### C. What to ask Antigravity to do

| If context suggests… | Shape the prompt toward… |
| --- | --- |
| Stress-test a proposed answer / plan / analysis | Candid critique: strengths, risks, corrections, clear verdict |
| Open question, no finished work yet | Recommendation with reasoning and alternatives |
| High-stakes or high uncertainty | Challenge assumptions; include discarded alternatives |
| Large repo / long docs | Summary + map of paths; let Antigravity read files in plan mode |
| Implementation handoff | Concrete deliverable + constraints + definition of done |
| Image / visual work | Explicit image brief (see **Image generation** below) |
| Follow-up after prior Antigravity work | Delta since last turn + next ask (shorter prompt is fine on resume) |

Prefer evidence over rubber-stamp agreement whenever you are asking for
judgment.

## Models

Default: `--model gemini-3.6-flash-high --effort high`. Keep this unless the
user asks for a different model/effort or context clearly calls for it.

List available models for this account:

```bash
agy models
```

Switch with `--model <id>`. Effort is a separate flag: `--effort low|medium|high`.
Ids currently look like:

- `gemini-3.6-flash-high` / `gemini-3.6-flash-medium` / `gemini-3.6-flash-low`
- `gemini-3.5-flash-high` / `gemini-3.5-flash-medium` / `gemini-3.5-flash-low`
- `gemini-3.1-pro-high` / `gemini-3.1-pro-low`
- `claude-sonnet-4-6` / `claude-opus-4-6-thinking`
- `gpt-oss-120b-medium`

If the user names a model you cannot resolve, run `agy models` and pick the
closest match; confirm only when the choice is genuinely ambiguous.

Image generation does **not** use the reasoning-model flag. The generative
image tool uses **Nano Banana 2** (fixed, not customizable via `--model`).

## Brief to send

Always self-contained enough for the chosen call:

1. **Task / goal** and success criteria
2. **Current work product** (answer, plan, draft, design, decision, code) — or
   the open question if there is none yet
3. **Your reasoning** — assumptions, alternatives, uncertainties
4. **Constraints** — hard requirements, non-goals, environment facts
5. **What you want back** — match the ask to the situation (critique, verdict,
   recommendation, alternative design, draft, implementation, images, etc.)

On continue/resume, the prompt can be shorter (prior session context exists):
say what changed and what to do next.

When a structured second opinion fits, ask Antigravity to end with something
parseable, e.g.:

```text
VERDICT: AGREE|REVISE|REJECT
```

with concrete fixes under REVISE/REJECT. For implementation asks, request
what changed, remaining risks, and something like
`VERDICT: DONE|BLOCKED|PARTIAL`. Skip rigid verdict lines when a free-form
answer fits better.

## Image generation

Antigravity can create and edit images via the built-in **`generate_image`**
tool (Nano Banana 2). When the task needs visuals — or the user asks
Antigravity for images — put an explicit image brief in the prompt and run
headless with tool approval enabled (`--dangerously-skip-permissions`).

### Tool shape (instruct Antigravity to use this)

| Arg | Role |
| --- | --- |
| `Prompt` | Required. Full prose description of the image (or of the edit) |
| `ImageName` | Required. All lowercase with underscores, max 3 words (e.g. `login_page_mockup`); Antigravity chooses storage |
| `ImagePaths` | Optional. Absolute path(s) to existing image(s) for **editing** or as references; max 3 images |
| `AspectRatio` | Optional. One of `1:1` (default), `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9` |

The agent calls `generate_image` itself; you do not call a separate image CLI.

| Need | Instruct Antigravity to… |
| --- | --- |
| Brand-new image, no source | Call `generate_image` with `Prompt` + `ImageName` only |
| Edit / restyle / iterate on an existing image | Call `generate_image` with `ImagePaths` set to the source absolute path(s) and a `Prompt` that states what changes and what must stay |
| UI mockup, app asset, architecture diagram | Same tool — describe the visual deliverable clearly |
| Multiple variations | Multiple separate `generate_image` calls with distinct prompts/names |
| Exact text, numbers, charts, diagrams where labels must be pixel-perfect | Prefer building the asset in code (HTML/CSS/SVG) rather than image models — say so in the brief when accuracy of labels/data matters |

### What to put in the image brief

Include:

1. **Subject and action** — who/what, doing what
2. **Setting** — place, time, environment
3. **Style** — photo, illustration, UI mock, diagram, product shot, etc.
4. **Composition / framing** — camera angle, crop, layout
5. **Lighting and mood** (when relevant)
6. **Aspect ratio** via `AspectRatio` (`1:1` default; also `2:3`, `3:2`, `3:4`,
   `4:3`, `9:16`, `16:9`) when a non-square image is needed; add framing prose
   (e.g. square logo, wide banner) only if the ratio alone is ambiguous
7. **Output expectations** — report the absolute saved path(s); optionally copy
   into a workspace path the user cares about
8. **References** — absolute paths for edits via `ImagePaths`

Write prompts as short natural prose (about 2–5 sentences), front-loading the
subject. Describe what to include, not long negative lists.

### Where files land

Generated images are saved under the conversation brain directory, typically:

```text
~/.gemini/antigravity-cli/brain/<conversationId>/<ImageName>_<timestamp>.<ext>
```

(Artifacts may also appear as conversation artifacts.) Always trust the path
Antigravity reports after the tool runs. If the user needs the file in a
project path, copy or move it there yourself after the run.

### Example headless image ask

```bash
cat > "$PROMPT_FILE" <<'PROMPT'
Generate one image with the generate_image tool.

ImageName: ceramic_mug_desk

Prompt: A ceramic coffee mug on a sunlit oak desk beside an open notebook,
shallow depth of field, warm morning light, quiet product-photo style.

Do not modify other project files. After generation, reply with the absolute
saved image path(s) and a one-line description of what you produced.
PROMPT

cd "$REPO"
agy --print "Read the brief at $PROMPT_FILE and respond to it fully." \
  --model gemini-3.6-flash-high --effort high \
  --dangerously-skip-permissions \
  --print-timeout 10m \
  --output-format json \
  < /dev/null
```

### Example edit ask

```text
Use generate_image to edit /absolute/path/to/source.png.
Set ImagePaths to that path. ImageName: source_dusk_bg.
Prompt: Change only the background to a soft gradient dusk sky; keep the
subject, pose, and framing identical.
Reply with the absolute output path.
```

### After images come back

1. Note the absolute path(s) Antigravity reports (under the brain dir or
   wherever the reply gives).
2. If the user needs the files in a specific project location, copy or move
   them there yourself after the run.
3. If generation failed (quota, auth, moderation, tool error), report that
   honestly; do not claim an image exists.

## After Antigravity responds

1. Read stdout (or `.response` / the printed body from JSON). On CLI failure
   (auth, network, quota), report it honestly — do not invent a second opinion
   or image. Parse `conversation_id` from `--output-format json` when a
   follow-up is likely.
2. Summarize the useful findings for the user (or paths of generated assets).
   Raw dump only if asked.
3. **Surface disagreement** if you still think you are right; do not silently
   drop either view.
4. If you are still finishing the task and next steps are clear, take them
   (including adopting Antigravity’s edits after a write-capable run). If
   direction is ambiguous, present options and proceed.
5. Another Antigravity turn is fine after big revisions; use `--continue` /
   `--conversation` when that prior session is the one you want. Avoid
   endless ping-pong.
