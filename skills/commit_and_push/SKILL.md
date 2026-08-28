---
name: commit_and_push
description: Commit and push staged changes to the current branch
---

# Commit and Push

## Instructions

1. Run `git status` and `git diff HEAD` to see what has changed.
2. Run `git log -n 3` to match recent commit style (verbosity, formatting).
3. Propose a concise, focused commit message based on the diffs.
4. Stage all relevant files with `git add`.
5. Commit with the proposed message.
6. Run `git status` to confirm the commit succeeded and nothing is left behind.
7. Push to the current branch on `origin`.
8. If the repo has CI that runs on push (and it will actually trigger on this push — the branch is watched and the repo has CI configured), make sure CI passes before reporting done. Diagnose failures and fix the root cause rather than working around them; do not skip this for pushes where CI won't run.

## Notes

- Keep commit messages clear and concise — focus on _why_, not _what_.
- Do not commit unrelated or unintended changes.
- If nothing is staged and there are no modifications, tell the user instead of trying to commit.
