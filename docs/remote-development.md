# Remote development

Eligible Linear tasks use the [SQLite controller and native two-job graph](remote-development-controller.md).
This topic owns worker behavior, provider policy, merges, lifecycle and repairs.
[Queues](remote-development-queue.md), [deployment](remote-development-deployments.md),
[setup](remote-development-setup.md), [accounts](remote-development-accounts.md),
[steering](remote-development-steering.md) and [images](remote-development-images.md)
own their detailed contracts.

## Worker execution

`automation/worker.py` runs review, coding, validation, guarded publication and
cleanup. `agent_session.py`, `codex_runtime.py` and `cursor_runtime.py` implement
provider conversations. `automation/controller/projects.json` supplies states,
branches, validation commands, merge policy, profiles and quota thresholds.

Readiness reviews the issue, complete comments, repository context/rules and user
decisions with isolated auth, read-only access, no publication token and a
structured verdict. It reads relevant docs and identifies consequential missing
decisions; short precise tasks can pass. It never executes repository validation.
Model review is limited to 600 seconds within a fixed 720-second phase deadline;
heartbeats cannot extend it. Approval binds issue, context, policy and images,
rechecked before coding/publication. Rejection/clarification reports the decision,
consequence and question; address it and return to Todo for another review.

## Agent profiles

The registry pins profiles; live model catalogs must confirm them:

| Agent | Model ID | Reasoning effort |
| --- | --- | --- |
| Codex | `gpt-6.1-sol` | `max` |
| Cursor | `grok-4.7-high` | `high`, encoded in the exact variant |

New selections prefer every eligible Codex account before Cursor, independently
of list order or Cursor headroom. Existing sessions keep their provider. There is
no complexity classifier/model substitution; Antigravity is deferred. Missing
Cursor variant or unacknowledged selection explicitly waits.

`quota_selection.py` chooses lowest usage within the first eligible provider pool.
For Codex with known weekly usage, ignore an identified short window (currently
five hours) for ranking only when **strictly below 70% used**; at 70% include it in
the most-depleted-window score. Missing weekly metadata stays conservative;
unknown never means zero. Eligibility checks every window, requiring at least
20% remaining, snapshots no older than 60 seconds and one active task/account.
Busy, blocked, stale, unknown or exhausted accounts are excluded, including during
replacement. No capacity records provider-specific `waiting_for_quota` reasons
and retries after 15 minutes.

Codex probes `account/read`, `model/list`, `account/rateLimits/read` under leases in
private homes, then reserves/rechecks before its development app-server thread;
review uses `codex exec`. Cursor pins CLI `2026.10.22-27dcf5f` and [ACP](https://cursor.com/docs/cli/acp),
with `--trust` and exact `grok-4.7-high`. Live `GetMe` must match saved identity;
`GetCurrentPeriodUsage` must report known included/API usage. Auto-only and paid/
on-demand capacity do not prove Grok headroom; missing/denied data stays unknown,
without invented weekly allowances. Ranking includes applicable included/API limits.
Cursor review uses ask mode in an enforced read-only Linux filesystem, masks
executable repo config and denies shell/web/MCP approvals. ACP follow-ups retain
session/workspace; ambiguous handoff is uncertain and never resent. Development
shares the same deadlines, steering, validation and publication gates.

## Automatic merges

Mappings default `auto_merge` to `true`; `false` stops at an open PR. The broker
sends policy; new/recovered PRs request native squash auto-merge bound to observed
head after repository/base/branch/bot/dispatch-marker verification. Missing repo
auto-merge or PR-specific required checks refuses the request. Existing App grants
suffice; no Administration grant or `--admin`/force-merge bypass is used.
GitHub handles pending/failed CI and conflicts. Worker success can precede merge;
recovery recognizes already merged PRs to avoid repeating work. See [GitHub auto-merge](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request).

## Linear lifecycle

`linear_states` maps In Progress, Needs attention and Done UUIDs; ready states such
as Todo admit tasks. Reporting projects committed views; failed writes retain
intent without resetting dispatch or rerunning agents.

| Observation | Linear projection |
| --- | --- |
| Admission/account wait | Todo, with queue/hold reason |
| Coding, validation, publication, PR/CI | In Progress and stage |
| Rejected review or exhausted repair | Needs attention |
| Verified merge, or validated no-change success | Done |

Human cancellation/completion is respected; [release rules](remote-development-queue.md#ownership-and-release)
still require stop/publication proof. [Queue feedback](remote-development-queue.md#linear-feedback)
owns labels and the stable status comment.

`controller/reporting.py` uses `common.reporting.attempt_comment` for separate
start/finish usage comments with stable attempt IDs. Initial account/profile/quota
stay immutable through failover; final readings/changes are per account with
unknown/reset/missing readings explicit. Current `N% used` phrases are bold:
initial values at start and final values, including new windows, at finish.
Before readings at finish stay plain even without final data. Duration runs from
recorded agent-job start to finish callback, excluding PR/CI wait; missing callbacks
do not invent timestamps. Previous actors' comments/replies survive; deterministic
replacement handles ownership conflicts, and ambiguous creates are verified before
retry. Reports exclude credentials, environment dumps, transcripts and raw failures.

## Single-attempt auto-repair

After verified stop, one repair may address coding failure, failed required checks,
merge conflict or outdated PR base. Pending CI, cancellation, uncertain dispatch,
readiness and dependency holds do not consume it. Repair uses the original issue/
grant and new attempt, current context and fresh review, without a child issue.

The private payload supplies original task, parent branch/PR, validated WIP and
bounded scrubbed failures. Restore viable work, diagnose failures, validate and
reuse the deterministic branch/marker. Rebased updates require explicit
force-with-lease and bot/branch/base checks. Startup/review/capacity retries retain
repair mode/evidence; after repair coding starts, no further coding repair is
allowed. Failed repair holds for operator review; verified merge/no-change releases.
The stable Auto-Repair Report mentions the configured user and links runs, PR and
prepared commit when available.

## Retries and validation

Completion is once per registry generation; leaving/re-entering Todo cannot reset
it. Generation changes permit eligible reruns, not repair. Changed context needs
fresh review. [Queue policy](remote-development-queue.md) owns retry timing and
uncertain dispatch discovery.

Default agent budget is 300 minutes (configurable 1–330) shared by coding, failover,
validation and corrections. The job allows 360 including setup/cleanup; changing
settings cannot extend an attempt. Each turn preserves branch/history/workflow
permissions, passes configured validation and publication scans. Bounded corrections
retain conversation/workspace. Runtime files remain outside the writable target,
including post-job cleanup. [Accounts](remote-development-accounts.md) owns isolated
auth, parent-held publication tokens, fenced cleanup and WIP credential checks.
