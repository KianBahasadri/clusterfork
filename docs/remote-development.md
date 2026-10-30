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
within five minutes. It queues entity IDs and diagnostic timestamps from Issue create/update/remove,
Project/Comment create/update, and IssueRelation create/update/remove events when delivered, and returns 200 only after a needed queue send succeeds. Comment events use
the separate [agent steering](remote-development-steering.md) path. The issue/project consumer
fetches current Linear data, then checks the mapped project, its configured
enrollment project label, archive flags, and configured ready-state IDs. An issue
label does not enroll a project. Removing enrollment prevents new dispatches but
does not cancel submitted jobs. See
[setup](remote-development-setup.md#linear-webhook-and-first-run) for the enrollment label.

The shared timer reconciles worker/PR status and scans mapped projects every
15 minutes. Project labels are fetched once per issue page to stay within
Linear's query-complexity limit.

Per-issue Linear and GitHub reconciliation calls are limited to active work and
pending reports. Readiness checks local review state before fetching an issue:
reported passes and applied rejections need no timer lookup. Todo reentry is
discovered by Issue webhooks and project issue-list scans. Selection reporting
retries only queue rows with durable pending label intent. Steering comment
scans cover workers still accepting instructions and issues with queued,
in-flight, or unreported instructions. Acknowledgement repair retries changed or
pending reports; it does not refresh every historical acknowledgement.

Label and steering acknowledgement writers persist pending intent and a unique
in-flight marker before calling Linear. A newer successful reporter preserves
any older writer's marker, so a late write, failed response, or interrupted
process remains recoverable. Successful reports retire only their own marker;
abandoned markers expire after five minutes and are repaired on the next timer
pass. Stable comment IDs prevent duplicate steering acknowledgements, and
reporting retries never resend instructions to an agent.

Once the worker, repository queue, readiness review, labels, lifecycle sync, and
steering reports have settled, later ticks and Project scans make no per-issue
Linear or GitHub calls for that historical task. Paginated project issue-list
scans and local durable-state scans continue. Late `@clusterfork` comments on
completed issues receive non-delivery replies through Issue or Comment webhooks;
the timer does not poll completed issue threads to discover them.

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

A rejected review moves the issue to the configured Needs attention state (`blocked` lifecycle key;
the legacy fallback looks for a unique team state named Blocked), clears `clusterfork: selected` and active stage labels,
and does not retry automatically. Unrelated labels and human status changes are
preserved. A valid `needs_clarification` result adds `clusterfork: needs clarification`
and explains one to four missing decisions, consequences, and answerable questions.
Invalid results retain a bounded, credential-redacted output snapshot and a specific
field, consistency, parsing, or binding error. Each attempt keeps a stable review
comment with its feedback and run link, so later reviews see rejections and replies.
Clarification and rejection comments include the top-level `notify_user_url` from
`automation/azure/projects.json` (currently Kian's Linear profile URL), which Linear
turns into an `@kian` mention for Inbox and email alerts. Passed reviews and temporary
hold/retry comments omit the notification mention.
Address feedback in the description or comments, then manually move Needs attention to Todo
for another review. Resubmission clears the owned clarification label after a pass;
it needs no generation change and never consumes or resets a completed coding receipt.

Unavailable context, dispatch failures, timeouts, quota waits, and infrastructure
uncertainty hold coding with a visible retry comment. The shared timer retries
temporary failures after 15 minutes and verifies the old GitHub attempt stopped
before launching another. Deployment startup deferrals use the recovery below;
they do not incur this failure cooldown. An uncertain/crashed dispatch without a confirmed run
is held for discovery and investigation, never blindly resent. Duplicate issue
events, project scans, and callbacks share the durable review record. Cancellation,
archive, loss of enrollment, project moves, or human status changes prevent an
in-flight result from admitting work. An invalidated coding dispatch records an
admission hold and retries after a fresh pass and after the old run stops.
Completed coding tasks retain their once-per-generation rule.
If a manual rerun of Needs attention/In Progress work has a stale approval, move the
issue to Todo to request its new review; the rerun cannot grant itself a pass.

**Deployment compatibility:** the Azure readiness modules must match
`automation/worker.py` and shared policy code on every mapped repository's
workflow branch. The broker and runner require the
`readiness-v1:<contract hash>` start protocol (including the prompt, schema,
budget, `repository-selection-v1`, `linear-steering-v1`, and `auto-repair-v1` contracts) and an explicit
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

[Repository queues](remote-development-queue.md) covers priority and FIFO admission, issue dependencies,
queued/selected markers, worker fencing, slot release, and selection feedback timing.

## Automatic deployments

[Deployment pauses and recovery](remote-development-deployments.md) owns verified
deployment, worker draining, startup deferral, and prompt reconciliation after resume.

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
`blocked`, and `done`. Clusterfork maps these to In Progress, Needs attention,
and Done. Only ready states such as Todo dispatch new issues.

| Event | Linear status |
| --- | --- |
| Worker starts, or required CI is pending | In Progress |
| Merge conflicts, failed/canceled required checks, closed unmerged PR, draft PR, or unmet merge requirements | Needs attention |
| Worker/check failure eligible for its single automatic repair | In Progress while repairing |
| Failed repair, cancellation, or missing outcome without a confirmed failure | Needs attention |
| PR merges, or validated work needs no repository changes | Done |
| No account has enough quota | Todo; retries after 15 minutes |

Start and finish attempt immediate updates. The existing shared 15-minute timer
checks later PR/CI changes and retries failed Linear writes; no GitHub webhook or
continuously running worker is needed. It updates one owned comment with the
current reason, PR link, and worker-run link. Durable synchronization leases and
a stable UUIDv4 comment ID prevent duplicate comments after retries.

The shared comment adapter reads the authenticated viewer and the legacy
comment's author. When they differ (or author metadata is unavailable), it
preserves that comment and its replies and creates one replacement under the
current actor. The replacement UUIDv4 derives from the original logical comment
ID and viewer ID, so reconciliation and process restarts find and update the
same replacement, including after a lost creation response. A definite forbidden
update can also trigger this migration when author metadata appeared to match;
timeouts and other transient update failures retry the existing comment. This
applies to lifecycle, attempt, readiness, queue, and steering reports.

[KIAN-923's reporting investigation](remote-development-deployments.md#status-reporting-investigation)
owns concurrent/ambiguous comment-creation recovery and readiness reporting isolation.

Confirmed completion records `completion_owner` in `ClusterforkLinearSync`
before external writes. It attempts the explanation, stage removal, and Done
transition independently. Failed writes remain pending even when the issue is
already Done; successful comment signatures are retained, and `terminal=true`
is saved only after all reports settle. Nonterminal transitions still require
their explanation first. A manually completed issue without this durable
completion intent is preserved. Reporting repair never resets dispatch receipts
or admits another coding worker for completed work.

The KIAN-921 investigation confirmed a failure path: a rejected `commentUpdate`
aborted synchronization before stage removal and the state transition, while
the GraphQL rejection details were discarded. Regression coverage reproduces
this with a comment owned by the previous identity and verifies recovery. The
2026-10-29 KIAN-915 HTTP/deferred logs did not retain the rejection payload;
ownership denial remains an incident hypothesis, not a confirmed API response.
`linear_graphql_rejected` now records allowlisted error code/type, the root
field, a capped error count, and rejection category (`errors`,
`invalid_response`, or `unsuccessful_mutation`). `linear_sync_deferred` retains
that classification with dispatch/run identity. Raw messages, response bodies,
variables, headers, and credentials are excluded. HTTP `operation_completed`
still describes transport completion; it does not establish GraphQL success.

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
A manual GitHub job rerun can resume still-admitted work after the previous worker
stops, unless its slot was handed to a repair or its single repair worker failed.
Status updates never reset a completed dispatch receipt.

## Single-attempt auto-repair

After GitHub confirms all matching workers and the recorded attempt stopped, an
admitted coding/validation failure or completed failing required PR checks can
consume **one** repair budget per dispatch key. Pending CI, intentional cancellation,
unknown dispatches, readiness/dependency holds, and merge-policy blockers do not
consume it. The parent queue row atomically records `repair_attempted`, a stable
`repair_issue_id`, and immutable context under the slot ETag before external creation;
the receipt mirrors that intent and prior lifecycle writers drain. Interrupted creation reuses the UUID.

Azure creates a High-priority Todo sub-issue in the same project, labeled
`clusterfork: auto-repair`, and atomically hands it the occupied slot. Only a child
bound to that durable intent and its generated content bypasses readiness clarification. Enrollment,
dependency, worker identity, account leases, and publication gates remain enforced.
The worker checks out the **parent's** `clusterfork/<identifier-lowercase>-<key[:8]>`
branch, investigates, runs configured validation, and pushes to the existing PR or
creates its missing PR with the parent's dispatch marker. Required checks and native
merge requirements and the normal publication permission checks still apply;
`--admin` bypasses remain refused.

The repair description includes the original task's title and requirements. A
failure before publication can leave no recoverable branch; in that case the
repair must implement the original task from the reviewed base. Passing the
validation suite alone does not establish completion of the missing feature.
Generated GitHub link destinations use Linear's saved Markdown form so exact
content binding survives creation; later title or description edits still block admission.

The label **or** persisted `is_auto_repair` metadata forbids another generation; failed
repair workers cannot be rerun automatically or through broker admission.
If it fails or its completed required checks still fail, both issues become Blocked
(the configured Needs attention state) and the slot stays held for human review.
After merge, or validated completion with no changes/PR remaining, both become Done;
the same reconciliation pass releases the slot and dispatches the next runnable issue.

`notify_user_url` in `automation/azure/projects.json` configures the profile mention. Each terminal
outcome writes one stable `comment_id(key, "auto-repair-report")` comment per issue,
starting with Kian's profile URL so the Clusterfork OAuth app can notify `@kian`.
The Auto-Repair Report links the trigger, repair issue/run and PR, summarizes bounded diagnostics,
prepared commit/file count and validation, and states whether the queue is flowing or awaiting review.
Reports exclude credentials, raw logs, environment dumps and agent transcripts. Pending projections
and in-flight markers recover failed, ambiguous or interrupted writes even after the next task starts.

## Retries and troubleshooting

Completed issues dispatch once per registry `generation`. Editing an issue or
moving it out of Todo and back does not reset completion. Capacity waits retry
through reconciliation; eligible execution/check failures use the single repair above.
Other blockers require human recovery. Existing PRs use their deterministic branch name.
The [recovery tools](remote-development-recovery.md) cover verified operator repairs of stored content bindings.
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
Both readiness and coding prompts receive the broker's current workflow-publication
capability. Readiness identifies required edits without that capability as a
permission blocker; the worker also refuses unauthorized workflow changes, including
changes recovered from an earlier attempt's branch. Authorized edits follow the same
validation and PR process. [Setup](remote-development-setup.md#github-app-and-repository)
owns the installation grant; [accounts](remote-development-accounts.md#worker-credential-boundary)
owns publication token scope.
Durable agent-session resume is not implemented. A mid-task usage failure can use
the same single repair budget. Linear credentials stay in Azure, outside the agent.

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
