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
Startup failure before coding retries after 60 seconds; temporary review/capacity
failures retry after 15 minutes. Retries require stop/publication proof, current
eligibility and fresh review on context changes, retaining the grant. Repair
startup/capacity retries retain parent evidence and [one repair budget](remote-development.md#single-attempt-auto-repair).

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
interrupt it. Events/periodic observations discover satisfied/removed prerequisites.

## Linear feedback

At most one owned execution-status label appears: `clusterfork: queued`,
`clusterfork: selected`, `clusterfork: needs clarification`, or a stage:
`clusterfork: runner initialization`, `clusterfork: account selection`,
`clusterfork: coding`, `clusterfork: final testing and review`,
`clusterfork: publication`, `clusterfork: PR review and CI`, `clusterfork: waiting for quota`.

Precedence is unresolved clarification, current execution/PR/quota stage, selected,
then queued. Clarification survives resubmission/temporary retries until a passing
review. Terminal/skipped tasks have none. Queued means waiting; selected means
grant reserved, not review passed/agent running. Rejection clears selection while
stop is verified. Indicators never change grants, eligibility or retry policy.
`clusterfork: auto-repair` is an independent attempt-kind label.

Reporting removes conflicting indicators before adding the desired one. Failed/
ambiguous writes retry idempotently and remain pending until confirmed, even if
comment text is unchanged. Preserve human assignments, unrelated labels and
project enrollment. One stable comment explains dependencies, holds, run/PR links
or the active predecessor; grant changes refresh waiters. Reporting retries retain
identity/FIFO/dispatch and cannot start work.

Webhook commit wakes observation; background ticks run every 30 seconds, full
project scans every two minutes. These are intervals, not feedback latency promises.
Settled terminal tasks need no per-task polling; project lists recover missed events.
