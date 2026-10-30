# Single controller implementation

This implementation is in progress. The production runtime is still the
[Functions implementation](remote-development.md); the files below have not been
cut over or accepted against live task execution.

## Task ownership

`automation/controller/core.py` is the replacement task-state writer. `apply`
accepts the typed commands in `model.py`; `inspect` returns committed task views.
It owns repository grants, task/attempt identity, queue ordering, readiness
decisions, coding progression, external stop/publication observations, retry
authorization, and independent admission holds.

`store.py` uses a real local SQLite database with transactions, foreign keys,
state constraints, and unique repository grants. One process holds an exclusive
owner-file lock. The lock protects that database on its host; infrastructure must
still restrict which VM identity and disk may serve as the controller. Database
files use private permissions and must live on persistent storage.

Agent callbacks record results without proving GitHub jobs stopped. Repository
ownership remains through cleanup and PR resolution. Only verified external
observations can release it. A merged PR remains authoritative even when its
worker later reports cleanup failure. Superseded attempts cannot mutate current
execution state. Operator and deployment holds have distinct owners.

Account credentials and leases remain in their existing Azure storage. The task
database does not supply cross-store transactions or another credential cache.

`broker.py` applies the task grant before scoped worker operations and reuses the
existing fenced account resources. Probe identities are recorded before returning
credentials, then cleared only after verified release. Review/coding assignments
are independent. Codex replacement access requires release of the exhausted
account and the same registered coding thread; it cannot switch providers.
The HTTP transport and production instruction adapter still need wiring.

## Native execution graph

`automation/controller/workflows.py` generates `clusterfork-task.yml` and four
reusable workflow files with distinct authenticated roles:

| Role | Workflow | Access |
| --- | --- | --- |
| Review preflight | `clusterfork-review-account.yml` | Review account selection |
| Reviewer | `clusterfork-review.yml` | Assigned account and read-only review |
| Coding preflight | `clusterfork-coding-account.yml` | Independent coding account selection |
| Coding agent | `clusterfork-code.yml` | Assigned account, validation, guarded publication |

Native `needs` edges sequence these jobs in one run. Only selected preflights and
a passing review enable their downstream jobs. Each job gets a fresh VM. Runtime
and target checkouts are siblings, preserving local-action manifests through
post-job cleanup. `controller_bootstrap.py` verifies the runtime/job revision,
obtains broker authorization, and requests a scoped target checkout token.

`authorization.py` binds verified GitHub OIDC claims to the mapped task, immutable
runtime tag/revision, exact caller workflow, and one reusable job role. The HTTP
adapter must cryptographically verify the JWT first. Coding requires a successful
`review / run` job from the exact GitHub attempt; an output boolean alone cannot
grant permission. Account bindings are separate for review and coding.

These new workflows are not yet registered for production admission. The current
worker now emits the review scheduling output that their graph consumes; existing
worker behavior otherwise remains under its current broker.

## Verification

`tests/test_controller.py` exercises the interface against real SQLite files,
including concurrency, rollback, persistence, stale events, proof validation,
account assignment, cleanup/publication resolution, and independent holds.
`test_controller_authorization.py` checks signed roles and actual review-job
evidence. `test_controller_broker.py` crosses the real controller with the existing
account implementation to verify probes, cleanup, separate assignments, live
leases, context checks, and restricted failover. `test_controller_workflows.py`
parses the shipped YAML and checks job
dependencies, role separation, checkout order, and inter-job outputs.

The replacement still needs its broker/transport integration, Azure host and
deployment path, migration, and live acceptance. Unit tests are not cutover evidence.

## Rebuild baseline

On 2026-10-30, the existing deployment workflow was verified `disabled_manually`.
The existing durable deployment record was set to paused with owner
`operator:single-controller-rebuild`; public health confirmed `deployment_paused`
and `workers_drained`. There were no active account leases or open GitHub PRs.
The repository slot was empty. Historical tables included stale readiness records;
their status alone is not evidence of a live runner.

A private backup of the seven task/control tables was saved under
`~/.local/state/clusterfork/rebuild/baseline-20261030.json`. It excludes the account
credential table. Revalidate current external state before migration; these are
dated baseline observations, not an ongoing health claim.
