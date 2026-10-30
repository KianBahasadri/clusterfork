# Retired Functions runtime, 2026-10-30

This is a dated record of the replaced Functions coordinator. Current behavior is
owned by [remote development](../remote-development.md) and
[the controller](../remote-development-controller.md).

# Remote development

Eligible Linear issues route through one shared Azure Function App to an agent
worker on a standard GitHub-hosted runner. The repository queue admits one task
for a read-only readiness review. After a pass, the development worker leases an account using
live quota, implements the issue, runs configured validation, and publishes a
PR. GitHub can squash-merge it after required checks; Linear tracks progress and
blockers. Deployment is separate. This is Clusterfork's execution adapter, not
an installation of upstream Symphony.

[Setup](../remote-development-setup.md) covers provisioning and enrollment;
[remote accounts](../remote-development-accounts.md) covers credential ownership.
[Diagnostics](../remote-development-observability.md) covers correlated operational
logs, durable failure explanations, and Azure monitoring.
[Dated validation records](../archive/remote-development-validation.md) retain the
live routing and merge evidence without treating it as current health.

## Components and dispatch

| Source | Responsibility |
| --- | --- |
| `automation/azure/function_app.py` | Signed webhook, queue trigger, health, worker broker, and shared 2-minute timer |
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
the separate [agent steering](../remote-development-steering.md) path. The issue/project consumer
fetches current Linear data, then checks the mapped project, its configured
enrollment project label, archive flags, and configured ready-state IDs. An issue
label does not enroll a project. Removing enrollment prevents new dispatches but
does not cancel submitted jobs. See
[setup](../remote-development-setup.md#linear-webhook-and-first-run) for the enrollment label.

The shared timer reconciles worker/PR status and scans mapped projects every
2 minutes (`0 */2 * * * *`). Quota and readiness retry backoffs remain 15 minutes.
Project labels are fetched once per issue page to stay within Linear's
query-complexity limit.

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

Reviews use development's pinned profiles, provider precedence, live quota selection,
and fenced account pool. Both adapters enforce read-only access, isolated auth,
no inherited job credentials, and the same JSON result schema (see agent profiles).
They do not execute configured validation or receive
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
start, account access, coding, and publication recheck approval; initial development uses
the reviewed base commit. [Fenced auto-repairs](#single-attempt-auto-repair) can use an advanced base.
Material edits during account selection prevent coding
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
budget, repository selection, steering, auto-repair, and worker recovery contracts) and an explicit
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

[Repository queues](../remote-development-queue.md) covers priority and FIFO admission, issue dependencies,
queued/selected markers, worker fencing, slot release, and selection feedback timing.

## Automatic deployments

[Deployment pauses and recovery](../remote-development-deployments.md) owns verified
deployment, worker draining, startup deferral, and prompt reconciliation after resume.

## Agent profiles

`automation/azure/projects.json` pins these profiles:

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
pins and selection thresholds. Durable quota waits include bounded provider-specific reasons. The
isolated, once-only post-deployment probe and staged-account activation are owned by [remote
accounts](../remote-development-accounts.md#kian-922-post-deployment-validation).
