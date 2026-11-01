# clusterfork-symphony controller

The Azure VM controller is the sole task orchestrator. Linear supplies human
requests and displays committed status; GitHub runners execute controller commands.
See [runtime policy](remote-development.md), [queues](remote-development-queue.md),
[accounts](remote-development-accounts.md) and [deployment](remote-development-deployments.md)
for their operational rules.

## Task ownership

`automation/controller/core.py` is the sole task-state writer. `apply` accepts
typed commands from `model.py`; `inspect` returns committed views. It owns grants,
task/attempt identity, ordering, readiness, coding progression, external stop and
publication observations, retries, one repair budget and independent holds.
Account and instruction resources cannot independently advance task lifecycle.

`store.py` uses local SQLite transactions, foreign keys, state constraints and
unique repository grants. One process holds an exclusive owner-file lock. This
protects the database on its host; infrastructure must also restrict the serving
VM identity and disk. Database files require private permissions and persistent
storage. Callbacks do not prove GitHub stopped; superseded attempts cannot mutate
current execution. [Queue release rules](remote-development-queue.md#ownership-and-release)
require external evidence, including cleanup and PR resolution.

Task grants, account leases and instructions share SQLite. `accounts.py` supplies
provider-specific fenced records; `account_commands.py` composes reservation,
assignment and execution authorization in one transaction. Failed assignment
rolls reservation back. Probe claims repeat for the same owner/fence; another
identity requires verified release. No network request runs inside a transaction.

`Controller.authorize(task_id, attempt_id, role, action, now=...)` is the fresh,
read-only execution decision for broker operations, context revalidation and
steering. Its private `authority.py` implementation owns grant, eligibility,
dependency phase, session/deadline, publication, steering and execution-lease
checks. Queries create no reports. Startup permits a session to be created;
credential cleanup remains fenced and available after execution is revoked.
`broker.py` verifies signed identities, gathers external evidence and issues
scoped credentials; it does not independently interpret task phase or blockers.
`instructions.py` uses the same decision for observation and delivery. Shared
provider, routing, publication, image and reporting utilities live in
`automation/common/`.

## Observation and transports

`driver.py` observes Linear/GitHub and submits commands. It claims dispatch intent
before calling GitHub and discovers uncertain results rather than resending. It
resolves target context at admission and checks the required runtime before new
attempts. Linear comments and labels derive from task views. SQLite
`comment_signatures` records confirmed comment writes. Workflow states use
independent durable effects. `linear_state.py` privately interprets workflow
observations and human intent for Controller; [queue policy](remote-development-queue.md#linear-feedback)
owns state reconciliation and operator intent.

The admitted specification retains repository, branch and merge policy. Mapping
removal/change revokes new execution access while observation continues against
the original run/PR until ownership can release. Archived or moved issues retain
pending reports without changing their human-managed state. Project scans ignore
unseen ineligible issues and preserve terminal identity without new completion
comments. Hold changes refresh waiting-task reporting intent.

`server.py` exposes signed webhook intake, OIDC worker requests and bounded public
health metadata. Verified webhook IDs enter the durable inbox before acknowledgement.
Operator commands use a local mode-0600 Unix socket, never public HTTP routes.
`/api/accounts` separately verifies an allowlisted Azure operator identity for
enrollment/reclaim. `controller_main.py` assembles adapters, reads managed-identity
secrets and verifies the expected VM resource identity before serving.

## Deployment and migration

SQLite schema 12 retains each phase's GitHub run identity and durable executor
commands/results. Upgrades retain task, grant, lease, approval and human-intent
history; existing phase identities are adopted from their recorded attempt.
Startup recovers intent from the retained authenticated inbox. Older controller binaries reject schema 12;
rollback requires a compatible binary or restoration of the pre-upgrade database
after the normal deployment drain. [Queue policy](remote-development-queue.md#linear-feedback)
owns adoption of existing Linear projection acknowledgements.

Task history and account/instruction imports are complete; private snapshots and
SQLite digest receipts retain the audit record. Startup uses SQLite without a
Table SDK, migration adapter or legacy storage. The retired coordinator and relay
are removed; their [historical records](README.md#historical-investigations) remain.
[Deployment](remote-development-deployments.md) owns runtime identity, draining,
ingress and operator/deployment holds, including initial-install holds.

## Controller-owned execution

`automation/controller/workflows.py` generates `clusterfork-task.yml`, which
executes exactly the controller-selected `review` or `coding` phase. There is no
job dependency or worker result that schedules another phase. After readiness
approval, the controller verifies that review and its cleanup stopped, releases
its credentials, and durably dispatches coding as a separate GitHub run. Each
phase uses a fresh hosted VM, independent account binding and the approved runtime.
Runtime and target checkouts are siblings.

Inside coding, `execution.py` implements Controller's durable commands: coding,
testing, bounded coding corrections, and commit/push preparation. Workers report
results against immutable command IDs; repeated identical results return the
current command, while conflicting or superseded results cannot advance work.
Progress reports cannot choose another stage or grant push access. Commands,
correction counts and result receipts survive controller restart.

The assignment transaction reserves the lease; heartbeats renew it through
checkout. Starting execution cannot reopen initial selection. Review uses the
read-only sandbox. Coding executes validation and guarded commit/push commands,
then returns the exact prepared SHA and scanned PR metadata. Only the controller
creates/recovers the PR and decides when to merge; the worker has no PR-write
credential. A successful callback with prepared changes cannot count as no-change
completion while publication remains pending. The controller dispatches at its
verified immutable runtime tag.

`publication.py` performs persisted PR/merge effects outside transactions after
verified executor stop and fresh task/context authorization. Definite rejections
permit at most three calls, with durable one- and two-minute delays. Ambiguous
writes are discovered instead of blindly repeated. An unresolved PR creation
retains repository ownership even after cancellation. Terminal observations settle
pending effects without polling historical tasks. [Runtime](remote-development.md#automatic-merges)
owns check and merge policy.

`authorization.py` binds verified GitHub OIDC claims to the mapped task, immutable
runtime tag/revision, exact caller workflow and reusable job role. The HTTP adapter
cryptographically verifies signature, issuer, audience and expiry, and checks
hosted-runner type, run/attempt and actual dispatch by `clusterfork[bot]`. The task
grant authorizes the mapped target, independently of caller repository name.
Coding also requires successful `review / run` evidence from the recorded review
run/attempt; its own GitHub identity remains separately bound.

## Verification

The [suite](conventions.md#local-validation) exercises real SQLite/HTTP boundaries,
signed roles, leases, workflow graphs and recovery. Git-backed release/deployment
tests verify runtime identity, target freshness and drain gates. Unit tests do not
establish live cutover; dated receipts follow. The Python 3.12 runtime CI also
runs `test_symphony_policy.py`: all ten submission/cleanup/reporting interleavings
with restart at every cut, later withdrawals, ambiguous writes, cross-operation
authorization and bounded broker outage recovery. These use fake remote services
and real SQLite without credentials or model calls.

## Rebuild baseline

On 2026-10-30, deployment was `disabled_manually`, with durable pause owner
`operator:single-controller-rebuild`. Health showed `deployment_paused` and
`workers_drained`; no live account leases, open PRs or repository slot remained.
Stale readiness rows were not live-runner evidence. The private seven-table backup
is `~/.local/state/clusterfork/rebuild/baseline-20261030.json`, excluding credentials.
Revalidate external state before any recovery; this is a dated observation.

## Live acceptance, 2026-10-30

Initial release `abc53b7a4665b5193f88577b71ed24d0abdfd8e9` was verified over HTTPS
and by immutable tag after stopping the Function App. The disk held 41 imported
history records; four credential fingerprints matched. Installation preserved
operator holds, explicitly released for acceptance.

| Task | Run | Merged PR | Evidence |
| --- | --- | --- | --- |
| KIAN-933 | [41130619289](https://github.com/KianBahasadri/clusterfork/actions/runs/41130619289) | [41](https://github.com/KianBahasadri/clusterfork/pull/41) | Ordinary guarded completion and both credential releases |
| KIAN-934 | [41131982035](https://github.com/KianBahasadri/clusterfork/actions/runs/41131982035) | [42](https://github.com/KianBahasadri/clusterfork/pull/42) | Target context exactly matched preceding merge `0b9f947` while runtime stayed pinned |
| KIAN-935 | [41133214559](https://github.com/KianBahasadri/clusterfork/actions/runs/41133214559) | [43](https://github.com/KianBahasadri/clusterfork/pull/43) | Installer stop fix `9aa974d` rolled out by [41134535439](https://github.com/KianBahasadri/clusterfork/actions/runs/41134535439) |
| KIAN-936 | [41135013810](https://github.com/KianBahasadri/clusterfork/actions/runs/41135013810) | [44](https://github.com/KianBahasadri/clusterfork/pull/44) | Runtime and target both matched `9aa974d` |
| KIAN-937 | [41143929662](https://github.com/KianBahasadri/clusterfork/actions/runs/41143929662) | [45](https://github.com/KianBahasadri/clusterfork/pull/45) | Admission followed complete deployment, including ingress verification |

These runs passed all four jobs in the **then-current** graph and real post-job
cleanup, merged automatically and reached Done. For KIAN-937, deployment
[41143295703](https://github.com/KianBahasadri/clusterfork/actions/runs/41143295703)
finished at 23:20:45 UTC; admission followed 13 seconds later without manual resume.
Runtime/target matched `64897b746a2247a2cd474fc8ebe0f609df954033`.
PR 45 merged as `3a1ac89c1cde43eebc1be35d25a691ceefb91d71`; Done followed at
23:31:08 UTC with usage comments and execution-label cleanup.

Final inspection found 17 verified native inbox events, successful read-only
`onboard.py verify`, four accounts with no live leases, released phase assignments,
no grants/holds and an active unprivileged service. Database and operator socket
were mode `0600`; the database was on persistent storage. The [acceptance fixture](archive/controller-acceptance-20261030.md)
retains sequential checkout observations; relay-era evidence is historical.

## Resource consolidation, 2026-10-31 UTC

Two-job execution and SQLite resources were deployed with all four original
credential records/state and 41 history records preserved. Native signed Linear
intake worked after stopping the relay. A reviewed retirement deleted 21 legacy
resources, preserving the VM, disk, secrets and separate state account; the next
Terraform plan was empty. A fresh dependency environment passed 636 tests, with
the optional live-image test skipped and no Functions/Table SDKs.

[KIAN-939 run 41153381971](https://github.com/KianBahasadri/clusterfork/actions/runs/41153381971)
followed [deployment 41152900749](https://github.com/KianBahasadri/clusterfork/actions/runs/41152900749)
and passed exactly `review / run` and `code / run` on separate hosted runners,
including post-job cleanup; coding succeeded with legacy storage absent.
