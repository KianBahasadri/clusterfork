# Remote development

Eligible Linear issues use the single controller and a native GitHub Actions job
graph. The controller owns task decisions and durable state; GitHub runs each
review/coding phase on a fresh hosted VM. The worker validates its changes and
publishes a guarded PR. GitHub performs required CI and optional automatic merge;
the controller verifies final completion before admitting another repository task.

[Controller implementation](remote-development-controller.md) owns the task
interface, database, transports, signed job roles, migration and live evidence.
[Repository queues](remote-development-queue.md) owns priority, dependencies and
slot release. [Deployment](remote-development-deployments.md) owns runtime rollout.
[Setup](remote-development-setup.md), [accounts](remote-development-accounts.md),
[steering](remote-development-steering.md) and [images](remote-development-images.md)
own their respective contracts. The retired coordinator is preserved in the
[Functions runtime record](archive/remote-development-functions-runtime.md).

## Worker execution

`automation/worker.py` implements review, coding, configured validation, guarded
publication and credential cleanup. `agent_session.py`, `codex_runtime.py` and
`cursor_runtime.py` retain the existing provider adapters and conversation
protocols. `automation/controller/projects.json` supplies project states, repository
branches, validation commands, merge policy, profiles and quota policy.

Readiness assesses the current issue description, complete comment thread,
repository context, repository rules and explicit user decisions. The reviewer
reads relevant documentation and identifies missing consequential decisions,
rather than treating issue length as a quality signal. It uses read-only access,
isolated authentication, no publication token, and a structured verdict. It does
not execute repository validation. A precise small task can pass without extra
operator steps. A model review has a 600-second limit; its phase has a fixed
720-second deadline that heartbeats cannot extend.

A valid review binds issue content, target context, policy and images. The broker
rechecks that binding before coding/publication. Rejection or clarification holds
execution and reports the missing decision, consequence and question. Address the
feedback and return the issue to Todo for another review. Failed infrastructure
and quota waits retain bounded retries; uncertain dispatches require discovery.
A worker's success callback alone never marks the Linear task complete.

## Agent profiles

`automation/controller/projects.json` pins these profiles:

| Agent | Model ID | Reasoning effort |
| --- | --- | --- |
| Codex | `gpt-6.1-sol` | `max` |
| Cursor | `grok-4.7-high` | `high` (encoded in the model variant) |

Each new selection considers all eligible Codex accounts before Cursor, regardless of list order or
Cursor quota. Busy, exhausted, stale, unknown or otherwise ineligible Codex accounts allow Cursor
fallback. Existing Cursor sessions retain their assignment when Codex capacity returns; a new attempt
prefers Codex again. There is no complexity classifier or model substitution. Antigravity is deferred.
Both adapters verify the pinned profile against the account's live model catalog. Cursor requires the
exact variant and its acknowledged selection; an unavailable Grok 4.7 / High profile is an explicit wait
reason.

`quota_selection.py` implements the decision over provider-normalized snapshots: choose the eligible
account with the lowest usage score **within the first eligible provider pool**. For Codex, an identified
short window (currently five hours) **strictly below 70% used** is ignored for ranking when weekly usage
is known; score by weekly usage and any other applicable limits. At exactly 70% or above, include it in
the most-depleted-window score. Missing weekly metadata keeps conservative scoring; unknown usage never
counts as zero. Eligibility still checks **every** window: skip busy, blocked, unknown, stale, and nearly
exhausted accounts. Configuration requires at least 20% remaining, a snapshot at most 60 seconds old, and
one active task per account. These guards also apply to replacement accounts. If none qualifies, return
no selection and wait.

