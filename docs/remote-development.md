# Remote development

Eligible Linear issues route through one shared Azure Function App to a Codex
worker on a standard GitHub-hosted runner. The repository queue admits one task
for a read-only readiness review. After a pass, the development worker leases an account using
live quota, implements the issue, runs configured validation, and publishes a
PR. GitHub can squash-merge it after required checks; Linear tracks progress and
blockers. Deployment is separate. This is Clusterfork's execution adapter, not
an installation of upstream Symphony.

[Setup](remote-development-setup.md) covers provisioning and enrollment;
[remote accounts](remote-development-accounts.md) covers credential ownership.
[Diagnostics](remote-development-observability.md) covers correlated operational
logs, durable failure explanations, and Azure monitoring.
[Dated validation records](archive/remote-development-validation.md) retain the
live routing and merge evidence without treating it as current health.

## Components and dispatch

| Source | Responsibility |
| --- | --- |
| `automation/azure/function_app.py` | Signed webhook, queue trigger, health, worker broker, and shared 15-minute timer |
| `dispatch_core.py`, `dispatch_clients.py` in that directory | Current Linear eligibility, GitHub App dispatch, and durable receipts |
| `worker_core.py`, `worker_store.py`, `quota_selection.py` | Workflow authorization, account leases, fenced credentials, and quota selection |
| `linear_sync.py` | Linear lifecycle and PR/CI reconciliation |
| `steering.py`, `automation/agent_session.py` | Durable Linear instructions and delivery into the live Codex turn |
| `repository_queue.py` | Durable repository priority queue, admission, worker fencing, and queue advancement |
| `selection_reporting.py` | Durable Linear queued/selected-label intent and repair against current queue state |
| `readiness.py`, `readiness_policy.py` | Durable read-only reviews, structured-result validation, clarification, and revision-bound admission |
| `automation/worker.py`, `automation/codex_runtime.py` | Account inspection, Codex execution, validation, publication, and merge requests |
| `automation/azure/projects.json` | Project mappings, states, model/effort, validation commands, and merge policy |
| `.github/workflows/clusterfork-worker.yml` | Pinned Codex CLI on `ubuntu-24.04`, six-hour job, read/OIDC permissions |
| `.github/workflows/clusterfork-pilot.yml` | Routing-only receipt workflow; no coding agent |

