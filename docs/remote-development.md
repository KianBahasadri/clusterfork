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
[Dated validation records](archive/remote-development-validation.md) retain the
live routing and merge evidence without treating it as current health.

## Components and dispatch

| Source | Responsibility |
| --- | --- |
| `automation/azure/function_app.py` | Signed webhook, queue trigger, health, worker broker, and shared 15-minute timer |
| `dispatch_core.py`, `dispatch_clients.py` in that directory | Current Linear eligibility, GitHub App dispatch, and durable receipts |
| `worker_core.py`, `worker_store.py`, `quota_selection.py` | Workflow authorization, account leases, fenced credentials, and quota selection |
| `linear_sync.py` | Linear lifecycle and PR/CI reconciliation |
| `repository_queue.py` | Durable repository FIFO, admission, worker fencing, and queue advancement |
| `readiness.py`, `readiness_policy.py` | Durable read-only reviews, structured-result validation, clarification, and revision-bound admission |
| `automation/worker.py`, `automation/codex_runtime.py` | Account inspection, Codex execution, validation, publication, and merge requests |
| `automation/azure/projects.json` | Project mappings, states, model/effort, validation commands, and merge policy |
| `.github/workflows/clusterfork-worker.yml` | Pinned Codex CLI on `ubuntu-24.04`, six-hour job, read/OIDC permissions |
| `.github/workflows/clusterfork-pilot.yml` | Routing-only receipt workflow; no coding agent |