The runner calls Codex app-server `account/read`, `model/list`, and `account/rateLimits/read` in isolated
temporary Codex homes. It probes available accounts under leases, applies that ranking policy, then
reserves and rechecks the chosen account before starting the development app-server thread (readiness
still uses `codex exec`). Cursor uses the pinned `2026.10.22-27dcf5f` CLI's [ACP
interface](https://cursor.com/docs/cli/acp) for account model discovery and execution, with `--trust` and
the exact `grok-4.7-high` variant. Live `GetMe` must match its saved identity; `GetCurrentPeriodUsage`
must report known included API usage. Auto-only quota and paid/on-demand capacity do not establish Grok
headroom; missing measurements never become zero or an invented weekly allowance. Cursor keeps
conservative ranking across its applicable included/API limits. Its readiness review uses ask mode inside
an enforced read-only Linux filesystem, masks executable repository configuration, and denies shell, web
and MCP approvals; development keeps the same admission, deadlines, steering, validation and publication
gates. ACP follow-ups retain the session and workspace; an ambiguous prompt handoff is reported as
uncertain and never resent. Unknown or denied usage never counts as unused quota. A lack of capacity
records `waiting_for_quota` and allows reconciliation to retry after 15 minutes. The registry owns these
pins and selection thresholds. Durable quota waits include bounded provider-specific reasons. Historical staged-account validation is recorded in [remote accounts](remote-development-accounts.md#kian-922-post-deployment-validation); it is not part of task admission.

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
for CI. Recovery recognizes merged
PRs so a retry does not repeat work if merging preceded outcome persistence.
Reference: [GitHub auto-merge](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request).

## Linear lifecycle

Project `linear_states` maps In Progress, Needs attention and Done UUIDs; configured
ready states such as Todo admit new tasks. Reporting is a projection of committed
task views, not another lifecycle writer. Failed Linear writes keep reporting
intent pending without resetting dispatch identity or retrying agent work.

| Observation | Linear projection |
| --- | --- |
| Waiting for admission or account capacity | Todo, with a queue/hold reason |
| Coding, validation, publication or pending PR/CI | In Progress and the current stage |
| Rejected review or exhausted automatic repair | Needs attention |
| Verified merge, or successful validated work with no changes | Done |

Human cancellation/completion is respected; it cannot itself prove GitHub work
stopped or release an unresolved PR. Only owned queued, selected, clarification,
repair and stage labels are changed. Account capacity uses the waiting-for-quota
stage. One stable task comment carries the current reason and run/PR links.

`controller/reporting.py` reuses `common.reporting.attempt_comment` for distinct
start and finish usage comments with stable IDs per attempt. The initial account,
profile and quota snapshot remain immutable through failover. Final readings and
changes are reported separately for each account; unknown/reset windows and
missing final readings are explicit. Each current `N% used` phrase is bold in
Linear: the initial reading in start comments and each account's final reading
(including newly observed windows) in finish comments. Before readings in finish
comments stay plain, including when the final reading is unavailable.
Worker time covers its recorded agent-job start through the finish callback,
excluding later PR/CI waiting. No timestamp is invented when a missing callback
leaves duration unknown.

The shared Linear adapter preserves previous actors' comments and replies. It
creates one deterministic replacement when ownership prevents updating a comment,
and verifies an ambiguous create before retrying the same logical ID. It excludes
raw credentials, environment dumps, transcripts and failure output from reports.

## Single-attempt auto-repair

The controller permits one repair after verified worker stop and a coding failure,
failed required PR checks, merge conflict or outdated worker PR base. Pending CI,
intentional cancellation, uncertain dispatches and readiness/dependency holds do
not consume it. A repair remains on the original Linear issue and repository
grant with a new attempt identity; no generated child issue is needed.

The broker supplies the original task, parent branch/PR, validated WIP reference
and bounded scrubbed failure evidence through its private repair payload. A new
attempt receives current context and its required review. The worker restores
viable parent work, diagnoses code/check failures and runs configured validation.
It reuses the parent's deterministic branch and publication marker. Rebased PR
updates require the existing explicit force-with-lease and bot/branch/base checks;
no administrator merge bypass is permitted.

Pre-coding startup, review or capacity retries retain repair mode and the original
parent evidence. After repair coding begins, another coding repair is forbidden.
A failed repair holds the slot for operator review; a verified merge or no-change
completion releases it. A stable Auto-Repair Report mentions the configured user
and links the original/repair runs, PR and prepared commit where available.

## Retries and validation

Completed tasks remain once per registry generation. Moving an issue out of Todo
and back does not reset completion. A generation change permits eligible issues
to run again and is not a repair mechanism. Context changes require fresh review;
unknown dispatches are never blindly resent. Queue/retry policy lives in the
[repository topic](remote-development-queue.md).

The default agent budget is 300 minutes, configurable from 1 through 330; coding,
account failover, validation and corrections share it. The coding job permits 360
minutes including setup/cleanup. These settings do not extend an existing attempt.

Each coding turn must preserve branch/history integrity and workflow permissions,
pass every configured validation command, and satisfy publication scanning before
publication. Bounded correction turns use the same conversation/workspace. Runtime
files are separate from the writable target, including actual post-job cleanup.
Agent subprocesses receive isolated authentication and a cleaned environment;
publication tokens stay in the credentialed worker parent. Account saves/releases
remain fenced even after issue edits or cancellation.
