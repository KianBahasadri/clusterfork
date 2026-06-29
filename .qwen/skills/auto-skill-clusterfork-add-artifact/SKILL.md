---
name: clusterfork-add-artifact
description: Add new config artifacts (skills, shell modules, JSON configs) to the clusterfork dotfile installer project
source: auto-skill
extracted_at: '2026-06-29T03:50:24.606Z'
---

# Adding artifacts to clusterfork

When adding a new config artifact (a Qwen skill, shell module, JSON settings file, etc.) to the `clusterfork` project, **never write directly to the dotfile destination** (e.g. `~/.qwen/skills/`). Instead:

1. **Create the artifact in the project repo** under the appropriate source directory:
   - Qwen skills → `skills/<name>/SKILL.md` (not hidden, not `.qwen/skills/`)
   - Shell modules → `shell/<name>.sh`
   - JSON configs → top-level `<name>.json`

2. **Wire it into `install-clusterfork.sh`**:
   - Add `*_SRC` / `*_DEST` variables at the top of the script with the other path definitions.
   - Add an install step (copy/mkdir) following the existing pattern (use `step` for the checkmark output, `fail` for error handling).
   - If it's a directory of items (like skills or shell modules), also add a listing section in the output summary.

3. **Update the header comment** listing numbered steps to include the new artifact.

4. **Commit and push** both the artifact and the updated installer in one commit.

This ensures the artifact is version-controlled, reproducible, and installed uniformly by running `./install-clusterfork.sh`.

## Source

From conversation on 2026-06-28: user asked to create a Qwen "commit" skill. First attempt wrote it directly to `~/.qwen/skills/commit/`. User corrected: put it in a non-hidden `skills/` directory inside the project and update the installer to copy it to `~/.qwen/skills/`.
