# Single controller implementation

The controller is live on its Azure VM. Five acceptance tasks have completed
through the ordinary guarded path, including automatic runtime rollout and
verified deployment completion before the following task starts. Linear intake,
account cleanup, onboarding and existing reporting are verified on the live release.

## Task ownership

`automation/controller/core.py` is the replacement task-state writer. `apply`
accepts the typed commands in `model.py`; `inspect` returns committed task views.
It owns repository grants, task/attempt identity, queue ordering, readiness
decisions, coding progression, external stop/publication observations, retry
authorization, and independent admission holds.

The responsibilities previously spread across lifecycle writers map as follows:

| Existing writer | Replacement responsibility |
| --- | --- |
| `dispatch_core.Dispatcher`, `repository_queue.RepositoryQueue` | Controller eligibility, ordering, grants and dispatch intents; driver observations |
| `readiness.ReadinessGate` | Controller review binding/decisions and broker authorization; native review jobs |
| `worker_core.WorkerService` task records | Broker commands and controller phase/attempt transitions |
| `linear_sync.LinearSync` | Driver-observed GitHub/PR facts, controller completion, derived Linear reporting |
| `auto_repair.AutoRepair` | Controller-owned single repair budget and attempts; existing repair prompt/validation helpers |

Existing fenced accounts and instruction resources retain their own storage;
they cannot independently advance task lifecycle.

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

Task grants, account leases and instruction records now share this SQLite database.
`accounts.py` provides provider-specific fenced records; `account_commands.py`
composes account reservation, assignment and execution authorization in the same
transaction. No network request runs inside these transactions. A failed assignment
rolls its account reservation back. Probe claims are repeatable for the same owner
and fence; a different identity requires verified release first.

`broker.py` verifies signed phase identity and current task authority before
credential access. `instructions.py` uses typed commands against actual task views;
instruction delivery cannot advance task lifecycle. Shared routing, publication,
policy, image, account identity and reporting utilities live in `automation/common/`.
The retired Functions coordinator and its table adapters are removed.

## Observation and transports

`driver.py` reads current Linear/GitHub facts and submits typed commands. It claims
dispatch intent before the external call and discovers an uncertain result instead
of resending. It resolves the latest target context at admission and checks a
required-runtime predicate before admitting or retrying work. A deployment-owned
hold cannot clear an operator hold. Rendering Linear status is derived from task
views and does not make execution decisions. SQLite `comment_signatures` records
only confirmed remote writes, preserving deduplication across restarts. Failed or
ambiguous writes remain retryable using their stable comment IDs.

The admitted specification retains the original repository, branch and merge
policy. Removing or changing a mapping revokes new execution access while the
driver continues observing the original run and PR until ownership can be
released. Archived or moved issues retain pending reports without changing their
human-managed state.

`server.py` exposes authenticated webhook intake, OIDC worker requests, and public
bounded health metadata. Verified webhook IDs are committed in the local inbox
before acknowledgement. Operator commands use a local mode-0600 Unix socket;
they are not public HTTP routes. The separate `/api/accounts` transport verifies
an allowlisted Azure operator identity for local enrollment and fenced reclaim. `controller_main.py` assembles these adapters,
reads existing secrets through managed identity, and verifies the expected VM
resource identity before opening the service. A newly initialized database starts
with an operator hold; restarting an existing database preserves its current
holds. Hold changes create fresh reporting intent for waiting tasks. Project scans
ignore unseen ineligible issues and retain terminal task identity without adding
new historical completion comments.

`release.py` supplies committed runtime identity. [Deployment](remote-development-deployments.md)
owns runtime-input selection, rollout, verified draining, direct webhook ingress
and independent operator/deployment holds.

## Deployment and migration

The terminal history and account/instruction imports are complete. Their private
snapshots and SQLite digest receipts retain the audit record. Startup uses only
the controller database; no Table SDK, migration adapter or legacy runtime
storage is required. The retired Function App relay and table resources are
removed only after verifying the migrated records and native direct webhook delivery.

## Native execution graph

`automation/controller/workflows.py` generates `clusterfork-task.yml` with two
fresh hosted VMs: `review`, then `code` only after a successful review. Each job
checks out its approved runtime and runs one trusted worker process. That process
probes quota, reserves an account, irreversibly closes initial pool access, obtains
a scoped target checkout, then executes its phase. Only the review scheduling
boolean crosses the job boundary. Runtime and target checkouts remain siblings.

The controller reserves the selected lease atomically with assignment and renews
it through checkout. Starting execution cannot reopen initial selection. Review
runs in the existing read-only sandbox; coding performs validation and guarded PR
publication before releasing credentials. The provider and account policy is
unchanged by the reduction in VM count.

`authorization.py` binds verified GitHub OIDC claims to the mapped task, immutable
runtime tag/revision, exact caller workflow, and one reusable job role. The HTTP
adapter must cryptographically verify the JWT first. Coding requires a successful
`review / run` job from the exact GitHub attempt; an output boolean alone cannot
grant permission. Account bindings are separate for review and coding.

The controller dispatches the combined workflow at its verified runtime tag.
The worker emits the review scheduling output that the native graph consumes.

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
Driver tests exercise sequential target-code freshness, the runtime admission
barrier, reporting independence, uncertain dispatch discovery, and the one-repair
budget. HTTP tests use real local sockets to verify authentication and durable
intake. Instruction tests exercise the existing delivery/acknowledgement protocol
through controller views. Database upgrades preserve task and attempt ownership.
Runtime tests check host identity, authentication selection, and hold persistence.
Release tests use real Git repositories to check runtime identity, target-only
changes, and exclusion of uncommitted files.
Deployment tests exercise grant/PR/lease draining and runtime tag identity.
The once-only migration adapters are retired after their live import receipts
were verified; historical task identity remains in SQLite.