The webhook authenticates raw bytes with HMAC-SHA256 and requires a timestamp
within five minutes. It queues only entity IDs from Issue/Project create/update
events and returns 200 only after a needed queue send succeeds. The consumer
fetches current Linear data, then checks the mapped project, its configured
enrollment project label, archive flags, and configured ready-state IDs. An issue
label does not enroll a project. Removing enrollment prevents new dispatches but
does not cancel submitted jobs. See
[setup](remote-development-setup.md#linear-webhook-and-first-run) for the enrollment label.

The shared timer reconciles worker/PR status and scans mapped projects every
15 minutes. Project labels are fetched once per issue page to stay within
Linear's query-complexity limit.

## Todo readiness gate

An enrolled issue in a configured Todo state enters its repository FIFO first.
Only the issue holding that repository's slot receives a bounded review of
its title, description, relevant code, and repository rules. Reviewers read
`AGENTS.md` and the relevant documentation. They judge missing decisions, not
description length: a precise one-line fix can pass. They assess intended outcome,
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

Azure independently validates every criterion, blocker, and verdict, including
consistency between them. A valid pass is recorded in `ClusterforkReadiness`
before development dispatch. Approval binds to the exact title and
description content revision, project, versioned review policy/schema and project
configuration, model profile, and target branch commit. Tracker comments, labels,
and lifecycle updates do not invalidate their own review. Conservatively, any
target branch change invalidates context, including a preceding queued task's
merge. Admission, dispatch retries, worker start, account access, the transition
to coding, and publication recheck approval; coding must use the reviewed base
commit. Material edits during account selection prevent coding and publication.

A valid `needs_clarification` result returns the issue to its team's Backlog,
adds only `clusterfork: needs clarification`, and updates one stable review
comment with one to four missing decisions, their consequences, and answerable
questions. The team must have a unique backlog-type state named Backlog, or a
single backlog-type state. Unrelated labels and human status changes are
preserved. Update the description and manually move Backlog to Todo for another
review. Successful resubmission clears the owned clarification label. Reviews
and retries use separate receipts, so rejection/resubmission needs no registry
generation change and never consumes or resets a completed coding receipt.

Unavailable context, dispatch failures, timeouts, missing/malformed results, and
reviewer-reported infrastructure uncertainty hold coding with a visible retry
comment; they do not classify the issue as ambiguous. The shared timer retries
temporary failures after 15 minutes and verifies the old GitHub attempt stopped
before launching another. An uncertain/crashed dispatch without a confirmed run
is held for discovery and investigation, never blindly resent. Duplicate issue
events, project scans, and callbacks share the durable review record. Cancellation,
archive, loss of enrollment, project moves, or human status changes prevent an
in-flight result from admitting work. A coding dispatch invalidated while waiting
for a runner records an admission hold and retries after a fresh pass and after
the old run stops. Completed coding tasks retain their once-per-generation rule.
If a manual rerun of Blocked/In Progress work has a stale approval, move the
issue to Todo to request its new review; the rerun cannot grant itself a pass.

**Deployment compatibility:** deploy the Azure package (including both readiness
modules) together with compatible `automation/worker.py` and shared policy code
on every mapped repository's workflow branch. The broker and runner require the
`readiness-v1:<contract hash>` start protocol (including the prompt, schema, and
budget hash) and an explicit broker-selected phase. An old runner is denied
start; a new runner receiving an old broker response stops before
execution. Readiness dispatches use the existing worker workflow and inputs;
they are not imported into the coding FIFO. No generation reset, new credentials,
or expanded App permissions are needed; the broker creates `ClusterforkReadiness`.
Drain active workers before rollout, since credentials already issued by an old
broker cannot be retroactively withdrawn. Keep routing paused during a mixed
deployment, publish the compatible worker sources to the workflow branch, deploy
the broker package, then resume routing. This change does not deploy either side.

## Repository queues

Eligible Todo issues enter a durable FIFO before readiness review, per GitHub
repository. Multiple Linear projects mapped to the same repository share its
queue; repository names are case insensitive. Different repositories advance
independently. Queue order is
the sequence of first eligible observations committed to storage. A project scan
inserts its entire batch before dispatch, ordered by issue creation time and UUID.
Later deliveries and scans retain existing positions. This does not reconstruct
the historical order of Todo transitions missed before discovery.

Azure Table `ClusterforkRepositoryQueue` uses a partition derived from the
lowercase repository name. Transactions assign the next sequence and insert its
entry together, and atomically reserve the head and repository slot. Waiting
issues remain Todo without a review/development runner or account. If context changes,
the head can receive another readiness review before coding. Stable queue comments
explain which issue they follow and whether it is running, waiting for CI/quota,
blocked, or awaiting confirmation of an ambiguous dispatch.

The repository slot has **no expiry**. It spans readiness review, dispatch, coding, validation,
publication, pending CI, review, and merging. A finish callback or auto-merge
request does not release it. The shared 15-minute timer advances automatically
after GitHub confirms all matching runs' latest attempts have stopped and the
PR has merged, or a successful worker validated that no changes were needed.
The recorded worker identity is checked directly as well, because run listings
can lag. Open PRs hold the slot even with manual merging enabled. Worker starts
and completion observations fence each other using the slot's ETag; stale,
duplicate, or manual reruns cannot obtain account/publication access after their
task loses admission. Repository admission is enforced by the broker; the
existing issue-scoped Actions concurrency only guards duplicate issue jobs.

New workers require an explicit `serial_admission: true` confirmation from the
broker before inspecting accounts or starting coding. Older deployments that
omit it fail before agent execution. The health endpoint also reports this flag
so operators can verify the deployed capability. Merging a PR does not deploy
Azure: pause worker dispatches, let active workers finish, deploy the matching
broker, verify this health flag, then resume dispatches.

Failures, missing outcomes, closed unmerged PRs, and merge/CI blockers pause the
repository. Resolve the PR or manually rerun the **same** admitted task to repair
it. To abandon it, cancel the Linear issue, stop/cancel its GitHub run, and close
or merge any remaining PR. Reconciliation verifies those GitHub facts before
releasing the slot. Changing Linear status to Done, archiving, or removing
enrollment alone does not abandon active work. An expired worker/account lease
never proves that the worker stopped.

The head retains its position during quota waits and retries after its existing
15-minute delay, once its prior GitHub run has stopped. Before every dispatch,
the dispatcher fetches current enrollment, project, archive, cancellation, and
ready-state data. Ineligible queued entries are skipped; an undispatched issue
that later becomes eligible rejoins at the tail. Before coding a newly admitted
task, the worker fetches the latest target branch and creates its branch from
that revision, including the preceding task's merged changes.

Dispatch receipts retain their original workflow and target branch so active
work can still be checked after its project mapping changes or is removed.
Undispatched entries with removed/remapped destinations are skipped. Legacy
receipts without those fields require restoring their mapping for verification.

Restart recovery reuses queue entries, slots, and dispatch receipts. Pre-queue
receipts are imported before new Todo work is admitted. A reservation interrupted
before receipt creation can resume; a receipt left `dispatching` or `uncertain`
is never blindly resent. Reconciliation discovers its run and deterministic PR
branch. If no run can be confirmed, that repository stays paused for investigation
or explicit cancellation. Incomplete GitHub responses also keep the slot held.

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
reserves and rechecks the chosen account before starting `codex exec`.
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
