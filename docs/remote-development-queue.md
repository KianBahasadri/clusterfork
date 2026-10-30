# Repository queues

The controller is the only admission and completion authority. Its database and
command boundary are described in [controller implementation](remote-development-controller.md).
This topic owns queue policy, prerequisites, user feedback and release rules.
The former table coordinator is a [dated record](archive/remote-development-functions-queue.md).

## Ordering and admission

Mapped projects targeting the same case-insensitive GitHub repository share one
queue and one task grant. Different repositories can advance independently.
Runnable issues order by Linear priority **Urgent → High → Normal → Low → No priority**
(values **1 → 2 → 3 → 4 → 0**), then lower `sortOrder`, then committed FIFO sequence.
Project scans commit candidates in creation-time/UUID order before admission.
Missing sort values default to zero. Priority/order observations update waiting
positions without interrupting the active task. An issue that leaves eligibility
and later rejoins receives a new FIFO position for otherwise equal ordering.

Eligibility uses current Linear facts: explicit project UUID mapping, enrollment
project label, configured ready status, archive/cancellation state and dependencies.
An issue label does not enroll a project. Unseen ineligible issues create no task
or status comment. Known waiting issues that become ineligible are skipped and
lose their queued marker. Completed identities remain completed for that generation.

A transaction checks the runnable queue head, reserves the unique repository
grant, creates the attempt and records dispatch intent. GitHub dispatch occurs
outside the transaction. Claimed/uncertain dispatches are discovered by stable
identity; absence of a run listing does not authorize another dispatch.

Waiting issues use no runner or account. Admission binds a task to the latest
observed target branch, including the previous task's merge. Runtime selection is
independent; a required runtime change waits for [automatic deployment](remote-development-deployments.md).
Operator/deployment holds stop new attempts without revoking active work.

## Ownership and release

The repository grant has no expiry. It spans review, capacity waits, coding,
validation, publication, PR checks and merge. An urgent arrival or manual reorder
only changes the next admission. A finish callback or auto-merge request cannot
release the grant. Expired account/worker heartbeats do not prove GitHub stopped.

Completion requires authoritative stop evidence for the recorded and all matching
GitHub attempts plus publication resolution: a verified merge, or successful
validated work with no changes and no PR. Open PRs retain ownership, including
manual-merge projects. A merged PR remains authoritative if cleanup later fails.
Duplicate, stale and manual rerun identities cannot inherit another attempt's
account or publication grant.

To abandon active work, cancel the Linear issue, stop/cancel GitHub work, and close
or merge remaining publication. The controller verifies those facts before
release. Done, archive, enrollment removal or project moves alone do not abandon
unresolved active work. Missing or incomplete observations retain ownership.

Confirmed startup failure before coding retries after 60 seconds. Temporary review
failures and account capacity waits retry after 15 minutes. Retries require the
previous attempts to have stopped and publication to be checked, and retain the
repository grant and current eligibility checks. Context changes require fresh
review. A startup/capacity retry before repair coding retains the one repair budget
and original branch/evidence. Coding failures use the [single repair policy](remote-development.md#single-attempt-auto-repair).

## Issue dependencies

Only incoming `blocks` relations count as prerequisites. Their source issues must
have workflow **type `completed`**, including accessible sources outside mapped
projects. A label, status name, cancellation, deletion, unavailable source or
incomplete pagination never establishes completion. Removing a verified blocking
relation removes that prerequisite.

Dependency-held waiting issues keep priority, FIFO position and queued feedback
without taking a repository grant. The highest-priority runnable issue may pass
blocked candidates. Cycles remain waiting while unrelated tasks progress.

Dependencies are refreshed at admission and before guarded worker operations. A
new pre-coding blocker holds authorization; after prior attempts stop and no PR
exists, the task returns to its queue position and releases the grant. Dependencies
added after coding begins do not interrupt the current implementation. Events and
periodic observations discover satisfied or removed prerequisites automatically.

## Linear feedback

`clusterfork: queued` means received and waiting; `clusterfork: selected` means the
controller reserved this repository's grant. Neither asserts a passing review or
a running agent. Human assignments and unrelated labels are preserved. A review
rejection clears selection while the rejected runner is being verified stopped.

The task's stable status comment explains prerequisites, admission holds, current
run/PR links or which active issue must finish. Grant changes create fresh reporting
intent for waiting issues so they do not keep naming a completed predecessor.
Reporting retries preserve task identity, FIFO position and dispatch intent.
They cannot start another worker.

Webhook intake wakes the controller after committing the event. Background
observations run every 30 seconds; full project scans run every two minutes.
External API latency and retries affect feedback, so these are observation
intervals rather than an end-to-end timing promise. Once terminal reports settle,
completed tasks need no per-task GitHub/Linear polling. Project scans continue to
read current lists to recover missed notifications.
