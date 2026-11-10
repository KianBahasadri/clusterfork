# Repository queues

The [controller](remote-development-controller.md) owns admission/completion;
this topic owns queue policy, dependencies, release and feedback.
[Table-coordinator behavior](archive/remote-development-functions-queue.md) is historical.

## Ordering and admission

Mapped projects share one grant/queue per case-insensitive GitHub repository;
different repositories advance independently. Runnable order is Linear priority
**Urgent → High → Normal → Low → No priority** (**1 → 2 → 3 → 4 → 0**), then lower
`sortOrder` (missing means zero), then committed FIFO. Scans commit candidates in
creation-time/UUID order. Observations reorder waiting tasks without interrupting
active work; leaving/rejoining eligibility gives a new FIFO tie-break position.

Eligibility requires current explicit project UUID mapping, project enrollment
label, ready state, unarchived/uncanceled status and satisfied dependencies. Issue
labels cannot enroll projects. Unseen ineligible issues create no task/comment;
known ineligible waiters lose queued markers. Completed identities stay complete
for that generation. Waiting consumes no runner/account.

To pause a project, add the **project label** `clusterfork-symphony-paused`
alongside `clusterfork-symphony`. Remove only the pause label to resume. The
pause overrides enrollment for new readiness/coding starts, retries and repairs;
an issue label with the same name has no effect. Enrollment, mappings, attempt
history and retry deadlines are retained. Other projects continue normally.

Running phases keep their existing execution authority and can finish safely.
A completed review waits instead of launching coding. Waiting tasks hold no
runner/account or repository grant once prior execution and publication are
verified stopped; unresolved PRs or uncertain dispatches retain ownership.
The controller rechecks complete project labels before admission, dispatch and
phase startup. Scans recover missed project events; restart retains known
pauses. Waiting-task feedback explains the pause.

One transaction checks runnable head, reserves the unique grant and creates
attempt/dispatch intent; external dispatch follows. Claimed/uncertain outcomes
require stable-identity discovery; an absent run listing never permits resend.
Admission binds the latest target branch, including the preceding merge. Runtime
freshness and operator/deployment holds follow [deployment](remote-development-deployments.md);
holds prevent new attempts without revoking active work.

## Ownership and release

The non-expiring grant spans review, quota waits, coding, validation, publication,
CI and merge. Reordering affects only next admission. Callbacks, merge requests
and expired heartbeats cannot prove stop or release ownership. Completion needs
verified stop for recorded/all matching GitHub attempts plus verified merge, or
validated no-change success without a PR. Open PRs retain grants, including manual-
merge projects. Merge remains authoritative after cleanup failure. Stale/duplicate/
manual reruns cannot inherit another attempt's account/publication grant.

To abandon work, cancel Linear, stop GitHub execution and close/merge publication;
the controller verifies all three. Done/archive/enrollment removal/project moves
alone cannot abandon unresolved work; incomplete observations retain ownership.
Readiness failure releases admission only after all prior matching external
attempts (including reruns) are verified stopped, their fenced phase/probe
credentials are safely released, and publication is verified absent or closed.
The stopped review then stops blocking unrelated queued tasks. An unresolved PR always retains
admission. Cancellation and verified merge/no-change completion retain their
existing evidence requirements.

## Autonomous execution

The committed registry enables `autonomous: true` for the shared controller.
Submitted tasks do not require human review, clarification answers, or manual
Todo resubmission. The agent resolves implementation choices using repository
policy and conservative defaults, records assumptions, and implements and
validates the requested outcome. Unavailable live infrastructure does not block
writing IaC or exercising it with offline fixtures. Credentials, spending,
cancellation and publication controls still apply.

An explicit workflow pause remains authoritative. Returning paused work to Todo
rejoins the queue once source intent is valid and prior runs, credentials and
publication are verified stopped. Resumption retains attempt history and retry
deadlines, rejoins FIFO behind existing peers, and never takes another task's
grant. A stale status projection cannot undo a pause.

