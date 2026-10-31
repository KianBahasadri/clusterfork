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
Readiness failure releases admission only after all prior matching external
attempts (including reruns) are verified stopped, their fenced phase/probe
credentials are safely released, and publication is verified absent or closed.
The stopped review then stops blocking unrelated queued tasks. An unresolved PR always retains
admission. Cancellation and verified merge/no-change completion retain their
existing evidence requirements.

## Bounded readiness submissions

A reviewer returns `ready`, `needs_clarification` for consequential missing
decisions, or `missing_evidence` with the specific unavailable evidence, access
failure and next action. Evidence failures need no invented product-decision
question. Missing decisions/evidence, unsupported access and malformed results
enter **Needs attention**, with no timed review. Reviewer prose and the legacy
generic `retry` verdict cannot authorize another run. The versioned worker/
controller contract rejects that legacy verdict independently of model hints.

Only structured temporary execution failures observed by the trusted worker or
controller authorize automatic assessment retries: a timeout, verified transport
failure, or a temporary service response (408/429/500/502/503/504 or classified
GraphQL service/rate-limit failure). A generic failed job or command is insufficient.
Each explicit submission permits **three attempts total**, including failed
dispatch/startup/execution with no model result: initial attempt, then retries
after **5 minutes** and **15 minutes**. Dispatch uncertainty still requires stable
identity discovery and never authorizes a resend. Retries require verified stop,
credential/publication checks, current eligibility and deployment/operator holds
to permit new execution; they retain admission while waiting.

SQLite retains the submission cycle, charged attempts, failure class and retry
decision. Restarts, deployment/runtime/generation changes, new attempt IDs,
different error wording, context churn and reporting cannot replenish it. Context changes during
a pending authorized retry use the same budget and latest specification. An
approval invalidated before coding requires intervention and a fresh assessment;
it cannot independently schedule a retry. Exhaustion clears automatic retry intent
and enters Needs attention. Upgrades charge durable existing attempt history and
stop unresolved legacy loops; imported history without a trustworthy submission
boundary does not grant a fresh unattended cycle.

To open one fresh cycle, resolve the feedback and explicitly move the issue from
a non-ready state back to Todo. Only a signed human state-transition event, with
its original action identity/time and current eligibility, authorizes resubmission.
Duplicate/redelivered events, scans, bot lifecycle writes, labels, comments, run
IDs and timestamps alone cannot resubmit. A human submission received before
cleanup stays pending until safe release. Webhook intake commits the original
transition to `linear_intents` atomically with the inbox receipt, before HTTP
acknowledgement. This watermark survives inbox processing and restart; older
deliveries cannot replace a newer human action. Distinct actions with equal source
times hold authorization until an unambiguous newer action arrives. An acknowledged
ineligible submission remains withdrawn across redelivery and re-enrollment.
Upgrades recover retained signed receipts without replenishing budgets.

Submission processing and pending/queued revalidation use complete history for
that issue to distinguish the controller app's Needs attention projection from a
later human workflow action, matching the signed transition within a bounded 300-second
window to accommodate Linear webhook-to-history timestamp skew. A delayed signed Todo event can remain eligible
through our own projection, including an in-flight write with a lost response.
History alone never authorizes a cycle. Missing, inconsistent or ambiguous causal
evidence defers event acknowledgement or admission while retaining durable intent.
Current cancellation, archive, project mapping/enrollment and dependencies still
govern execution.
A later withdrawal clears pending intent; returning via a scan or bot write
cannot revive it. An accepted submission withdrawn while queued also needs a
new human transition. Human resubmission does not reset the independent
[single coding-repair budget](remote-development.md#single-attempt-auto-repair) or
transfer unresolved PR ownership.

Unfinished prerequisites and quota waits are separate from assessment failures.
A review account-capacity wait, or a prerequisite wait before assessment starts,
does not spend the assessment budget; capacity retries retain the 15-minute wait.
Repair startup/review/capacity retries retain repair mode and parent evidence.

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
and approval bindings stay intact. Material changes to reviewed evidence, new
unreviewed evidence and other authorization losses still apply. Prerequisites gate
queued tasks and every next attempt, including retries and repair.
Events/periodic observations discover satisfied/removed prerequisites. Rejoining
the runnable queue preserves any pending readiness retry delay and budget.

Human moves out of the ready states withdraw execution authorization, including
a move to Needs attention during coding. A signed transition fences worker
operations at intake; observations also detect a pause without waiting for its
webhook. The controller recognizes its own In Progress projection from durable
effects instead of treating every active issue's Needs attention state as eligible.
Pausing never proves runner stop, releases credentials or resolves an open PR.

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

During an authorized readiness retry, the stable comment states the verified
failure, delay and remaining attempts. Terminal feedback names the exact missing
decision/evidence or execution failure and next action, preserves current/prior
run links, and retains the existing user notification for attention-worthy review
failures. A reporting outage never resets the budget or recreates retry intent.
Every workflow-state transition has its own durable effect identity, independent
of comment/label/report revisions. Controller owns the desired state and epoch;
the driver records intent before writing, then confirmation or uncertainty.
Before a write, the controller rechecks its lifecycle epoch, current enrollment
facts and human watermark. Cleanup cannot replay confirmed states. Ambiguous
writes reconcile against the observed state before repeating a mutation.
Targeted history remains a recovery fallback for delayed/missing transitions and
unacknowledged attention writes. Signed events and retained projection provenance
distinguish controller traffic from a later human withdrawal. Transitions to
either the ready states or the active in-progress state preserve execution authorization
rather than triggering withdrawal. A controller write already in flight may land
after a human action; reconciliation restores the latest signed human state rather
than treating that echo as new intent. SQLite does not make remote Linear writes atomic. History reads remain targeted; settled
historical tasks gain no per-task polling. Upgrading adopts existing acknowledged
attention effects and live reported execution without resetting grants or budgets.
Late failure/context feedback keeps the original held-cycle boundary; it cannot
make an already submitted human transition look older than the failure.

Webhook commit wakes observation; background ticks run every 30 seconds, full
project scans every two minutes. These are intervals, not feedback latency promises.
Settled terminal tasks need no per-task polling; project lists recover missed events.
