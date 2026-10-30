# Retired Functions queue, 2026-10-30

This is a dated record of the replaced Functions coordinator. Current behavior is
owned by [remote development](../remote-development.md) and
[the controller](../remote-development-controller.md).

# Repository queues

The [runtime overview](../remote-development.md) covers worker execution and Linear lifecycle.
This topic owns repository admission, dependency waits, queue reporting, and slot release.

Eligible Todo issues enter one durable priority queue per GitHub repository before readiness review. Projects targeting
the same case-insensitive repository share its queue; different repositories advance independently. Runnable waiting issues are
admitted in Linear priority order: **Urgent → High → Normal → Low → No priority** (values **1 → 2 → 3 → 4 → 0**).
Within each priority, lower Linear `sortOrder` values come first, reflecting manual issue ordering. Equal sort values
fall back to FIFO sequence from first eligible observations committed to storage. A project scan inserts its complete
batch before dispatch, assigning sequence numbers by creation time and UUID. Later events/scans, priority or order edits,
retries, and restarts retain those sequences; missed historical Todo transitions cannot be reconstructed.

Issue webhooks, project scans, and timer reconciliation persist updated priorities and floating-point `sort_order` values
for waiting entries without changing their sequence or queued-label intent. Before reserving a free slot, Azure refreshes
waiting eligibility, priority, and order, including during a handoff and for legacy rows missing these fields. Priority
and order updates fence concurrent admission choices. Missing/null Linear sort values default to `0.0`; equal values
retain FIFO behavior. Temporary Linear read failures retain the last known priority, order, and durable position for
retry. Once selected, an issue keeps its slot through review, dispatch retries, quota waits, coding, and unresolved PRs
until normal release conditions are met, except for a new pre-coding dependency hold described below; an urgent arrival,
priority edit, or reorder only affects the next admission. Imported pre-queue dispatches still precede new Todo work
because they have already crossed the dispatch boundary.

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
without touching stage labels. The [runtime reconciliation rules](../remote-development.md#components-and-dispatch)
define recovery after a reporting process stops. Dispatch receipts keep repositories discoverable for pending cleanup
after a mapping is removed.

The repository slot has **no expiry**. It spans readiness review, dispatch, coding, validation, publication, pending CI,
review, and merging. A finish callback or auto-merge request does not release it. The
[shared timer](../remote-development.md#components-and-dispatch) advances
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

Failures, missing outcomes, closed unmerged PRs, and merge/CI blockers pause the repository. The
[runtime lifecycle](../remote-development.md#single-attempt-auto-repair) owns automatic repair and human recovery rules.
To abandon active work, cancel the Linear issue, stop/cancel its GitHub run, and
close or merge any remaining PR. Reconciliation verifies those GitHub facts before releasing the slot. Changing Linear
status to Done, archiving, or removing enrollment alone does not abandon active work. An expired worker/account lease
never proves that the worker stopped.

The head retains its position during quota waits and retries after its existing 15-minute delay, once its prior GitHub
run has stopped. Before every dispatch, the dispatcher fetches current enrollment, project, archive, cancellation, and
ready-state data. Events and reconciliation also recheck waiting entries while the head is active, including empty
project scans and issues moved out of mapped projects. Leaving Todo, cancellation, issue/project archive, project moves,
or loss of enrollment durably skips a waiting entry and removes its queued marker without waiting for the active task
to finish. An undispatched issue that later becomes eligible rejoins using its current priority and Linear order with a
new FIFO sequence for equal sort values. A temporary eligibility-read failure retains its durable position for retry.
Before coding a newly admitted task, the worker fetches the latest target branch and creates its branch from that
revision, including the preceding task's merged changes.

A confirmed worker failure before coding starts (`initializing` or `selecting_account`,
with no open PR and no durable `coding_started` flag) is a retryable dispatch failure.
After GitHub confirms all matching/recorded attempts stopped, the queue retains the
same slot and dispatch key, records `retry_after = now + 60`, and leaves
`repair_attempted` unspent. Lifecycle reporting returns known startup failures to
Todo. Repeated observations and restarts retain the original cooldown; a lagging run
listing cannot redispatch an already submitted retry. The same startup recovery can
retry a repair attempt before it starts coding, retaining the same issue and repair key.
Readiness/dependency/deployment holds retain their own retry rules. Cancellation,
unconfirmed dispatches, and missing outcomes without confirmed failure do not gain a
startup retry. An open PR continues through the
[single repair policy](../remote-development.md#single-attempt-auto-repair), including CI,
merge-conflict, and outdated-base recovery, rather than the startup path.

Dispatch receipts retain their original workflow and target branch so active work can still be checked after its project
mapping changes or is removed. Undispatched entries with removed/remapped destinations are skipped. Legacy receipts
without those fields require restoring their mapping for verification.

Restart recovery reuses queue entries, slots, and dispatch receipts. Pre-queue receipts are imported before new Todo
work is admitted. A reservation interrupted before receipt creation can resume; a receipt left `dispatching` or
`uncertain` is never blindly resent. Reconciliation discovers its run and deterministic PR branch. If no run can be
confirmed, that repository stays paused for investigation or explicit cancellation. Incomplete GitHub responses also
keep the slot held.

## Issue dependencies

“Blocked by” means an issue dependency; it is separate from the Needs attention workflow status (formerly Blocked).

Before admission, Azure fetches every page of the issue's incoming `inverseRelations`, including archived resources.
Only `blocks` edges count: their source issues are the prerequisites, including accessible issues outside mapped or enrolled
projects. Each source must have workflow **type `completed`**. Labels, issue text, status names (even “Done”), cancellation,
and deletion never grant completion. Missing sources/statuses, GraphQL errors, and incomplete or nonadvancing pagination
hold the issue for verification. A successfully read removal of the blocking relationship releases that prerequisite.

Dependency-held issues keep their original sequence and priority and the `clusterfork: queued` marker, without taking the
repository slot, readiness runner, or account. The queue selects the highest-priority runnable candidate, then uses Linear
order and FIFO sequence to break ties. Cycles therefore stay waiting while independent tasks progress. One stable queue
comment lists outstanding prerequisite identifiers and status types, or explains unavailable data; failed comment writes
retry without admitting work.

Dependencies are read again immediately before readiness dispatch, at reviewer/development startup and account access,
and at the final coding callback. A blocker added after selection holds that attempt; once GitHub confirms previously
dispatched runs stopped and no PR exists, the issue returns to its original queue position and releases the slot. A denied
pre-coding attempt stays retryable in Todo. Durable dependency epochs fence coding against concurrent holds and preserve
retry intent across restarts, without resetting completed coding receipts. Dependencies added after coding starts do not
interrupt that task. Blocker Issue notifications (including unmapped/deleted sources), dependent Issue notifications, and
available IssueRelation notifications refresh affected repository queues from current Linear data. The shared
timer also refreshes dependencies, recovering missed notifications and automatically admitting newly runnable issues.

## Selection feedback latency

`host.json` caps idle queue polling at **one second**, replacing Azure's [one-minute
default](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-storage-queue#hostjson-settings). It
allows eight events per host concurrently so an unrelated scan cannot monopolize the consumer; durable slot transactions
still enforce repository serialization. Faster polling increases idle storage transactions. With a warm consumer, no
backlog, and a free repository, expect feedback in seconds: polling plus current Linear eligibility, durable admission,
and the label API calls. Label lookup/creation can add calls. Each HTTP request has a 20-second timeout. Linear webhook
delivery, Azure cold starts/scaling, backlog, rate limits, and Linear client refresh can add delay; one second is a
polling cap, not an end-to-end SLA. A busy repository stays queued until authoritative completion checks allow
admission, normally on the shared timer. Failed label writes retry on events and that timer; lowering polling does
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