The webhook authenticates raw bytes with HMAC-SHA256 and requires a timestamp
within five minutes. It queues entity IDs and diagnostic timestamps from
Issue/Project/Comment create/update events and returns 200 only after a needed queue send succeeds. Comment events use
the separate [agent steering](remote-development-steering.md) path. The issue/project consumer
fetches current Linear data, then checks the mapped project, its configured
enrollment project label, archive flags, and configured ready-state IDs. An issue
label does not enroll a project. Removing enrollment prevents new dispatches but
does not cancel submitted jobs. See
[setup](remote-development-setup.md#linear-webhook-and-first-run) for the enrollment label.

The shared timer reconciles worker/PR status and scans mapped projects every
15 minutes. Project labels are fetched once per issue page to stay within
Linear's query-complexity limit.

## Todo readiness gate

An enrolled issue in a configured Todo state enters its repository queue first.
Only the issue holding that repository's slot receives a bounded review of
its title, description, full paginated comment thread (including reply parents),
relevant code, and repository rules. Explicit user decisions can settle earlier
concerns; a request to bypass review cannot grant a pass. Reviewers read `AGENTS.md`
and relevant documentation. They judge missing decisions, not description length:
a precise one-line fix can pass. They assess intended outcome,
consequential scope decisions, observable completion, and specific unresolved
security, privacy, data-loss, permission, external-action, or spending risks.
Documented project-wide security defaults can settle a decision, including when
an issue explicitly references them. New decisions or departures from those
defaults must be addressed in the issue; discoverable implementation details do
not require user clarification. This gate enables the existing development,
validation, publication, and merge process; a pass is not a security certification.

Reviews use the same pinned Sol 6.1/max profile, live quota selection, and fenced
Codex account pool as development. They run in the pinned CLI's `read-only`
sandbox with approvals disabled, isolated auth, no inherited job credentials,
and a JSON output schema. They do not execute configured validation or receive
publication tokens. Checkout credentials have only Contents read access. Each
model review has a **600-second** timeout; the broker allows another 120 seconds
for account selection and setup after start. Heartbeats cannot extend that
deadline. Review callbacks never authorize repository writes themselves.

Azure validates every criterion, blocker, verdict, and their consistency. A pass
is recorded in `ClusterforkReadiness` before development dispatch. Approval binds
to title and description, project, versioned policy/schema and configuration,
model profile, and target branch commit. Comments, labels, and lifecycle updates
do not invalidate their own review. Any target branch change invalidates context,
including a preceding queued task's merge. Admission, dispatch retries, worker
start, account access, coding, and publication recheck approval; coding must use
the reviewed base commit. Material edits during account selection prevent coding
and publication.

A rejected review moves the issue to the configured Blocked state (or a unique
team state named Blocked), clears `clusterfork: selected` and active stage labels,
and does not retry automatically. Unrelated labels and human status changes are
preserved. A valid `needs_clarification` result adds `clusterfork: needs clarification`
and explains one to four missing decisions, consequences, and answerable questions.
Invalid results retain a bounded, credential-redacted output snapshot and a specific
field, consistency, parsing, or binding error. Each attempt keeps a stable review
comment with its feedback and run link, so later reviews see rejections and replies.
Address feedback in the description or comments, then manually move Blocked to Todo
for another review. Resubmission clears the owned clarification label after a pass;
it needs no generation change and never consumes or resets a completed coding receipt.

Unavailable context, dispatch failures, timeouts, quota waits, and infrastructure
uncertainty hold coding with a visible retry comment. The shared timer retries
temporary failures after 15 minutes and verifies the old GitHub attempt stopped
before launching another. An uncertain/crashed dispatch without a confirmed run
is held for discovery and investigation, never blindly resent. Duplicate issue
events, project scans, and callbacks share the durable review record. Cancellation,
archive, loss of enrollment, project moves, or human status changes prevent an
in-flight result from admitting work. An invalidated coding dispatch records an
admission hold and retries after a fresh pass and after the old run stops.
Completed coding tasks retain their once-per-generation rule.
If a manual rerun of Blocked/In Progress work has a stale approval, move the
issue to Todo to request its new review; the rerun cannot grant itself a pass.

**Deployment compatibility:** the Azure readiness modules must match
`automation/worker.py` and shared policy code on every mapped repository's
workflow branch. The broker and runner require the
`readiness-v1:<contract hash>` start protocol (including the prompt, schema,
budget, `repository-selection-v1` reporting, and `linear-steering-v1` contracts) and an explicit
broker-selected phase. An old runner is denied start; a new runner receiving an
old broker response stops before execution. Selection reporting versions this
hash too: Azure and every workflow branch need matching shared policy/reporting
sources. Readiness dispatches use the existing worker workflow and inputs; their
separate receipts share the admitted issue's repository slot. No generation
reset, new credentials, or expanded App permissions are needed; the broker
creates `ClusterforkReadiness`.
The automatic deployment workflow drains active workers before updating the
broker. Other mapped repositories also need compatible worker sources.

## Repository queues

Eligible Todo issues enter one durable priority queue per GitHub repository before readiness review. Projects targeting
the same case-insensitive repository share its queue; different repositories advance independently. Waiting issues are
admitted in Linear priority order: **Urgent → High → Normal → Low → No priority** (values **1 → 2 → 3 → 4 → 0**).
Within each priority, FIFO follows first eligible observations committed to storage. A project scan inserts its complete
batch before dispatch, assigning sequence numbers by creation time and UUID. Later events/scans, priority edits, retries,
and restarts retain those sequences; missed historical Todo transitions cannot be reconstructed.

Issue webhooks, project scans, and timer reconciliation persist updated priorities for waiting entries without changing
their sequence or queued-label intent. Before reserving a free slot, Azure refreshes waiting eligibility and priority,
including during a handoff and for legacy rows without priority. Priority updates fence concurrent admission choices.
Temporary Linear read failures retain the last known priority and durable position for retry. Once selected, an issue
keeps its slot through review, dispatch retries, quota waits, coding, and unresolved PRs until normal release conditions
are met; an urgent arrival or priority edit only affects the next admission. Imported pre-queue dispatches still precede
new Todo work because they have already crossed the dispatch boundary.

Azure Table `ClusterforkRepositoryQueue` partitions by lowercase repository name. Transactions insert each entry with
its sequence and atomically reserve the head/slot. Waiting issues remain Todo without a runner or account; context
changes can require another readiness review before coding. Stable queue comments identify the preceding issue and
whether it is running, waiting for CI/quota, blocked, or awaiting confirmation of an ambiguous dispatch.

Queue insertion also records pending intent for the team-scoped `clusterfork: queued` label. Azure adds it only after
current eligibility passes and the waiting entry commits, through either webhook ingestion or timer/project-scan
recovery. Raw webhook receipt does not add it. The marker means **received and waiting**; it does not assert that
readiness passed or guarantee coding. Waiting issues retain Todo, human labels, stage labels, and project enrollment
through `clusterfork-symphony`. They start no readiness review, development runner, or account lease.

Admission also records a versioned, pending Linear label update in that same transaction. Azure attempts to add the
team-scoped `clusterfork: selected` label and remove `clusterfork: queued` immediately after reservation, before steering
comment scans, historical label/comment repairs, or dispatching the readiness reviewer/development worker, including on
the timer. An immediately admitted issue ends up selected without a queued marker. Webhook receipt and queue insertion
alone do not select waiting issues. The selected marker means **Azure reserved this repository's slot**; it does not
confirm that GitHub accepted a dispatch or a runner started. It leaves human assignments and unrelated labels intact
and coexists with the current stage label. It stays through readiness, dispatch
retries or uncertainty, quota waits, pending PRs, and development blockers until the slot is released. Readiness
rejection clears it immediately; a durable reporting intent prevents reconciliation from restoring it while the rejected
runner stops. The queue releases that slot and advances once GitHub confirms the old runner stopped.

Release atomically records a new pending label version. Azure reports the next admitted issue before repairing
historical removals and comments. Label failures are reporting failures: they can temporarily delay the visible marker,
but do not discard ownership, outcomes, FIFO positions, or dispatch receipts, or trigger another worker. Pending queued
and selected additions/removals survive restarts and retry on events and the shared timer, including removals after the
queue empties. Reporters recheck current queue/slot state across repositories after writes and repair superseded updates
without touching stage labels. The timer also refreshes waiting and historical entries to repair an ambiguous or delayed
write whose reporting process stopped before recording its result. Dispatch receipts keep repositories discoverable for
this cleanup after a mapping is removed.

The repository slot has **no expiry**. It spans readiness review, dispatch, coding, validation, publication, pending CI,
review, and merging. A finish callback or auto-merge request does not release it. The shared 15-minute timer advances
automatically after GitHub confirms all matching runs' latest attempts have stopped and the PR has merged, or a
successful worker validated that no changes were needed. The recorded worker identity is checked directly as well,
because run listings can lag. Open PRs hold the slot even with manual merging enabled. Worker starts and completion
observations fence each other using the slot's ETag; stale, duplicate, or manual reruns cannot obtain
account/publication access after their task loses admission. Repository admission is enforced by the broker; the
existing issue-scoped Actions concurrency only guards duplicate issue jobs.

New workers require an explicit `serial_admission: true` confirmation from the broker before inspecting accounts or
starting coding. Older deployments that omit it fail before agent execution. The health endpoint also reports this flag
so operators can verify the deployed capability. The automatic deployment workflow coordinates broker updates after
changes reach main.

The worker and required `routing-runtime` CI check share `.github/actions/setup-codex`. On Ubuntu 24.04 it installs the
pinned CLI, Bubblewrap, and the supplied AppArmor user-namespace profile. Before any account is leased,
`automation/check_codex_sandbox.py` runs the native read-only sandbox without credentials or a model call. It verifies
repository reads, denied writes inside and outside the repository, and blocked network sockets. Missing sandbox support
fails the runner setup instead of repeatedly spending quota on reviewers that cannot inspect files. The setup follows
the [Ubuntu sandbox prerequisites](https://learn.chatgpt.com/docs/sandboxing).

Failures, missing outcomes, closed unmerged PRs, and merge/CI blockers pause the repository. Resolve the PR or manually
rerun the **same** admitted task to repair it. To abandon it, cancel the Linear issue, stop/cancel its GitHub run, and
close or merge any remaining PR. Reconciliation verifies those GitHub facts before releasing the slot. Changing Linear
status to Done, archiving, or removing enrollment alone does not abandon active work. An expired worker/account lease
never proves that the worker stopped.

The head retains its position during quota waits and retries after its existing 15-minute delay, once its prior GitHub
run has stopped. Before every dispatch, the dispatcher fetches current enrollment, project, archive, cancellation, and
ready-state data. Events and reconciliation also recheck waiting entries while the head is active, including empty
project scans and issues moved out of mapped projects. Leaving Todo, cancellation, issue/project archive, project moves,
or loss of enrollment durably skips a waiting entry and removes its queued marker without waiting for the active task
to finish. An undispatched issue that later becomes eligible rejoins with a new sequence, at the tail of its priority.
A temporary eligibility-read failure retains its durable position for retry. Before coding a newly admitted task, the
worker fetches the latest target branch and creates its branch from that revision, including the preceding task's merged
changes.

Dispatch receipts retain their original workflow and target branch so active work can still be checked after its project
mapping changes or is removed. Undispatched entries with removed/remapped destinations are skipped. Legacy receipts
without those fields require restoring their mapping for verification.

Restart recovery reuses queue entries, slots, and dispatch receipts. Pre-queue receipts are imported before new Todo
work is admitted. A reservation interrupted before receipt creation can resume; a receipt left `dispatching` or
`uncertain` is never blindly resent. Reconciliation discovers its run and deterministic PR branch. If no run can be
confirmed, that repository stays paused for investigation or explicit cancellation. Incomplete GitHub responses also
keep the slot held.

### Selection feedback latency

`host.json` caps idle queue polling at **one second**, replacing Azure's [one-minute
default](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-storage-queue#hostjson-settings). It
allows eight events per host concurrently so an unrelated scan cannot monopolize the consumer; durable slot transactions
still enforce repository serialization. Faster polling increases idle storage transactions. With a warm consumer, no
backlog, and a free repository, expect feedback in seconds: polling plus current Linear eligibility, durable admission,
and the label API calls. Label lookup/creation can add calls. Each HTTP request has a 20-second timeout. Linear webhook
delivery, Azure cold starts/scaling, backlog, rate limits, and Linear client refresh can add delay; one second is a
polling cap, not an end-to-end SLA. A busy repository stays queued until authoritative completion checks allow
admission, normally on the 15-minute timer. Failed label writes retry on events and that timer; lowering polling does
not shorten the timer or failed-message retry backoff.

`Clusterfork timing` information logs record `webhook_received`, `event_enqueued`, `event_processing`,
`repository_admitted`, and `selection_label_started`/`written`/`deferred`. Records contain IDs, versions, outcomes, and
UTC epoch seconds only. Queue rows retain the first observation's `source_updated_at` (issue webhook `updatedAt`, when
present), `webhook_at`, `received_at`, `enqueued_at`, `processing_at`, and `queued_at`. `selected_at` is stamped in the
reservation transaction; `repository_admitted.at` confirms its commit. `selection_reported_at` records the last
acknowledged queued/selected projection checked against current queue/slot state and version. Compare processing minus
enqueue for queue wait and label-written minus admission for reporting latency. A write acknowledgement measures Linear
API acceptance; it cannot measure the user's UI refresh. Project scans lack an issue transition timestamp, and duplicate events retain the
original queue timestamps. No issue content or credentials enter these diagnostics.

KIAN-903 measurements (2026-10-29) compared the original and changed handlers with a virtual clock and mocked services.
Source update / webhook emission / receipt / queue-send acknowledgement / consumer offsets were **0 / 0.10 / 0.20 /
0.25 / 0.75s** in both. With a 12-second steering scan, admission improved **12.85 → 0.85s** and label acknowledgement
**13.15 → 1.15s**. Separately, 8-second historical removal plus 4-second comment repair improved consumer-to-label time
**12.3 → 0.3s**. With priority refresh enabled (KIAN-906), the same single-issue simulation records admission at **0.95s**
and label acknowledgement at **1.25s**. These isolate ordering, not live service performance. KIAN-902's reported
18:23:05 → 18:23:42 UTC gap cannot be attributed without its traces; default polling was a plausible contributor, not a
confirmed incident cause.

## Automatic deployments

`.github/workflows/deploy-azure.yml` runs on pushes to main and supports manual
recovery with `workflow_dispatch`. Every run reconciles the
[Azure Terraform stack](azure-infrastructure.md) before publishing the package.
Deployment runs are serialized, with later pushes retaining the newest pending
run. [Setup](remote-development-setup.md#automatic-deployment-identity) owns the
GitHub environment and Azure identity configuration.

Before deployment, `automation/deploy.py` writes a durable pause to
`ClusterforkOperations` (partition `control`). New readiness/development
dispatches stop, while existing workers retain their broker access and can
finish. The workflow waits for the exact main revision's `test` CI workflow to
succeed and for all worker runs, including queued runs and older manual reruns,
to stop. Draining allows 330 minutes within a six-hour deployment job.

Only the allowlisted Function App ZIP is published, using Flex Consumption's
remote build. `deployment.json` stamps its Git revision and broker source digest.
The health endpoint returns this metadata, the serial-admission capability, and
the deployment pause state. Startup failures return 503 with a `runtime_error`
class, without credentials or raw exception messages. The workflow resumes dispatch only after the live
revision/digest match the package, configuration is complete, and main still
points to that revision. If main advances, the newer deployment inherits the
pause. CI, drain, deployment, or health failures leave the pause in place; a
corrected main push or manual deployment workflow run performs recovery. The
pause has no time-based automatic expiry.

## Agent profiles

`automation/azure/projects.json` enables only Codex:

| Agent | Model ID | Reasoning effort |
| --- | --- | --- |
| Codex | `gpt-6.1-sol` | `max` |

The registry requires an explicit Codex profile and supported effort value. Each
phase uses one account selected by available quota. There is no complexity
classifier or model fallback. Antigravity is deferred. The runner verifies that
the pinned model and effort are in each account's live model catalog before use.

`quota_selection.py` implements the decision over provider-normalized snapshots:
choose the account with the lowest used percentage in its **most depleted**
applicable window. Both short and weekly/model limits matter. Skip busy, blocked,
unknown, stale, and nearly exhausted accounts. Configuration currently requires
at least 20% remaining, a snapshot at most 60 seconds old, and one active task per
account. Those are initial configurable operating values, not a prediction of a
task's consumption. If no account qualifies, return no selection and wait.

The runner calls Codex app-server `account/read`, `model/list`, and
`account/rateLimits/read` in isolated temporary Codex homes. It probes available
accounts under leases, selects by the worst applicable quota window, then
reserves and rechecks the chosen account before starting the development app-server
thread (readiness still uses `codex exec`).
Unknown or denied usage never counts as unused quota. A lack of capacity records
`waiting_for_quota` and allows reconciliation to retry after 15 minutes.
The registry owns these pins and selection thresholds.

## Automatic merges

Project mappings default `auto_merge` to `true`; set it to `false` for a project
that should stop at an open PR. The broker sends this policy to the runner.
New PRs and recovered open PRs request native GitHub auto-merge using squash,
bound to the observed head commit. The runner verifies the repository, base,
worker branch, bot author, and dispatch marker before requesting a merge.

The runner refuses auto-merge if repository auto-merge or required PR checks are
missing. It reads PR-specific required checks using its existing App permissions;
no Administration grant is needed. GitHub handles pending/failed CI and merge
conflicts; the worker never uses `--admin` or force-merges. A completed worker
outcome means its coding/publication work finished; the PR can still be waiting
for CI. The Azure outcome also records whether auto-merge was requested.
Recovery recognizes merged
PRs so a retry does not repeat work if merging preceded outcome persistence.
Reference: [GitHub auto-merge](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request).

## Linear lifecycle

Each project can configure `linear_states` with the UUIDs for `in_progress`,
`blocked`, and `done`. Clusterfork maps these to In Progress, Blocked,
and Done. Only ready states such as Todo dispatch new issues.

| Event | Linear status |
| --- | --- |
| Worker starts, or required CI is pending | In Progress |
| Merge conflicts, failed/canceled required checks, closed unmerged PR, draft PR, or unmet merge requirements | Blocked |
| Worker fails, is canceled, or ends without a recorded outcome | Blocked |
| PR merges, or validated work needs no repository changes | Done |
| No account has enough quota | Todo; retries after 15 minutes |

Start and finish attempt immediate updates. The existing shared 15-minute timer
checks later PR/CI changes and retries failed Linear writes; no GitHub webhook or
continuously running worker is needed. It updates one owned comment with the
current reason, PR link, and worker-run link. Durable synchronization leases and
a stable UUIDv4 comment ID prevent duplicate comments after retries.

The authenticated worker also reports its current stage through the broker.
Linear creates team-scoped issue labels as needed and keeps one current stage
label on the issue:

| Label | When it applies |
| --- | --- |
| `clusterfork: runner initialization` | Worker starts and prepares its checkout |
| `clusterfork: account selection` | Worker inspects and reserves account quota |
| `clusterfork: coding` | Selected Codex account begins implementation |
| `clusterfork: final testing and review` | Worker checks the resulting changes and runs configured validation |
| `clusterfork: publication` | Worker prepares and publishes the result |
| `clusterfork: PR review and CI` | Open PR awaits checks, review, or merging |
| `clusterfork: waiting for quota` | No account qualifies; issue awaits an automatic retry |

Stage changes replace only these owned labels; other issue labels are preserved.
Completion and blockers clear the stage label. PR/CI reconciliation can restore
the review label when a blocker clears. This does not create a separate review
agent; checks inside the coding session are part of the coding stage.

After selection and the final quota recheck, a separate start comment records
the account email (when available), stable account identity, plan, agent, model,
reasoning effort, and applicable quota windows before coding. Each reading shows
used/remaining percentages and reset times, with weekly windows identified by
their provider-reported duration. The selected account stays leased through
validation and publication. On exit, including execution or validation failure,
the worker attempts a fresh usage reading before releasing it.

A separate finish comment repeats the account/model details, compares before and
after usage in percentage points, and reports total time from worker start to its
finish callback, including checkout preparation, account selection, coding,
validation, and publication. It excludes subsequent PR/CI waiting. Window resets,
missing readings, and unreported weekly windows are explicit rather than counted
as zero usage. Readings may lag or include other account activity. An abandoned
or canceled runner without a finish callback reports unavailable final usage and
duration. Start/finish comments use distinct stable UUIDv4 IDs per run attempt;
reconciliation retries them without overwriting the start snapshot or adding
duplicate comments. Only quota measurements and account display fields cross the
reporting boundary; Linear credentials stay in Azure.

Pending CI is normal progress. A blocked PR returns to In Progress when its
blocker clears, then Done after merging. Unknown/incomplete GitHub responses leave
the previous state unchanged. Archived issues/projects, removed enrollment labels,
project moves, and manually completed/canceled issues are respected. Publication
accepts the worker's In Progress state while preserving other eligibility checks.
A manual GitHub job rerun can resume its still-admitted Blocked work after the
previous worker stops; status updates alone never
reset the completed dispatch receipt or automatically rerun a failed agent.

## Retries and troubleshooting

Completed issues dispatch once per registry `generation`. Editing an issue or
moving it out of Todo and back does not reset completion. Capacity waits retry
through reconciliation; execution failures are marked Blocked and require a
GitHub job rerun. Existing open PRs are recovered by their deterministic branch name.
Changing generation permits **all** eligible issues to dispatch again.

Each project can set `agent_timeout_minutes` in `automation/azure/projects.json`.
The default is **300 minutes (five hours)**; allowed values are integers from 1
through 330. The hosted job allows **360 minutes (six hours)**, leaving time for
setup, validation, and publication. GitHub-hosted jobs have a
[six-hour execution ceiling](https://docs.github.com/en/actions/reference/limits).
Settings are read when a worker starts; changing them does not extend an already
running attempt. Automatic checkpoint/continuation across jobs is not implemented.

Configured validation commands run after the agent finishes. Publication rechecks
current project enrollment and status, pushes a separate branch, opens a PR, and requests the
configured merge policy. GitHub waits for required checks before automatic merging.
Workflow-file edits are refused because the App has no Workflows permission.
Durable agent-session resume and automatic retries after a mid-task usage limit
are not implemented. Linear credentials stay in Azure and are not sent to the agent.

Azure Table `ClusterforkDispatches` records `dispatching`, `dispatched`, `rejected`, or
`uncertain`. `dispatched` means GitHub accepted the request; verify actual runner
success in Actions. Atomic receipt creation prevents concurrent delivery duplicates.
Definite GitHub rejections record `rejected` and retry the same queue head after
at least 60 seconds, through a later event or timer scan. A
timeout/server error may have occurred after GitHub accepted a request: that receipt
is held as `uncertain`, and a crash may leave `dispatching`. The repository queue
keeps the slot while reconciling GitHub; cancel and resolve the task as described
above to abandon it. Do not remove a receipt to bypass admission. This prioritizes avoiding
duplicates over automatically retrying an ambiguous request. It does not claim
exactly-once execution. Inspect `clusterfork-events-poison` after repeated failures.

Common causes: missing App installation, incorrect numeric App ID/PEM, workflow
not yet on `main`, wrong webhook signing secret, missing project label, or an issue
outside the mapped project/Todo state. The registry and app installation must both
allow the destination repository.