Readiness is a read-only implementation assessment. Its output schema permits
only `ready`, satisfied criteria and empty blocker/evidence arrays, preventing
inconsistent model output from becoming a human-action ticket. Transport,
malformed-result, context and execution failures all enter automatic diagnosis
and reassessment. The controller retains the concrete failure and verifies stop,
credential release and publication ownership before another attempt.

The initial automatic delay is 60 seconds, doubles with the durable attempt
ordinal, and caps at one hour. Duplicate callbacks do not move that deadline.
Restart/deployment preserves both history and backoff. There is no terminal
three-review or one-repair limit in autonomous mode, and no Needs attention
projection or request to cycle Todo. Repeated external failures remain visible
as automatic waiting; the controller does not claim they are fixed.

Cooling tasks with no open publication release the repository grant only after
all matching runs are verified stopped, credentials released and publication
verified absent or closed. Other runnable tasks may pass them. Open or uncertain
PRs retain ownership and are reconciled or repaired through fresh assessment.
Completed effects and superseded attempt identities cannot be replayed into
another execution. Existing signed roles, immutable runtime identities and
credential fences remain authoritative.

On first activation, the controller adopts existing noncanceled held work into
this automatic policy without resetting history, replacing credentials, dropping
grants or assuming that a runner stopped. A held issue's old Needs attention
state is accepted as migration metadata and replaced by normal queued/running
feedback. Retired state identities remain durable after the live mapping is removed,
so old signed attention transitions cannot turn a resumed Todo item into a pause.
Human pause/restoration effects never establish a retired state. New tasks are
created in Todo. Operators can still cancel or explicitly
pause work; these actions are not human-review gates and are not overridden.

Legacy registries without the global autonomous setting retain the prior bounded
review API for compatibility. The shared production registry enables the new
policy; tests exercise both compatibility and autonomous execution.

## Issue metadata

Every Symphony issue, including agent-created and follow-up issues, requires an
explicit priority (**1 Urgent, 2 High, 3 Normal, 4 Low**) and an estimate from its
team's configured scale. **No priority (0)** and `estimate: null` are incomplete.
Choose urgency and effort from the issue's scope, complexity and verification
work; preserve existing user values. An explicit zero estimate is retained and
may be selected when the team permits zero and the work justifies it.

