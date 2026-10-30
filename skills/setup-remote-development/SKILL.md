---
name: setup-remote-development
description: Enroll the current GitHub repository in Clusterfork's shared Linear remote development system, or repair an existing enrollment. Connect an explicitly selected existing Linear project, install pinned shared worker workflows and validation, verify the deployed mapping, and enable the project label last. Use for repository onboarding; ordinary Todo issue execution needs no setup skill.
---

# Set up remote development

Run once per repository, then use Linear's project label + Todo for daily work.
Project and repository names may differ. An enrollment request authorizes setup
within the user's stated scope; a request only to prepare configuration does not
authorize publication, deployment, or enabling enrollment. Respect the session's
publication and external-action restrictions.

## Source and access

Use a source checkout of `KianBahasadri/clusterfork` containing
`automation/onboard.py`. Prefer the operator's existing checkout; otherwise obtain
the source with the existing GitHub identity. No dotfile installation is needed.
Read that checkout's `docs/README.md`, `docs/remote-development-setup.md`, and, for
deployment, `docs/azure-infrastructure.md` and
`docs/remote-development-deployments.md`. Follow the target repository's
`AGENTS.md`, documentation, CI, and merge rules too.

Run the helper from the central source checkout, passing the target explicitly:

```bash
python3 automation/onboard.py discover --checkout /path/to/target
```

The helper uses the existing operator's `gh` access and central Linear
credentials (`--env-file /private/operator.env` or existing environment values).
It does not log in or change authentication. App-access verification happens
inside the deployed broker. Keep App keys and account credentials there; never
retrieve them for this skill or put credentials, broker account data, or new
long-lived secrets in the target repository. Reuse the existing App installation,
Function App, dispatcher, account pools, model pins, and quota policy.

## Choose the binding before writing

1. Confirm the GitHub owner/repository and live default branch discovered from
   the target checkout's `origin`. Do not substitute the current feature branch.
2. List accessible existing Linear projects. Use context and the user's choice
   to select a project, then record its **UUID**. Similar names are only search
   hints. Resolve multiple plausible projects before changing the registry.
   Do not create a project as an onboarding shortcut.
3. Discover the selected project's teams and their workflow states:

   ```bash
   python3 automation/onboard.py discover --checkout /path/to/target --project-id PROJECT_UUID
   ```

   Choose the intended team explicitly when several contribute to the project.
   Record Todo state UUIDs (`unstarted`), In Progress (`started`), Needs
   attention/Blocked, and Done (`completed`). Do not enroll Backlog, move issues
   to Todo, change completed issues, or guess between ambiguous lifecycle states.
4. Inspect the repository's actual validation conventions and CI. Choose
   reproducible validation argv arrays and meaningful required check names that
   test, lint, or build the repository. Include necessary dependency setup in the
   argv commands when the shared Python 3.12/Node 22 runtime is insufficient.
   Run them locally. Passing `true`, printing a message, or checking only the
   onboarding files is not useful repository validation.

## Prepare and publish configuration

Choose a **published, tested full commit SHA** containing both
`clusterfork-select-account.yml` and `clusterfork-develop.yml`. This pins the
reusable workflows and the engine to the same revision. Never use a moving
branch/tag as the worker version.

Preview the changes with the discovered values. JSON arguments below illustrate
the shape; replace all UUIDs and commands with the repository's real values:

```bash
python3 automation/onboard.py configure --checkout /path/to/target \
  --project-id PROJECT_UUID --team-id TEAM_UUID --worker-revision FULL_COMMIT_SHA \
  --states-json '{"ready":["TODO_UUID"],"in_progress":"STARTED_UUID","blocked":"ATTENTION_UUID","done":"DONE_UUID"}' \
  --validation-commands '[["npm","ci"],["npm","test"]]' \
  --required-check 'Repository tests'
```

Use `--apply` to write the previewed local changes. They are the target's small
`.github/workflows/clusterfork-worker.yml` and the central
`automation/azure/projects.json` mapping. Existing required check names accumulate;
other mappings, the registry generation, model/quota policy, and unrelated files
are preserved. Workflow collisions stop before writes; reconcile them explicitly
instead of overwriting user configuration.

