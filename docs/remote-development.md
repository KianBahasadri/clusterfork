# Remote development

Eligible Linear issues route through one shared Azure Function App to a Codex
worker on a standard GitHub-hosted runner. The worker leases an account using
live quota, implements the issue, runs configured validation, and publishes a
PR. GitHub can squash-merge it after required checks; Linear tracks progress and
blockers. Deployment is separate. This is Clusterfork's execution adapter, not
an installation of upstream Symphony.

[Setup](remote-development-setup.md) covers provisioning and enrollment;
[remote accounts](remote-development-accounts.md) covers credential ownership.
[Dated validation records](archive/remote-development-validation.md) retain the
live routing and merge evidence without treating it as current health.

## Components and dispatch

| Source | Responsibility |
| --- | --- |
| `automation/azure/function_app.py` | Signed webhook, queue trigger, health, worker broker, and shared 15-minute timer |
| `dispatch_core.py`, `dispatch_clients.py` in that directory | Current Linear eligibility, GitHub App dispatch, and durable receipts |
| `worker_core.py`, `worker_store.py`, `quota_selection.py` | Workflow authorization, account leases, fenced credentials, and quota selection |
| `linear_sync.py` | Linear lifecycle and PR/CI reconciliation |
| `automation/worker.py`, `automation/codex_runtime.py` | Account inspection, Codex execution, validation, publication, and merge requests |
| `automation/azure/projects.json` | Project mappings, states, model/effort, validation commands, and merge policy |
| `.github/workflows/clusterfork-worker.yml` | Pinned Codex CLI on `ubuntu-24.04`, 45-minute job, read/OIDC permissions |
| `.github/workflows/clusterfork-pilot.yml` | Routing-only receipt workflow; no coding agent |

The webhook authenticates raw bytes with HMAC-SHA256 and requires a timestamp
within five minutes. It queues only entity IDs from Issue/Project create/update
events and returns 200 only after a needed queue send succeeds. The consumer
fetches current Linear data, then checks the mapped project, its `symphony`
project label, archive flags, and configured ready-state IDs. An issue label does
not enroll a project. Removing enrollment prevents new dispatches but does not
cancel submitted jobs.

The shared timer reconciles worker/PR status and scans mapped projects every
15 minutes. Project labels are fetched once per issue page to stay within
Linear's query-complexity limit.

## Agent profiles

`automation/azure/projects.json` enables only Codex:

| Agent | Model ID | Reasoning effort |
| --- | --- | --- |
| Codex | `gpt-6.1-sol` | `max` |

The registry requires an explicit Codex profile and supported effort value. Each
issue uses one account selected by available quota. There is no complexity
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
reserves and rechecks the chosen account before starting `codex exec`.
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
`blocked`, and `done`. Clusterfork maps these to In Progress, Blocked,
status, and Done. Only ready states such as Todo dispatch new issues.

| Event | Linear status |
| --- | --- |
| Worker starts, or required CI is pending | In Progress |
| Merge conflicts, failed/canceled required checks, closed unmerged PR, draft PR, or unmet merge requirements | Blocked |
| Worker fails, is canceled, or ends without a recorded outcome | Blocked |
| PR merges, or validated work needs no repository changes | Done |
| No account has enough quota | Todo; retries after 15 minutes |

Start and finish attempt immediate updates. The existing shared 15-minute timer
checks later PR/CI changes and retries failed Linear writes; no GitHub webhook or
continuously running worker is needed. It updates one owned comment with the
current reason, PR link, and worker-run link. Durable synchronization leases and
a stable UUIDv4 comment ID prevent duplicate comments after retries.

Pending CI is normal progress. A blocked PR returns to In Progress when its
blocker clears, then Done after merging. Unknown/incomplete GitHub responses leave
the previous state unchanged. Archived issues/projects, removed enrollment labels,
project moves, and manually completed/canceled issues are respected. Publication
accepts the worker's In Progress state while preserving other eligibility checks.
A manual GitHub job rerun can resume Blocked work; status updates alone never
reset the completed dispatch receipt or automatically rerun a failed agent.

## Retries and troubleshooting

Completed issues dispatch once per registry `generation`. Editing an issue or
moving it out of Todo and back does not reset completion. Capacity waits retry
through reconciliation; execution failures are marked Blocked and require a
GitHub job rerun. Existing open PRs are recovered by their deterministic branch name.
Changing generation permits **all** eligible issues to dispatch again.

The agent has a 30-minute execution timeout inside the 45-minute job. Configured
validation commands run after it finishes. Publication rechecks current project
enrollment and status, pushes a separate branch, opens a PR, and requests the
configured merge policy. GitHub waits for required checks before automatic merging.
Workflow-file edits are refused because the App has no Workflows permission.
Durable agent-session resume and automatic retries after a mid-task usage limit
are not implemented. Linear credentials stay in Azure and are not sent to the agent.

Azure Table `ClusterforkDispatches` records `dispatching`, `dispatched`, or
`uncertain`. `dispatched` means GitHub accepted the request; verify actual runner
success in Actions. Atomic receipt creation prevents concurrent delivery duplicates.
Definite GitHub rejections release the receipt so queue retries can recover. A
timeout/server error may have occurred after GitHub accepted a request: that receipt
is held as `uncertain`, and a crash may leave `dispatching`. Check Actions before
manually removing such a receipt in Azure Storage Explorer. This prioritizes avoiding
duplicates over automatically retrying an ambiguous request. It does not claim
exactly-once execution. Inspect `clusterfork-events-poison` after repeated failures.

Common causes: missing App installation, incorrect numeric App ID/PEM, workflow
not yet on `main`, wrong webhook signing secret, missing project label, or an issue
outside the mapped project/Todo state. The registry and app installation must both
allow the destination repository.
