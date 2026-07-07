---
name: generate_docs
description: >
  Generate or regenerate the AGENTS.md file and docs/ directory from the
  current state of the repo. Reads all source files, creates one doc per
  topic with no repeated information, and writes a slim AGENTS.md that
  points to docs/. Use when asked to "generate docs", "update docs",
  "regenerate docs", or "/generate_docs".
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

3. **Generate AGENTS.md.** Write a minimal AGENTS.md: one line describing the
   project, one line pointing to `docs/`, and one line noting that `CLAUDE.md`
   is a symlink to this file. No detail, no repeated info.

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
   short em-dash description, followed by a Notes section. If the existing
   docs/README.md already has a Notes section, include it verbatim — do not
   rewrite it. Otherwise write one explaining:
   - Docs are AI-generated, after-the-fact, implementation-accurate not
     design-accurate.
   - Information should not be repeated anywhere else; each topic lives in
     exactly one file.
   - Experiments and dead-ends should get their own doc files.

7. **Write one file per topic.** Each doc file covers exactly one topic.
   Information must not be repeated across files. If a detail belongs to two
   topics, pick the better fit and reference it from the other file.

8. **Do not create files for things that don't exist.** Only document what's
   actually in the repo. If a file or feature isn't present, don't invent it.

## Notes

- Re-running this skill is a regeneration, not an append — the same repo
  state should produce equivalent output.