If suitable CI is missing, use `--install-ci` in place of `--required-check`.
It adds `clusterfork-validation.yml` and its `Clusterfork validation` check,
running the selected argv commands for pushes and PRs on a standard Ubuntu
runner. Check that those commands cover real behavior and fit that environment.
Keep existing CI. If another toolchain or workflow structure is necessary, add
the appropriate repository CI and pass its actual check names instead.

Publish the target workflow/CI and central mapping through their normal tested
review/merge processes when authorized. The worker workflow must exist on the
configured default branch before enrollment; the Azure packager does not publish
it. Respect Workflows write permission for the **installation**, not just the App
registration. Do not request new account secrets or Administration permission
for the App.

Preview and apply merge setup with the operator's existing repository access:

```bash
python3 automation/onboard.py merge --checkout /path/to/target --project-id PROJECT_UUID
python3 automation/onboard.py merge --checkout /path/to/target --project-id PROJECT_UUID --apply
```

This enables auto-merge/squash and adds missing required checks in an additive
branch ruleset. It preserves existing protection, checks, reviews, and other
repository settings. Do not disable required reviews, bypass protection, or
grant the App a bypass so an automatic merge can finish. Existing manual-merge
overrides and conflicting ruleset scopes need an explicit user decision.

Provision missing team issue labels and an ungrouped **project** label named
`clusterfork-symphony` using the existing Linear label operator. Discover existing
labels first; do not duplicate them. The helper reports missing names and accepts
`--enrollment-label-id UUID` when label names are ambiguous. Do not apply the
project label yet. If webhook team coverage needs updating, update the existing
webhook; keep the shared timer and service.

## Deploy, verify, then enable

Deploy the central mapping through the existing `deploy-azure.yml` process after
its exact-revision CI passes. It pauses admissions and drains workers before
updating the shared dispatcher, reconciles the existing Terraform resources, then
verifies health and resumes. Do not deploy around a held pause or cancel active
workers. Do not provision a new Azure service or reset `generation` to make setup
take effect.

```bash
python3 automation/onboard.py verify --checkout /path/to/target --project-id PROJECT_UUID
```

Verification checks the exact deployed registry/source digests, pinned engine's
protocol, serial admission, App installation grants, Actions availability/policy,
default-branch worker content, automatic merge settings, effective required checks,
successful current CI, and Linear state/label availability. It lists existing
Todo candidates by identifier/title. Show this list to the user before enabling;
dependencies, prior durable receipts, quota, and readiness review still determine
admission. An empty Todo list is valid. Do not change issue states to manufacture
a first run.

Only after these checks pass, apply enrollment **last**:

```bash
python3 automation/onboard.py enroll --checkout /path/to/target --project-id PROJECT_UUID
```

`enroll` repeats verification, adds the project label without replacing other
labels, and reads it back. Existing labels and identical local/merge configuration
are no-ops on repeat invocation. Report the project UUID, repository/default
branch, worker pin, validation/checks, verified deployment, and enrollment result.
When any access/check is missing, keep enrollment disabled and report the exact
remaining operator action, affected resource/permission, and verification command.
Prepared files or a healthy broker alone do not establish completed enrollment.

## Updating a pin or repairing setup

Shared implementation changes are published once in the central repository.
Re-run `configure` with a new tested SHA, publish only the small caller workflow
change and its central mapping update, and use the existing drained deployment
process. Hold admissions before changing an enrolled caller's pin so dispatches
cannot race the mapping update. The OIDC job revision must equal that mapping's pin. If the readiness
protocol changes, coordinate all enrolled pins while admissions are paused;
keep the pause until every caller and the broker are compatible, then verify
before resuming. Do not rerun `enroll` as a way to repair a protocol mismatch.
Deployment checks every enrolled pin's contract and installed caller before
resuming; a mismatched or unpublished caller keeps the pause held. The current
shared checkout uses Clusterfork's public central source; a private source needs
runtime read access for the caller, which the helper reports before enrollment.

For an already enrolled project, preserve its label and durable receipts while
repairing configuration. Never bump the registry generation, recreate resources,
clear queue/readiness state, or rerun completed issues as a setup side effect.
