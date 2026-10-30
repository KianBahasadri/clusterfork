# Retired Functions lifecycle, 2026-10-30

This is a dated record of the replaced Functions coordinator. Current behavior is
owned by [remote development](../remote-development.md) and
[the controller](../remote-development-controller.md).

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

Start and finish attempt immediate updates. The shared 2-minute timer
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

[KIAN-923's reporting investigation](../remote-development-deployments.md#status-reporting-investigation)
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
| `clusterfork: coding` | Selected agent account begins implementation |
| `clusterfork: final testing and review` | Worker checks the resulting changes and runs configured validation |
| `clusterfork: publication` | Worker prepares and publishes the result |
| `clusterfork: PR review and CI` | Open PR awaits checks, review, or merging |
| `clusterfork: waiting for quota` | No account qualifies; issue awaits an automatic retry |

Stage changes replace only these owned labels; other issue labels are preserved.
Completion and blockers clear the stage label. PR/CI reconciliation can restore
the review label when a blocker clears. This does not create a separate review
agent; validation and any correction turns use the same conversation and workspace.

After selection and the final quota recheck, a separate start comment records
the account email (when available), stable account identity, plan, agent, model,
reasoning effort, and applicable quota windows before coding. Each reading shows
used/remaining percentages and reset times, with weekly windows identified by
their provider-reported duration. Each account stays leased until switching or
exiting; the final one covers validation and publication. Before releasing an
account, including on failure, the worker attempts a fresh usage reading.

A separate finish comment identifies account changes and compares before/after
usage in percentage points **separately for each account**, retaining the initial
start snapshot. It also reports total time from worker start to its
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
admitted coding/validation failure, completed failing required PR checks, or an open
worker PR with merge conflicts (`CONFLICTING`/`DIRTY`) or an outdated base (`BEHIND`) can
consume **one** repair budget per dispatch key. Pending CI alone, intentional cancellation,
unknown dispatches, readiness/dependency holds, and merge-policy blockers do not
consume it. The parent queue row atomically records `repair_attempted`, `repair_key`,
`repair_issue_id` equal to the original issue UUID, and immutable context under the
slot ETag; the receipt mirrors that intent and prior lifecycle writers drain.
The deterministic second dispatch key hashes the generation, project, and
`comment_id(parent_key, "auto-repair-issue")`. Interrupted admission reuses that intent.

Azure creates no Linear sub-issue. It atomically hands the occupied slot to a
second queue entry and receipt using the original Linear UUID and identifier,
with `dispatch_key = repair_key`. Only the receipt owning the slot accepts steering;
`clusterfork: selected` stays on the original issue until slot release. The issue
stays In Progress, or Todo during a quota wait, with `clusterfork: auto-repair`
alongside its current stage label. Admission requires the parent's durable repair
binding, the original issue's current eligibility, and its current passing
`ClusterforkReadiness` review. Human title/description edits require a fresh review;
generated Markdown hashes no longer authorize repairs. Enrollment, dependency,
worker identity, account leases, and publication gates remain enforced.
The worker checks out the **parent's** `clusterfork/<identifier-lowercase>-<key[:8]>`
branch. It restores the saved WIP commit when its branch still exists and either no
open PR exists or the WIP descends from the current PR branch; divergent WIP cannot
replace newer PR work. The saved WIP SHA must match the fetched branch.
The worker runs configured validation and pushes to the existing PR or
creates its missing PR with the parent's dispatch marker. Required checks and native
merge requirements and the normal publication permission checks still apply;
`--admin` bypasses remain refused.

The authenticated `broker/start` response's `repair.instructions` supplies the
rendered original task, trigger, failure summary, branch/PR context, and repair
instructions directly to the worker. The versioned start protocol rejects older
brokers/workers; no generated child description is needed. Only this private repair
payload receives the parent's stored scrubbed
`failure_output` (up to 16,000 UTF-8 bytes), `wip_branch`, and `wip_commit_sha` from
`ClusterforkWorkers`. Public Linear text retains the short failure summary and check
names/links. In the credentialed parent process, the repair worker reads required PR
checks with `gh pr checks --required` and fetches failed Actions job logs using the
repair checkout token's Actions read access. `PublicationGuard` and shared review
redaction scrub the evidence before a combined 16,000-byte CI tail enters the agent's
token-free prompt. Unavailable logs retain a bounded fallback; unreadable auth caches
withhold evidence. Neither log channel is copied to Linear or Actions output.

The repair agent diagnoses Git status, branch history/diff against `origin/<base>`,
and supplied failure evidence first. It patches viable work in the working tree,
including resolving base conflicts, or resets to `origin/<base>` and re-implements
an unsalvageable/missing implementation. Both paths must satisfy the original issue
and all validation commands; the worker creates the publication commits. Repairs
may use the latest fetched base despite the parent's initial readiness SHA. Git
integrity checks retain the worker branch name, allow the original or current base
HEAD, and permit rebased history only with bot authors and committers on introduced
commits. A reset/rebased update to an existing open PR uses an explicit
`--force-with-lease` bound to its original head, after re-verifying the repository,
base, worker branch, `clusterfork[bot]` owner, and parent dispatch marker.

The label **or** persisted `is_auto_repair` metadata forbids another repair; a repair
that has started coding cannot be rerun automatically or through broker admission.
If it fails or its completed required checks still fail, the original issue becomes Blocked
(the configured Needs attention state) and the slot stays held for human review.
After merge, or validated completion with no changes/PR remaining, it becomes Done;
the same reconciliation pass releases the slot and dispatches the next runnable issue.
Both terminal outcomes remove the current stage and `clusterfork: auto-repair` labels.
Legacy records with a separate `repair_issue_id` still settle both issues and their reports.

Repair start/finish usage comments use the repair dispatch key on the original issue.
`notify_user_url` in `automation/azure/projects.json` configures the profile mention. Each terminal
outcome writes one stable `comment_id(parent_key, "auto-repair-report")` comment on that issue,
starting with Kian's profile URL so the Clusterfork OAuth app can notify `@kian`.
The Auto-Repair Report links the trigger, failed initial run, repair run, PR, and
prepared commit. It summarizes bounded diagnostics, file count, validation, and
whether the queue is flowing or awaiting review.
Reports exclude credentials, raw logs, environment dumps and agent transcripts. Pending projections
and in-flight markers recover failed, ambiguous or interrupted writes even after the next task starts.

## Retries and troubleshooting

Completed issues dispatch once per registry `generation`. Editing an issue or
moving it out of Todo and back does not reset completion. Capacity waits retry
through reconciliation; eligible execution/check failures use the single repair above.
Other blockers require human recovery. Existing PRs use their deterministic branch name.
The [recovery tools](../remote-development-recovery.md) cover historical child-issue content bindings.
Changing generation permits **all** eligible issues to dispatch again.

Each project can set `agent_timeout_minutes` in `automation/azure/projects.json`.
The default is **300 minutes (five hours)**; allowed values are integers from 1
through 330. The hosted job allows **360 minutes (six hours)**, leaving time for
setup and publication. Coding, account failover, validation, and correction turns
share the agent deadline. GitHub-hosted jobs have a
[six-hour execution ceiling](https://docs.github.com/en/actions/reference/limits).
Settings are read when a worker starts; changing them does not extend an already
running attempt. Automatic checkpoint/continuation across jobs is not implemented.

After each coding turn, the live agent session checks Git history/branch integrity,
workflow permissions, all configured validation commands, and staged publication
credentials. A failed check returns its identity, exit code when available, and
[scrubbed failure evidence](../remote-development-observability.md#failure-explanations)
to the same conversation. At most **two correction turns** may fix the existing
workspace under the original deadline and active account/worker heartbeats. Every
gate runs again after each correction; account failover retains this shared budget.
Publication rechecks
current project enrollment and status, pushes a separate branch, opens a PR, and requests the
configured merge policy. GitHub waits for required checks before automatic merging.
Both readiness and coding prompts receive the broker's current workflow-publication
capability. Readiness identifies required edits without that capability as a
permission blocker; the worker also refuses unauthorized workflow changes, including
changes recovered from an earlier attempt's branch. Authorized edits follow the same
validation and PR process. [Setup](../remote-development-setup.md#github-app-and-repository)
owns the installation grant; [accounts](../remote-development-accounts.md#worker-credential-boundary)
owns publication token scope.
Coding quota failures resume the persistent conversation on another account;
[accounts](../remote-development-accounts.md#coding-account-failover) owns that boundary.
Linear credentials stay in Azure, outside the agent.

If execution fails after coding starts, the worker attempts to preserve uncommitted
or unpushed work before runner exit. It verifies the expected worker branch and
HEAD, enforces workflow permissions, and scans both staged and committed changes
with the publication guard. Clean changes are committed as `clusterfork[bot]` and
pushed to the task branch when no open PR exists. An open PR instead receives a
separate `clusterfork-wip/<identifier-lowercase>-<target_key[:8]>` branch, preserving
its existing head. Failed finish callbacks retain `wip_branch` and `wip_commit_sha`
only after a successful push. A denied scan, lost publication permission, or push
failure leaves the original execution failure intact. Preserved WIP does not establish
successful validation or publication and never opens a PR or requests a merge.

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