The VM and persistent disk were provisioned on 2026-10-30. Service installation
and initial migration are complete. Final compatibility deployment and acceptance
passed. The dated receipts below establish live cutover; unit tests alone are not
cutover evidence.

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

## Live acceptance, 2026-10-30

The installed `abc53b7a4665b5193f88577b71ed24d0abdfd8e9` release was verified over
HTTPS and by its immutable runtime tag. The legacy Function App was stopped before
the controller started. The persistent disk contains 41 imported historical queue
records. All four credential rows matched their pre-cutover auth fingerprints.
Operator holds survived installation and were released explicitly for acceptance.

KIAN-933 used [run 41130619289](https://github.com/KianBahasadri/clusterfork/actions/runs/41130619289).
All four jobs and their real post-job cleanup steps passed.
[PR 41](https://github.com/KianBahasadri/clusterfork/pull/41) merged automatically
as `0b9f9473f5bdab14e54cb1b56e9a174fba033951`; the controller then marked it Done.
The review and coding phase assignments both recorded credential release.

KIAN-934 used [run 41131982035](https://github.com/KianBahasadri/clusterfork/actions/runs/41131982035).
Its saved target context is exactly KIAN-933's merge commit while its runtime
remained pinned to the installed release.
[PR 42](https://github.com/KianBahasadri/clusterfork/pull/42) merged automatically
as `fb0a8c842bdd1cfd24d30eb0f3c6c11e51fc83a1`; Linear reached Done without manual
queue advancement.

KIAN-935 used [run 41133214559](https://github.com/KianBahasadri/clusterfork/actions/runs/41133214559).
[PR 43](https://github.com/KianBahasadri/clusterfork/pull/43) merged the installer
stop-verification fix as `9aa974df9d84ee6fa281cea512fdbea888d6a034`.
[Deployment 41134535439](https://github.com/KianBahasadri/clusterfork/actions/runs/41134535439)
automatically installed and verified that runtime.

KIAN-936 used [run 41135013810](https://github.com/KianBahasadri/clusterfork/actions/runs/41135013810).
Its saved runtime and target context both match `9aa974df9d84ee6fa281cea512fdbea888d6a034`.
All four jobs passed, [PR 44](https://github.com/KianBahasadri/clusterfork/pull/44)
merged automatically, and Linear reached Done. This proved active-runtime and
checkout freshness; the final gate additionally waits for the whole deployment
workflow, including ingress verification, before admitting the following task.

The final compatibility release is `64897b746a2247a2cd474fc8ebe0f609df954033`.
[Deployment 41143295703](https://github.com/KianBahasadri/clusterfork/actions/runs/41143295703)
completed successfully at **23:20:45 UTC**. KIAN-937's
[run 41143929662](https://github.com/KianBahasadri/clusterfork/actions/runs/41143929662)
was created automatically at **23:20:58 UTC**, 13 seconds later. Its saved runtime
and target context both match that verified release. The task was already in Todo
while earlier ingress verification failed; it remained queued until the complete
deployment passed, without manual admission or resume.

All four jobs and every real post-job cleanup step passed. Only the historical
acceptance fixture changed in [PR 45](https://github.com/KianBahasadri/clusterfork/pull/45),
which passed required CI and merged automatically as
`3a1ac89c1cde43eebc1be35d25a691ceefb91d71` at 23:30:27 UTC. The controller marked
KIAN-937 Done at 23:31:08 UTC and removed its execution labels. Separate readiness,
development-start and development-finish usage comments were present.

Real Linear issue updates produced 17 verified native inbox events by the final
inspection. The retained Function App exposed exactly `health` and `linear_relay`; its
old worker RPC returned 404. HTTPS health and the immutable runtime tag identified
the deployed release. The read-only `onboard.py verify` command passed against the
live project, runtime, App grants, workflow, CI, labels and protocol. All four
account rows remained present, with zero live leases after execution.
The final database inspection confirmed completion, stopped jobs, merged
publication, released review/coding assignments, no repository grants and no
admission holds. The service was active under its unprivileged user; the database
remained on the persistent disk, with mode `0600` for it and the operator socket.

## Resource consolidation, 2026-10-31 UTC

The two-job implementation and SQLite resource migration are deployed. All four
account records retained their original credential bytes and active/staged state;
41 imported task-history records remain available. A native signed Linear event
was committed after the relay was confirmed stopped. The reviewed retirement
plan then deleted exactly 21 legacy infrastructure resources, with no changes to
the controller VM, persistent disk, secrets or separate Terraform state account.
A subsequent Terraform plan reported no changes.

The final dependency set passed 636 tests in a fresh Python environment without
the Functions or Table SDKs; the existing optional live-image test was skipped.

[KIAN-939’s workflow](https://github.com/KianBahasadri/clusterfork/actions/runs/41153381971)
passed with exactly `review / run` and `code / run` on different hosted runners,
including every real post-job cleanup step. It was created after successful
[deployment 41152900749](https://github.com/KianBahasadri/clusterfork/actions/runs/41152900749).
The coding phase continued successfully while legacy storage was absent.
