---
name: commit_and_push
description: Commit and push staged changes to the current branch
---

# Commit and Push

## Instructions

1. Run `git status`, `git diff`, and `git diff --cached` to inspect unstaged and already-staged changes.
2. Run `git log -n 3` to match recent commit style (verbosity, formatting).
3. Propose a concise, focused commit message based on the diffs.
4. Stage only the intended files or hunks with `git add`, then review `git diff --cached` to confirm the commit includes only your changes.
5. Commit with the proposed message.
6. Run `git status` to confirm the intended changes were committed; unrelated changes may remain.
7. Push to the current branch on `origin`.
8. If the repo has CI that runs on push (and it will actually trigger on this push — the branch is watched and the repo has CI configured), make sure CI passes before reporting done. Diagnose failures and fix the root cause rather than working around them.

## Notes

- Keep commit messages clear and concise — focus on _why_, not _what_.
- Only stage, commit, and push changes you made for the current task. Leave the user's and other agents' changes untouched, including edits in the same file and already-staged changes. If you're unsure whether a change is yours or should be included, ask the user before proceeding.
- If nothing is staged and there are no modifications, tell the user instead of trying to commit.
