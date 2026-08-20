---
name: generate_docs
description: >
  Generate or regenerate the AGENTS.md file and docs/ directory from the
  current state of the repo. Reads all source files, creates one doc per
  topic with no repeated information, and writes a slim AGENTS.md that
  instructs agents to read and maintain docs/. Use when asked to
  "generate docs", "update docs", "regenerate docs", or "/generate_docs".
---

# Generate Docs

## Purpose

Produce an after-the-fact record of how the repo actually works, not a spec
for how it should work. The output is AI-generated documentation that is
accurate to the implementation.

## Instructions

1. **Check for existing files.** Before reading or writing anything, check
   the repo root for `AGENTS.md`, `CLAUDE.md`, and `docs/`.
   - Proceed without asking when nothing blocks a regeneration: none of them
     exist, or the existing `docs/README.md` has the AI-generated Notes
     section (step 6) — meaning the docs are this skill's own earlier
     output — and `CLAUDE.md` is absent or already a symlink.
   - Otherwise something may be hand-written (a regular-file `CLAUDE.md`, or
     `AGENTS.md`/`docs/` without that Notes section). Stop and present a
     brief summary of what exists — list the files with a one-line note on
     each — then ask the user how they would like to proceed (overwrite,
     back up first, cancel, etc.). Wait for their response before continuing.

2. **Read everything that isn't ignored.** Read every source file in the repo
   that git does not ignore (`git ls-files -co --exclude-standard` lists
   them) — code, shell scripts, config files, existing docs, AGENTS.md, and
   README.md. Do not skip files. Do not assume content from filenames. Do
   not read gitignored files: they can hold secrets (`.env`, saved
   credentials), and no credential or token value may ever appear in the
   docs.

3. **Generate AGENTS.md.** Write a slim AGENTS.md: minimal **domain** content,
   but explicit **process** instructions for using docs. Do not paste topic
   detail from `docs/` into AGENTS.md.

   Use this structure (adapt only the project one-liner; keep the Documentation
   section wording stable so regenerations stay consistent):

   ```markdown
   # AGENTS.md

   <one sentence: what this project is>

   ## Documentation (read/write)

   - **Read first:** For how this repo works, start at `docs/README.md`, then
     open only the topic files you need. Do not re-derive behavior from
     filenames alone when a doc covers it.
   - **Before non-trivial changes:** Check the relevant doc so you match
     existing patterns.
   - **After behavior changes:** Update the **one** topic file under `docs/`
     that owns that fact. Do not copy the same detail into AGENTS.md,
     README.md, or multiple docs.
   - **Regenerate vs patch:** `docs/README.md` records the commit it was
     generated from; `git diff <sha>..HEAD --stat` shows what changed since.
     Prefer the `generate_docs` skill when docs are broadly stale or many
     topics shifted; for small, targeted edits, patch the single topic file
     (and `docs/README.md` if you add/remove a topic).
   - `CLAUDE.md` is a symlink to this file.

   Do not put implementation detail in this file — it lives under `docs/`.
   ```

   Replace `<one sentence: what this project is>` with a single accurate
   sentence from what you read. Nothing else from the docs belongs here.

4. **Create CLAUDE.md symlink.** Create a symlink `CLAUDE.md → AGENTS.md` in
   the repo root so Claude Code picks up the same instructions. If CLAUDE.md
   is already a symlink, recreate it to ensure it points to AGENTS.md. Never
   replace a regular-file CLAUDE.md unless the user chose that in step 1.

5. **Plan doc topics.** Identify distinct topics from what you read. Each topic
   gets its own file under `docs/`. Topics should be concrete and scoped —
   "installation", "shell-modules", "auth-rotation", etc. If something was
   tried but not shipped (experiments, dead-ends, reverted features), it gets
   its own doc file too.

6. **Write docs/README.md.** Create an index listing every doc file with a
   short em-dash description.

   Below the index, write a generation stamp on its own line:

   ```markdown
   _Generated from commit `<short-sha>` on <YYYY-MM-DD>._
   ```

   Use `git rev-parse --short HEAD` for the SHA. Always rewrite this line on a
   regeneration — it is the staleness signal, and a stale stamp is worse than
   no stamp. It is what lets `git diff <sha>..HEAD --stat` answer "have these
   docs drifted?"

   Then a Notes section. If the existing docs/README.md already has a Notes
   section, include it verbatim — do not rewrite it. Otherwise write one
   explaining:
   - Docs are AI-generated, after-the-fact, implementation-accurate not
     design-accurate.
   - Information should not be repeated anywhere else; each topic lives in
     exactly one file.
   - Experiments and dead-ends should get their own doc files.

7. **Write one file per topic.** Each doc file covers exactly one topic.
   Information must not be repeated across files. If a detail belongs to two
   topics, pick the better fit and reference it from the other file.

   Keep each file under ~400 lines. Agents are told to open only the topic
   files they need, so a file too large to open cheaply gets skipped or eats
   the context budget. Past that size, split by sub-topic and list each part
   in `docs/README.md`.

## Notes

- Re-running this skill is a regeneration, not an append — the same repo
  state should produce equivalent output.
- AGENTS.md holds process rules for reading/writing docs; topic facts live
  only under `docs/`. Do not grow AGENTS.md into a second docs tree.
