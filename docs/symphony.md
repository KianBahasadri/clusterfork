# Symphony

Symphony runs one `codex app-server` subprocess with a dedicated Codex thread
and chat history per GitHub repository, while retaining cross-repository read
and planning access across mapped projects. Each project
needs a GitHub repository URL in its description or external links. Repositories
are cloned on first use. Projects that link to the same repository share its
worktree queue, coordinator conversation, and Codex thread. Project chips select
a Linear project ID, so projects that share
a repository remain distinct in the issue panes while showing an unread dot on
unselected repository chips when new activity arrives elsewhere. Use `/new` to
start a fresh Codex thread for the active repository without clearing the
stored chat transcript.

## Plan and execute

Open Symphony on the tailnet and sign in to Codex with the device flow if
prompted. Choose a project chip in the page header. The desktop layout puts that
project's Backlog on the left, coordinator chat in the middle, and Todo, In
Progress, and Done in the right-hand Queue panel. Each side panel has an
independent header control that collapses it to a compact rail on desktop or a
header row on narrow screens, giving the reclaimed space to chat; either panel,
both, or neither can be collapsed. The choice lasts for the page lifetime,
including project switches, refreshes, and viewport changes, and resets on
reload. Narrow screens stack the same panes. Backlog is the unapproved idea
list. Expand an issue's proposed plan and refine it with the coordinator.
Approve the plans you want in chat, including plans
across projects. The coordinator records
each approval, locks the current Linear plan, and moves the issue to Todo. A
Todo issue without that recorded approval is visibly awaiting approval and
never runs; writing a plan or changing its Linear status is not approval.
Recorded Todo approvals dispatch automatically when a repository, worker slot,
and provider are available, moving the issue to In Progress as work starts.
Issues returned to Backlog, canceled, or moved out of the approved project do
not start. Completed, Canceled, and Duplicate are terminal states across issue
lists, approvals, and state transitions.

For work that genuinely spans repositories, split it into one Backlog issue per
repository and link them with child or blocking relationships. `create_issue`
creates one issue in an enrolled repository's active Linear project and unique
Backlog state. It requires `repo`, `title`, `description`, an explicit `priority`
from 1 to 4, and an `estimate` from the target team's configured scale. Optional
`project_id` selects among several projects mapped to the repository, while
`parent_id`, `blocked_by`, and `labels` reuse existing issues and labels. It
rejects unmapped or archived projects, ambiguous teams or labels, invalid
metadata, and related issues outside enrolled projects. Creation never records
approval, moves work to Todo, or dispatches a worker. If blocking links partly
fail, the created issue's ID, identifier, and URL are still returned with the
failed links; reconcile them in Linear instead of recreating the issue. Reconcile
an uncertain creation response in Linear before another create attempt. Each
issue still needs the user's explicit chat approval before `approve_issue`; a
parent's approval or status never approves its children, and the coordinator
sequences dependent work in the conversation rather than relying on links to
schedule it.