`Linear.create_issue` requires both fields before sending a creation mutation and
validates the estimate against the live team settings. Linear's
[scales](https://linear.app/docs/issue-property#scale-range-options) include
linear, Fibonacci, exponential and T-shirt (numeric Fibonacci values), with
optional zero and extended values. Inherited settings resolve through the
parent team. Unavailable or disabled settings cannot establish a valid scale;
setup checks this before enrollment without changing team configuration.

For existing or manually created issues, readiness receives the current fields
and valid estimate values. The assessment returns `issue_metadata` with a
scope-based rationale. Before accepting a ready result, the broker validates the
proposal, re-reads the issue, fills only missing fields, and verifies the write.
Existing values, assignments, descriptions and labels are retained. Ambiguous
writes reconcile through a read rather than repeating the mutation blindly.
Missing fields cleared after approval are completed from the reviewed proposal
again before coding starts. Metadata completion does not invalidate content
approval or reset issue identity, FIFO identity or readiness history. Priority
observations continue to determine queue ordering.

The assessor remains read-only; only the trusted broker writes the metadata.
Missing fields require no human-review handoff. Invalid proposals and failed
reads/writes prevent implementation and follow automatic diagnosis/reassessment.
The worker also supplies the creation rule to coding and repair agents.

## Issue dependencies

Only incoming `blocks` sources count, including accessible unmapped issues, and
must have workflow **type `completed`**. Labels/status names, cancellation,
deletion, unavailable sources or incomplete pagination cannot prove completion.
Verified relation removal removes a prerequisite. Blocked waiters keep priority/
FIFO/feedback without grants; runnable tasks may pass them and cycles do not block
unrelated work.

Refresh dependencies at admission and guarded worker operations. A new pre-coding
blocker holds authorization; once prior attempts stop without a PR, return to the
queue position and release the grant. Blockers added after coding starts do not
interrupt that attempt's context access, coding/testing/publication stages or
publication authorization. An added admission-only edge does not invalidate
approval through the root issue's cached dependency metadata; original snapshots
and approval bindings stay intact. Material changes to reviewed contracts and other authorization losses still
apply. In autonomous mode, complete new evidence read during coding is validated
and recorded without requesting another review; prior contracts remain checked.
Nonblocking relations and workflow state of non-prerequisite references are
discovery metadata rather than material contract changes. Prerequisites gate
queued tasks and every next attempt, including retries and repair.
Events/periodic observations discover satisfied/removed prerequisites. Rejoining
the runnable queue preserves any pending readiness retry delay and budget.

Explicit cancellation or a move to an unrelated paused workflow state withdraws
execution authorization. The legacy Needs attention state does not request human
review or withdraw autonomous work. A signed transition fences worker
operations at intake; observations also detect a pause without waiting for its
webhook. The controller recognizes its own In Progress projection from durable
effects instead of treating every active issue's Needs attention state as eligible.
Pausing never proves runner stop, releases credentials or resolves an open PR.

## Linear feedback

At most one owned execution-status label appears: `clusterfork: queued`,
`clusterfork: selected`, or a stage:
`clusterfork: runner initialization`, `clusterfork: account selection`,
`clusterfork: coding`, `clusterfork: final testing and review`,
`clusterfork: publication`, `clusterfork: PR review and CI`, `clusterfork: waiting for quota`.

Precedence is current execution/PR/quota stage, selected, then queued.
Terminal/skipped tasks have none. Queued means waiting; selected means
grant reserved, not assessment passed/agent running. The old
`clusterfork: needs clarification` label is removed by autonomous reporting;
only legacy registries retain its review semantics.
Indicators never change grants, eligibility or retry policy.
`clusterfork: auto-repair` is an independent attempt-kind label.

Reporting removes conflicting indicators before adding the desired one. Failed/
ambiguous writes retry idempotently and remain pending until confirmed, even if
comment text is unchanged. Preserve human assignments, unrelated labels and
project enrollment. One stable comment explains dependencies, holds, run/PR links
or the active predecessor; grant changes refresh waiters. Reporting retries retain
identity/FIFO/dispatch and cannot start work.

Automatic waiting feedback names the failed operation and reassessment delay,
retains run links, and reports a continuing condition without requesting human
review. Reporting outages do not reset backoff or recreate execution intent.
Every workflow-state transition has its own durable effect identity, independent
of comment/label/report revisions. Controller owns the desired state and epoch;
the driver records intent before writing, then confirmation or uncertainty.
Before a write, the controller rechecks its lifecycle epoch, current enrollment
facts and human watermark. Cleanup cannot replay confirmed states. Ambiguous
writes reconcile against the observed state before repeating a mutation.
Autonomous projections include durable retry and signed-intent identities; a
return to Todo cannot reuse its already-confirmed admission projection.
Targeted history remains a recovery fallback for delayed/missing transitions and
unacknowledged attention writes. Signed events and retained projection provenance
distinguish controller traffic from a later human withdrawal. Transitions to
either the ready states or the active in-progress state preserve execution authorization
rather than triggering withdrawal. A controller write already in flight may land
after a human action; reconciliation restores the latest signed human state rather
than treating that echo as new intent. SQLite does not make remote Linear writes atomic. History reads remain targeted; settled
historical tasks gain no per-task polling. Upgrading adopts existing acknowledged
attention effects and live reported execution without resetting grants or budgets.
Legacy bounded-review resubmission retains its original held-cycle boundary;
late feedback cannot make a signed human action look older than the failure.

Webhook commit wakes observation; background ticks run every 30 seconds, full
project scans every two minutes. These are intervals, not feedback latency promises.
Settled terminal tasks need no per-task polling; project lists recover missed events.
