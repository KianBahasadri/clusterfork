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

1. **Read everything.** Read every source file in the repo — shell scripts,
   config files, Python scripts, the installer, existing docs, AGENTS.md, and
   README.md. Do not skip files. Do not assume content from filenames.

2. **Generate AGENTS.md.** Write a minimal AGENTS.md: one line describing the
   project, one line pointing to `docs/`. No detail, no repeated info.

3. **Create CLAUDE.md symlink.** Create a symlink `CLAUDE.md → AGENTS.md` in
   the repo root so Claude Code picks up the same instructions. If the symlink
   already exists, recreate it to ensure it points to AGENTS.md.

4. **Plan doc topics.** Identify distinct topics from what you read. Each topic
   gets its own file under `docs/`. Topics should be concrete and scoped —
   "installation", "shell-modules", "auth-rotation", etc. If something was
   tried but not shipped (experiments, dead-ends, reverted features), it gets
   its own doc file too.

5. **Write docs/README.md.** Create an index listing every doc file with a
   short em-dash description. Include the Notes section verbatim from the
   existing docs/README.md if one already exists — do not rewrite it.

6. **Write one file per topic.** Each doc file covers exactly one topic.
   Information must not be repeated across files. If a detail belongs to two
   topics, pick the better fit and reference it from the other file.

7. **Preserve the Notes section.** The docs/README.md must always contain the
   Notes section explaining:
   - Docs are AI-generated, after-the-fact, implementation-accurate not
     design-accurate.
   - Information should not be repeated anywhere else.
   - Experiments and dead-ends should get their own doc files.

8. **Do not create files for things that don't exist.** Only document what's
   actually in the repo. If a file or feature isn't present, don't invent it.

## Notes

- The docs describe what was done, not what was planned. They may drift from
  intent over time.
- Each topic lives in exactly one file. No repetition.
- Re-running this skill should produce equivalent output — it's a regeneration,
  not an append.
