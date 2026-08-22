---
name: create-github-action-tests
description: >
  Backfill characterization tests and wire them into free GitHub Actions CI
  on a public repo. Confirms Actions would be free (public repo, standard
  hosted runners), auto-rejects billed or unknown CI until the user explicitly
  approves, adds new test files only, proves each new test is deterministic
  and cannot silently skip, then wires them into a hardened workflow. Use when
  asked to "create github action tests", "backfill characterization tests",
  "add tests and GitHub Actions CI", "wire tests into CI", or
  "/create-github-action-tests".
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
   - Each test must actually exercise the path its name claims. Re-read every
     setup line and confirm nothing later in the test undoes it — a fixture
     that recreates the file you just deleted turns a "missing input" test into
     something else while still passing. A green test whose label overstates
     what it covers is worse than no test: it is a false coverage claim.

5. **Prove each test is deterministic and cannot silently skip.** If an existing
   suite is already red *before* any new files, report that and do not add a
   workflow. New characterization tests may still be written if they themselves
   pass; they must not become a CI job that pretends the repo is green.
   - Otherwise run the full suite once, then run the newly added tests several
     times in a row (e.g. a short loop of 5–10 runs). Keep only tests that pass
     on every run.
   - Drop or fix any test that is flaky or order-dependent. A flaky test trains
     everyone to ignore the CI badge, which destroys the whole point.
   - **A test that can skip itself is not covered.** A skipped test suite exits
     0 and reads as green while asserting nothing. If a test needs an external
     interpreter, binary, or optional dependency, CI must install it (step 6)
     *and* the test must **fail rather than skip** when the tool is absent and
     an environment variable such as `CI` is set. A convenience skip for local
     developers is acceptable only with that CI-side hard failure in place.
   - Verify that, do not assume it: rerun the new tests once with the tool
     removed from `PATH`. That run must report skips only outside CI, and must
     fail with `CI` set. Treat "everything skipped, exit 0" as a red flag.
   - A red, flaky, or silently-skipped test committed as if it were green is a
     failure of this skill, not a finding.

6. **Wire the tests into CI with the least invasive change.** Pick the first
   option that fits:
   1. **Extend an existing test job** when that job already runs a command that
      now picks the new tests up on its own (the suite lives in a directory the
      job already runs whole). Add only the setup steps the new tests need.
      Prefer this: it avoids a duplicate checkout and toolchain setup, and keeps
      a single green/red signal.
   2. **Add a job to an existing test/CI workflow** when the new tests need a
      different runner, toolchain, or command than any existing job.
   3. **Create `.github/workflows/test.yml`** when the repo has no suitable test
      workflow.

   Only touch a file that is already a test/CI workflow whose triggers are
   `push` / `pull_request` (or a subset). Never edit deploy, release, pages, or
   bot workflows.
   - **Permissions.** On a new workflow file, set `permissions: contents: read`
     at the top level. On a new job in an existing file, set it on that job
     only. Never change an existing job's permissions as a side effect.
   - If the workflow you extend has no `permissions:` key anywhere and every job
     in it is a test job, top-level `permissions: contents: read` is the right
     hardening — but *propose* it in step 8 and let the user decide. Do not add
     it silently; it changes the token for jobs this skill did not write.
   - **Clean-runner setup.** Keep setup separate from test invocation. Setup is
     checkout, `runs-on: ubuntu-latest` (or another standard GitHub-hosted
     `ubuntu-*` / `windows-*` / `macos-*` label only if the tests truly need that
     OS), setup-python/node/go/rust at the repo's declared version, and every
     runner, dependency, and external tool the tests need — including the tools
     behind step 5's no-skip rule. Inline it in the job; do not require editing
     lockfiles or source. Forbidden `runs-on` without separate explicit
     approval: larger runners (core-count, GPU, or "large" labels), custom
     images, runner groups, and self-hosted.
   - **Test invocation.** When you add one, it is the full-suite command that
     passed *once* in step 5 — never the repeat loop, and never a command that
     assumes packages already installed on your machine. Under option 1 there is
     no new command; confirm the existing one really does collect the new tests.
   - Pin any third-party action to a full commit SHA, not a moving tag. First-
     party `actions/*` (checkout, setup-*) pinned to a major tag is acceptable.
   - Triggers are `push` and `pull_request` only. Never use `pull_request_target`
     or `workflow_run` in a file this skill creates or edits.
   - Never write a secret into the workflow, and do not make the test job depend
     on one — fork PRs run without secrets by design.

7. **Verify the workflow before handing it back.** Re-read the finished YAML and
   confirm it has a clean-runner setup plus the step-5 full-suite command, not
   the local repeat loop, and that every tool the tests need is installed there.
   Parse the file with whichever of these is available — do not assume PyYAML is
   installed, it frequently is not:
   - `python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' <path>`
   - `ruby -ryaml -e 'YAML.load_file(ARGV[0])' <path>`
   - `yq '.' <path> >/dev/null`

   Pass the real path as an argument; do not redirect the file on stdin. If none
   of them are installed, say so — a careful read is the fallback of last
   resort, not the plan. If `act` is installed and the user wants it, offer a
   local dry run; do not require it.

8. **Summarize and hand off.** Report, concisely:
   - What you tested and, just as important, what you deliberately did not test
     and why (network, secrets, display, nondeterminism).
   - Any intent-mismatch / genuine bug from step 4 (not in CI).
   - The workflow file and job you added or extended, and the exact command it
     runs.
   - Any hardening you deliberately did not apply and why — in particular a
     top-level `permissions: contents: read` proposed under step 6.
   - Any new local prerequisite the tests introduce (an interpreter, a tool). If
     the repo documents how to run its tests, say that doc needs the new
     prerequisite added: a contributor who sees green without the tool installed
     is being misled.
   - That CI only starts running once the change is pushed. Present the diff for
     review; commit via the `commit_and_push` skill when the user is ready. Do
     not commit or push on your own unless asked.