Symphony keeps a shared cache of every mapped project's issues using a batched,
paginated Linear query. It reconciles every 300 seconds, promptly after a
successful local Linear write, and after a verified Issue or Project webhook.
Local writes for plan changes, approval, In Progress dispatch, and Done
completion, plus overlapping webhook deliveries, coalesce for about 250 ms into
one serialized refresh. A request received while a fetch is running triggers
another refresh after it completes. Project webhooks also invalidate the
60-second project mapping cache, so renamed projects and changed repository
links appear promptly. The cache includes open issues and Done issues completed
within the last rolling 24 hours, using `completedAt` rather than `updatedAt`.
Older Done, Canceled, and Duplicate issues stay out of both panes, and expired
Done entries disappear on a normal refresh without an issue edit. The browser
receives the lists through its regular status requests when they change.
Visible idle tabs poll `/api/state` every second, hidden idle tabs every two
seconds, and active coordinator turns use the faster chat cadence below.
Switching projects uses the cache and restores each pane's scroll position and
expanded plans.
Backlog stays on the left. Todo, In Progress, and Done are collapsible groups in
the right-hand Queue panel, labeled with those status names. The whole Queue
panel collapses independently from those status groups and preserves their open
state. Todo and In Progress sort from Urgent to Low priority with unprioritized
issues last; recent Done sorts newest completed first. Groups start expanded,
remember their collapsed state per project, and show an empty state when they
have no issues.
Status groups open and close with a visible spring using the standard Web
Animations API. Rapid clicks reverse from the current height; reduced motion
disables the animation.
Refresh failures keep the last successful lists and retry with bounded
backoff. Project chips use their Linear names and emoji. Issue cards show the
identifier, workflow icon and title, with compact pills for priority, due date,
estimate, and colored labels. Workflow icons match Linear's dotted, empty,
half-filled, and filled status shapes, using the configured colors with stronger
contrast in light mode. Worker phases, proposed plans, and an awaiting-approval
badge appear on their issues. A round arrow on an unstarted Backlog card asks
the coordinator for a proposed plan in chat while preserving the composer draft;
it neither approves nor queues work. Expandable proposed plans render Markdown
with the same formatting and safety rules as issue details, while the saved plan
text remains the source for approval. When `set_plan` saves a proposal, Symphony
also posts a proposal entry in chat with a one-click **Accept** button (and shows
**Accept** inside unapproved issue cards' `Proposed plan` dropdown), backed by
same-origin `POST /api/approve` with live `planHash` verification so operators can
lock and queue unmodified plans without spending a Codex turn (while
natural-language approval via `approve_issue` still works).

Click an issue card to open its details in a modal. The description, attachments,
documents, comments, and activity appear beside its properties and related issues.
Related issue links open their context in the same modal. Details preload in the
background with at most two preloads running at once, prioritizing the selected
project. Requests share an in-page cache and use versioned URLs based on
Linear's `updatedAt`, so the browser's private HTTP cache reuses unchanged details
across reloads. A changed issue preloads its new version without refetching the
panes. Mistune renders Markdown with raw HTML escaped.
Issue cards also show the time spent in their current Backlog, Todo, In Progress,
or Done stage. Symphony reads paginated Linear status history, uses the issue's
creation time only when complete history establishes the initial status, and
keeps the open interval moving in the browser without refetching Linear or
rebuilding the cards. Incomplete, inconsistent, or unavailable history shows an
unavailable chip instead of a guessed duration.
Linear uploads stream through Symphony using its existing Linear credentials,
including private images and range requests. The key stays on the server.
Close the modal with Close, Escape, or the backdrop to return focus to its card.
The modal opens on the Issue tab; the Execution tab loads on demand from a
non-cached endpoint and refreshes while it is open. It shows tracked stage totals,
the recorded In Progress breakdown with worker attempt totals, reviewer time,
measured, concurrent wall, waiting, and overlap values, retained GitHub
workflow/job/attempt details, and a Timeline that starts collapsed. Reviews &
notes by attempt retains bounded, redacted notes.md and review.md snapshots for
each recorded attempt, with its reviewer verdict, model, and finding count.
Repeated findings across reviewer passes count separately; incomplete totals are
labeled partial or at least, and activity without retained records stays
unavailable rather than guessed. Coverage is labeled when records are partial,
lower-bound, or unavailable; active CI runs stop at their last observed GitHub
response rather than growing through unobserved time. Diagnostic worker-log and
verification excerpts are bounded, redacted server-side, and rendered as text; CI
entries link to their exact GitHub runs and job logs. Live refreshes preserve
expanded sections, diagnostics, focus, selection, and scroll position. Closing the
tab stops its refresh. The app fills the viewport below the project header, keeping the page
itself fixed while the chat and issue panes scroll independently. Connection
failures appear as a temporary notice while Symphony reconnects.

Type `/` to see commands. Use the arrow keys to choose one, Tab to complete it,
or Enter to run it. Enter sends a message, Shift+Enter inserts a newline, and
the up arrow at the start of the prompt recalls previous inputs. Escape closes
command suggestions. Clicking or tapping the composer leaves its border
neutral; keyboard navigation shows its focus ring. Paste PNG, JPEG, or WebP
images into the composer to attach up to four images per message, 5 MiB each
and 10 MiB total. Pending thumbnails have accessible remove controls, may
accompany text or be sent alone, and remain available for retry after a failed
submission. Accepted images are stored on the Symphony data volume, passed to
Codex as native image inputs, and shown with their user message after reloads.
Slash commands do not accept images.
Submitting a message shows an accessible Sending status immediately, including
while pasted images encode, and prevents duplicate sends until the request
finishes. The server accepts the saved message and the composer displays that
canonical row without waiting for the next status poll; its user-visible text,
attachments, and the coordinator's separate working state stay distinct. A
rejected submission keeps the draft and images available and shows the error.
Coordinator replies render Markdown links, emphasis, paragraphs, lists, and
code; external and relative link destinations are preserved. Raw HTML is escaped
and unsafe URL schemes are blocked. User messages and operational notices remain
plain text. Live replies update in place as their Markdown develops, keeping
unchanged blocks and text nodes instead of replacing the whole response. A
visible active turn polls every 200 ms; visible idle polls return to one second,
and hidden tabs stay at two-second polls. A full 200-message backlog drains
without waiting. The chat follows the bottom only while it is already near the
bottom, so scrolling up pauses following during stream updates, accepted messages,
and delayed image layout. Interrupting a turn clears any pending live presentation,
and reduced-motion preferences do not add animated typing.
Use the Interrupt button to stop an active turn. If Codex exits during a turn,
Symphony clears the partial response and reconnects to the conversation on the
next message. The composer shows the current model and reasoning effort,
context used, 5-hour and weekly quota when available, and turn timing. `/status`
also lists approved work. Symphony uses the design guide palette and follows the
browser's light or dark preference. The favicon is a multi-size ICO.

Use `/new` in chat to start a fresh Codex thread for the active repository, or `/compact` to compact the current thread (showing a queued notice when sent during an active turn and compaction progress in the timing line until complete). A message sent during a turn in the same repository steers the active turn. A message for a different repository queues for the next turn. The app uses the
selected project's repository working directory and includes the shared
`agents/AGENTS.md` defaults before its repository `AGENTS.md` when starting a
session or when either instruction file changes, keeping subsequent user turns
lean (reading live approved work from `worker_status` and each issue's locked
approved plan, worker notes, and review report from `read_notes`). After a
`contextCompaction`, the next coordinator turn includes a one-shot reminder to
use `worker_status` and `read_notes` (and re-injects instruction rules only if
they differ from the thread's native rules or were edited). The Config button
in the page header opens a dialog for managing **OpenCode Go** credentials and
the **Codex** account alongside a read-only view of that exact global
`AGENTS.md` file; opening Config starts no coordinator turn. OpenCode Go uses
OpenCode's native PVC credential store under `worker-data/opencode/opencode.db`
(`/data/symphony/worker-data/opencode/opencode.db`, mode `0600`) via
`XDG_DATA_HOME` (with read-only recognition of pre-migration `auth.json`),
managed via `opencode auth --standalone` over stdin so unrelated provider
integrations are preserved and credentials survive restarts; startup never
reads or seeds `OPENCODE_GO_API_KEY` from the environment, and configuring or
clearing the key waits when a worker is active (returning `409`). When OpenCode
Go is unconfigured, new worker dispatches wait without consuming attempts until
configured. Codex uses `/data/symphony/codex/auth.json` via `CODEX_HOME`,
exposing sanitized status/account metadata (`email`, `plan`, `workspace`),
device sign-in, cancellation, and sign-out in Config while preserving saved
conversation threads across account changes and rejecting account mutations with
`409` while the coordinator is busy. Submitted keys are never returned to the
browser or written into chat, SQLite, logs, or responses.
Explicit user or task directions and applicable repository or nested
instructions override the shared defaults, while Symphony's approval,
publication, and tool restrictions remain authoritative. Symphony links the same
file into Codex's `CODEX_HOME/AGENTS.md` and the isolated
`XDG_CONFIG_HOME/opencode/AGENTS.md` used by workers and the reviewer, so native
discovery also applies to fresh sessions, resumed-session update notices, and
compaction requests. A missing,
unreadable, or oversized shared file prevents startup or appears as a viewer
error instead of being silently ignored.

Editing `agents/AGENTS.md` follows the normal repository, CI, image, and
idle-aware updater flow. Codex reloads the file for each model turn: in
isolated-home probes a changed file appeared in the next turn without a restart,
and a removed file disappeared from the active instruction block after process
restart and saved-session resume. Earlier instruction updates remained only in
conversation history, where later updates explicitly replace them. OpenCode
reads the current file for new sessions and the reviewer starts fresh, but a
resumed OpenCode session keeps its original instruction block in the system
prompt. When the file changes or disappears, OpenCode adds a system-update
message carrying the new text or removal notice; the old block is not rewritten.
Symphony's coordinator context injects the current canonical instruction files on
first turn, after changes, or when needed after compaction without pasting
repeated copies into every turn.

Isolated-home probes of the pinned Codex and OpenCode builds exercised completed
manual and automatic compaction followed by ordinary model requests, plus
changed and removed defaults and two repositories with conflicting local rules.
The current shared defaults and repository rules were present in the actual
model requests where applicable, including after saved-session resume, subject
to the OpenCode resumed-session limitation above. Production credentials, live provider
endpoints, and the idle-aware rollout were not exercised. Codex uses its shell
and live web search inside the restricted pod. It can inspect checkouts and
worker logs and use `gh` to diagnose CI. OpenCode workers handle implementation
by default.

OpenCode uses the Go subscription and caps concurrent workers at three. Each
attempt has a one-hour safety timeout. Every new and resumed implementation
worker receives default remote MCP servers for Context7
(`https://mcp.context7.com/mcp`) and Exa web search (`https://mcp.exa.ai/mcp`)
through `OPENCODE_CONFIG_CONTENT`, while OpenCode's interactive built-in
`websearch` tool, direct pushes, PR creation, and subagents are denied. Worker
and reviewer environments strip GitHub, Linear, OpenAI, Codex, OpenCode Go API
key, and Linear webhook signing-secret variables. The container image includes
standard CLI utilities (`jq`, `rg`, `sqlite3`, `tmux`, `gawk`, `patch`, `file`,
`zip`) and pinned Python Playwright in `/opt/symphony` with its managed Chromium
under `/opt/playwright-browsers`, discovered through
`PLAYWRIGHT_BROWSERS_PATH`. Launch Chromium through Playwright from
`/opt/symphony/bin/python` with `--no-sandbox`, `--disable-dev-shm-usage`, and
`--disable-gpu` under restricted PodSecurity; Docker, QEMU, Go, and privileged
networking tools remain out of scope for the Symphony image.
The checklist is a shell command file that Symphony runs a second time, in the
worktree, to get its own exit status.
The worker's notes and log live in `runs/<issue-id>/` on the PVC. The worktree
stays reserved through audit, rework, and CI. After verification exits 0 and
before the coordinator audit, Symphony runs one stateless read-only OpenCode
reviewer in a fresh session. It uses `opencode-go/kimi-k3.1-pro#low`
unless `SYMPHONY_REVIEW_MODEL` overrides the model. The reviewer receives the
approved plan, notes, and full diff against the recorded base, with repository
edits, writes, shell, subagents, and MCP tools denied. Symphony writes a bounded
`runs/<issue-id>/review.md` with a structured verdict and file/line findings,
or an honest unavailable/error report; raw output and the diff remain in
`review.log` and `review.diff`. Only the terminal assistant message ending in
the final stop step becomes the report; progress and tool-call text stay in the
log, and a missing or nonterminal stop is unavailable. A clean verdict must
stand alone. Review has a ten-minute ceiling, and missing, malformed, failed, or
timed-out output is reported as unavailable rather than as a clean verdict.
Failed verification skips review, stale reports are cleared
on every attempt, and the tested tree is compared after review so a changed
tree blocks handoff. Reviewer findings are advice, not approval.
Existing work without a report shows review unavailable. The coordinator reads
notes and review before shipping. Worker errors, rejected pushes, and failed CI
appear in chat and the issue panes with the worktree preserved. OpenCode Go
quota, rate-limit, and authentication failures pause new dispatches. Use
`/retry KIAN-123` to revisit a parked issue and `/continue` when the provider
failure has been resolved.

Symphony records each issue's attempts, worker startup, verification,
coordinator work that is explicitly tied to the issue, publication, CI waiting,
failures, completion, and interruption in the existing SQLite ledger. These
records survive worktree cleanup and restarts. A restart ends an open interval
at the last recorded activity and marks the unobserved gap instead of extending
the interval through the outage. Historical periods before these records existed
are shown as unavailable rather than as zero time.

The coordinator pump dispatches recorded approvals whose live issues are still
Todo in their approved project. The shared dispatcher rechecks that live state
for manual spawns too, and resume requires the issue to still be In Progress.
It checks the existing worker cap, repository reservation, provider pause, and
rework limit before reserving the slot and moving the issue to In Progress.
Waiting for capacity stays idle without waking the coordinator or consuming
attempts. Linear failures remain visible on the issue while queued work retries
after a normal refresh, and an unconfirmed status update is parked with a
diagnostic. A restart recovers queued approvals once without duplicate
dispatch; issues moved back to Backlog, canceled, or moved to another project
are skipped.

The coordinator pump batches pending worker events per repository into one turn
in that repository's coordinator session and checkout. A committed
lifecycle change acknowledges the issue's earlier pending notifications, and
events whose recorded phase no longer matches the task are dropped before
delivery; actionable review, blocked, failed Linear completion, and worktree
cleanup warning notifications remain deliverable. Non-actionable push (`ci`)
and clean `Done.` milestones post directly to chat, supersede earlier pending
notifications, and wake automatic dispatch without starting a coordinator turn.
Only the delivered batch is
acknowledged, so events arriving during that turn stay pending.

Symphony writes one squash commit and pushes directly to `main`. It waits for
the exact revision's GitHub Actions workflows whose push filters match the
branch and changed paths. If none match, Symphony advances the repo after the
push. Keep deployment jobs dependent on successful CI in the repository's
workflow. After verified publication, Symphony sets Linear to Done and removes
the worktree. The CI watcher retains workflow run, job, and attempt identities,
timestamps, conclusions, URLs, and failure diagnostics. Repeated observations
update the same attempt, while old failed attempts and earlier revisions remain
visible. Timing distinguishes workflow and job totals, concurrent wall time,
and CI waiting so overlaps are not counted twice; missing or partial GitHub
details are labeled unavailable. A restart preserves the conversation, queued
and approved plans, worker sessions and pending CI watches. When the
coordinator's tool definitions change, Symphony starts a fresh Codex thread and
records the change in the chat history.

## Deploy on Guaranine

The `test` workflow publishes the image to GHCR from `main` after tests pass.
Deploy the full commit tag (or image digest) from the successful run. The main
deployment uses a single restricted pod and an OpenEBS local PVC. A private
Tailscale Serve sidecar proxies the app's loopback listener over tailnet HTTPS.
A separate restricted userspace Tailscale gateway exposes only the Funnel route
`/webhooks/linear` to the Symphony Service on port 8082.

Guaranine's Tailscale policy must add `tag:guaranine-webhook-gateway` to
`tagOwners` and grant it the `funnel` node attribute in
`klever-lab/guaranine/tailscale/policy.json`. Follow Guaranine's documented
policy application and tests before registering the webhook. The private UI
gateway retains `tag:guaranine-app-gateway` and `AllowFunnel` false.

Create the namespace and provide these Kubernetes secrets:

| Secret | Required values |
| --- | --- |
| `symphony-api` | `LINEAR_API_KEY`, `GITHUB_TOKEN` with repository content and workflow write and Actions read access, and `LINEAR_WEBHOOK_SECRET` matching the Linear webhook signing secret |
| `symphony-tailnet` | Single-use, preauthorized auth key in `authkey` for `tag:guaranine-app-gateway` |
| `symphony-webhook-tailnet` | Single-use, preauthorized auth key in `authkey` for `tag:guaranine-webhook-gateway` |

Store the input files outside this checkout with mode 0600. The Codex login, OpenCode Go credentials in
`worker-data/opencode/opencode.db`, and the Tailscale node identities live on their
PVCs and persist across pod replacements; configure or rotate OpenCode Go
credentials directly through the Config dialog rather than Kubernetes secrets.

```bash
kubectl apply -f symphony/kubernetes/namespace.yaml
kubectl apply -f symphony/kubernetes/storage-and-network.yaml
kubectl set image --local -f symphony/kubernetes/deployment.yaml \
  symphony=ghcr.io/kianbahasadri/symphony:FULL_COMMIT_SHA -o yaml | kubectl apply -f -
kubectl apply -f symphony/kubernetes/webhook-gateway.yaml
kubectl apply -f symphony/kubernetes/updater.yaml
kubectl -n symphony rollout status deployment/symphony
kubectl -n symphony rollout status deployment/symphony-webhook
kubectl -n symphony exec deployment/symphony -c tailscale -- \
  tailscale --socket=/tmp/tailscaled.sock serve status
kubectl -n symphony exec deployment/symphony-webhook -c tailscale -- \
  tailscale --socket=/tmp/tailscaled.sock serve status
```

`symphony/kubernetes/updater.yaml` installs an idle-aware `CronJob`
(`symphony-updater`, every two minutes) in a separate restricted pod with RBAC
limited to `get` and `patch` on `deployment/symphony`. When the published
`ghcr.io/kianbahasadri/symphony:main` digest changes, the updater checks
Symphony's internal read-only `/idle` endpoint on port 8081 (reachable only
from `app: symphony-updater`) and patches the deployment to the pinned
`@sha256:...` digest once no coordinator turn, queued message, undelivered
event, or active task (`approving`, `working`, `publishing`, `ci`, `finishing`)
is running. If `deployment/symphony` has zero ready replicas while scaled up,
the updater rolls forward even when `/idle` is unreachable so a broken rollout
can recover on the next push to `main`. The Symphony pod itself keeps
`automountServiceAccountToken: false` and has no Kubernetes API egress.

Each image bakes its source commit as `SYMPHONY_COMMIT`. Symphony resolves that
commit tag to a digest and compares it with the published `:main` digest at
startup and every 60 seconds. Status polls read only the cached result, and an
invalid or missing build identity or unavailable digest never reports an
update. When a newer image is confirmed, chat offers **Update**. Updating holds
new automatic Todo dispatches and `spawn_worker` admissions across every
repository while work already in progress continues; resumes, review,
publication, CI watching, completion, coordinator chat, and worker events are
not blocked. The notice then offers **Cancel**, which restores automatic
dispatch immediately. Draining is process-local and never persisted, so a
restart begins with normal automatic dispatch. While draining, `/idle` also
waits for work in review, blocked rework, or failed completion, while queued
approved Todo issues alone do not hold it. The two-minute updater schedule
means a confirmed update is not rolled out immediately, and the browser does
not reload by itself.

Open the private HTTPS address reported by the UI gateway and verify the project
chips and panes load. Register one Linear webhook for Issue and Project events
against the HTTPS address reported by the webhook gateway, ending in
`/webhooks/linear`, and install its signing secret as `LINEAR_WEBHOOK_SECRET` in
`symphony-api`. Registering a webhook requires workspace admin access. If the
secret is absent, the private UI and local-write refreshes still start, while
`POST /webhooks/linear` fails closed with 503 and no refresh. The listener
verifies `Linear-Signature` over the exact raw body with HMAC-SHA256,
requires a numeric `webhookTimestamp` in milliseconds within 60 seconds of server
time, accepts only Issue and Project create, update, or remove payloads, and
returns 200 after signaling the refresh loop. Invalid signatures, stale or
malformed payloads, oversized or incomplete bodies, timeouts, and wrong routes
or methods are rejected without waking the refresh loop. The signing secret is
stripped from worker and reviewer subprocesses, git environments, and recorded
execution diagnostics.

The public gateway reaches only the webhook listener; the private UI remains
reachable only through its own tailnet gateway. Kubernetes NetworkPolicy
enforces default-deny in the namespace, allowing the Symphony pod only DNS,
public TCP 443 egress, port 8081 ingress from the updater, and port 8082 ingress
from `app: symphony-webhook`. The webhook gateway may use DNS, public TCP 443,
and Symphony port 8082. Linear retries failed deliveries three times with longer
backoff and may disable an unresponsive webhook, so the 300-second reconciliation
remains the fallback. [Deferred work](symphony-follow-ups.md) tracks ntfy,
additional composer commands, and hostname-level egress.
