---
name: create-github-action-tests
description: >
  Backfill characterization tests and wire them into free GitHub Actions CI
  on a public repo. Confirms Actions would be free (public repo, standard
  hosted runners), auto-rejects billed or unknown CI until the user explicitly
  approves, adds new test files only, proves each new test is deterministic,
  then adds a dedicated hardened workflow. Use when asked to "create github
  action tests", "backfill characterization tests", "add tests and GitHub
  Actions CI", "wire tests into CI", or "/create-github-action-tests".
---

# Create GitHub Action Tests

## Purpose

Add characterization tests — they lock in what the code actually does today,
not what it should do — plus a GitHub Actions workflow that reruns them on a
clean runner. Additive only: new test files and a workflow. Never edit source
to make a test pass.

## Instructions

1. **Confirm GitHub Actions would be free.** Proceed on your own only when the
   repo is public *and* the workflow will use standard GitHub-hosted runners.
   That combination is unlimited free Actions minutes. Anything else is billed
   or unknown: auto-reject and wait for explicit human approval.
   - Run `gh repo view --json visibility,nameWithOwner` in the repo. Proceed
     only when `visibility` is `PUBLIC` (case-insensitive).
   - Auto-reject — do not write a workflow, do not continue as if the gate
     passed — when visibility is `PRIVATE`, `INTERNAL`, or anything other than
     public; when there is no GitHub remote, `gh` is missing, or visibility
     cannot be determined; or when the user asks for larger runners, GPU or
     custom images, runner groups, or self-hosted labels.
   - On auto-reject, say what you found and that Actions would not be free.
     Ask them to choose: stop; or local characterization tests only (no
     workflow). If a GitHub repo exists and the issue is billed minutes (private
     / internal / billed runner), also offer: proceed with billed GitHub
     Actions (they accept the minutes). Do not treat a generic "continue" or
     the original invocation as approval. Wait for that choice.
   - After approval for billed CI, still use standard hosted runners unless
     they also explicitly request a billed runner label.

2. **Learn the repo before writing anything.** Read what git tracks
   (`git ls-files`) — source, existing tests, config, and any
   `.github/workflows/`. Do not read gitignored files: they can hold secrets
   (`.env`, credentials), and no secret value may ever land in a test, a
   fixture, or a workflow.
   - Prefer the repo's existing test command if one is declared (package
     scripts, Makefile, pyproject, already-green docs). Otherwise use that
     language's conventional runner. Examples, not a closed set: Python →
     pytest or stdlib `unittest`; Rust → `cargo test`; Go → `go test`; JS/TS →
     whatever `package.json` already declares, else `node --test`.
   - If tests already exist, match their framework, layout, and naming exactly.
     Do not introduce a second testing style.

3. **Pick a small, honest target.** Do not try to cover the whole repo. Choose
   the code that is cheap to pin down and worth pinning: pure functions,
   parsers, formatters, calculations, state machines — deterministic logic with
   clear inputs and outputs.
   - Deliberately skip, and say you skipped, anything that needs the network,
     real credentials, a display/GUI, a database, or wall-clock/random behavior
     — unless it can be driven with a fake or fixed seed cleanly and without
     touching source. Fork-triggered CI gets no secrets, so a test that needs a
     real credential cannot pass there anyway.
   - Zero good targets is a valid outcome. If the repo is a handful of trivial
     scripts with nothing deterministic worth locking, say so and stop rather
     than inventing filler tests.

4. **Write characterization tests — additive only.** New test files only. Never
   modify source to make it testable, and never modify source to make a test
   green.
   - **Characterization (what CI runs):** run the code, observe the real
     output, encode that as the expectation. These tests must be deterministic
     and green. A comment that current behavior looks wrong is fine; the test
     still asserts what the code does *now*.
   - **Intent mismatch (not CI):** if a test you wrote from apparent intent
     fails against actual behavior, that is a real bug. Do not add it to the
     committed suite, do not xfail it into a red or ignored job, and do not
     delete the evidence. Leave it out of CI (uncommitted snippet is fine) and
     surface it in step 8 so the user can choose lock-in vs fix.
   - Never delete a green characterization test to hide a bug.

5. **Prove each test is deterministic before it can stay.** If an existing
   suite is already red *before* any new files, report that and do not add a
   workflow. New characterization tests may still be written if they themselves
   pass; they must not become a CI job that pretends the repo is green.
   - Otherwise run the full suite once, then run the newly added tests several
     times in a row (e.g. a short loop of 5–10 runs). Keep only tests that pass
     on every run.
   - Drop or fix any test that is flaky or order-dependent. A flaky test trains
     everyone to ignore the CI badge, which destroys the whole point.
   - A red or flaky test committed as if it were green is a failure of this
     skill, not a finding.

6. **Add a minimal, hardened CI workflow.** Default to a new dedicated file
   `.github/workflows/test.yml`. Only add a job to an existing file when that
   file is already a test/CI workflow whose triggers are `push` / `pull_request`
   (or a subset). Never edit deploy, release, pages, or bot workflows. Never
   change another job's permissions as a side effect (if you must extend, set
   `permissions: contents: read` on the *new job* only).
   - Split **clean-runner setup** from **test invocation**. Setup: checkout,
     `runs-on: ubuntu-latest` (or another standard GitHub-hosted `ubuntu-*` /
     `windows-*` / `macos-*` label only if the tests truly need that OS),
     setup-python/node/go/rust at the repo's declared version, and install the
     same test runner and deps used locally. Inline in the job; do not require
     editing lockfiles or source. Forbidden `runs-on` without a separate
     explicit approval: larger runners (core-count, GPU, or "large" labels),
     custom images, runner groups, and self-hosted. The job's test command is
     the full-suite invocation that passed *once* in step 5 — never the repeat
     loop, never a command that assumes packages already installed on your
     machine.
   - Set `permissions: contents: read` at the top level of a *new* workflow
     file; the default token is broader than a test run needs.
   - Pin any third-party action to a full commit SHA, not a moving tag. First-
     party `actions/*` (checkout, setup-*) pinned to a major tag is acceptable.
   - Triggers are `push` and `pull_request` only. Never use `pull_request_target`
     or `workflow_run` in a file this skill creates or edits.
   - Never write a secret into the workflow, and do not make the test job depend
     on one — fork PRs run without secrets by design.

7. **Verify the workflow before handing it back.** Re-read the finished YAML
   and confirm it has a clean-runner setup plus the step-5 full-suite command,
   not the local repeat loop. Check the YAML parses:
   `python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' .github/workflows/test.yml`
   if PyYAML is available (pass the real path as `sys.argv[1]`; do not redirect
   the file on stdin), otherwise a careful read. If `act` is installed and the
   user wants it, offer a local dry run; do not require it.

8. **Summarize and hand off.** Report, concisely:
   - What you tested and, just as important, what you deliberately did not test
     and why (network, secrets, display, nondeterminism).
   - Any intent-mismatch / genuine bug from step 4 (not in CI).
   - The workflow added (or the job added to an existing test workflow), and the
     exact command it runs.
   - That CI only starts running once the change is pushed. Present the diff for
     review; commit via the `commit_and_push` skill when the user is ready. Do
     not commit or push on your own unless asked.
