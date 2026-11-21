# Symphony

Symphony runs Codex as one coordinator across Linear projects. Each project
needs a GitHub repository URL in its description or external links. Repositories
are cloned on first use. Projects that link to the same repository share its
worktree queue. Project chips select a Linear project ID, so projects that share
a repository remain distinct.

## Plan and execute

Open Symphony on the tailnet and sign in to Codex with the device flow if
prompted. Choose a project chip in the page header. The desktop layout puts that
project's Backlog on the left, coordinator chat in the middle, and Todo, In
Progress, and Done on the right. Narrow screens stack the same panes.
Backlog is the unapproved idea list. Expand an issue's proposed plan and refine
it with the coordinator. Approve the plans you want in chat, including plans
across projects. The coordinator records
each approval, locks the current Linear plan, and moves the issue to Todo. A
Todo issue without that recorded approval is visibly awaiting approval and
never runs; writing a plan or changing its Linear status is not approval.
Recorded Todo approvals dispatch automatically when a repository, worker slot,
and provider are available, moving the issue to In Progress as work starts.
Issues returned to Backlog, canceled, or moved out of the approved project do
not start. Completed, Canceled, and Duplicate are terminal states across issue
lists, approvals, and state transitions.

Symphony refreshes a shared cache of every mapped project's issues every 30
seconds using a batched, paginated Linear query. The cache includes open issues
and Done issues completed within the last rolling 24 hours, using `completedAt`
rather than `updatedAt`. Older Done, Canceled, and Duplicate issues stay out of
both panes, and expired Done entries disappear on a normal refresh without an
issue edit. The browser receives the lists through its regular status requests
when they change. Switching projects uses the cache and restores each pane's
scroll position and expanded plans. Backlog stays on the left. Todo, In
Progress, and Done are collapsible groups on the right, labeled with those
status names. Todo and In Progress sort from Urgent to Low priority with
unprioritized issues last; recent Done sorts newest completed first. Groups
start expanded, remember their collapsed state per project, and show an empty
state when they have no issues.
Status groups open and close with a visible spring using the standard Web
Animations API. Rapid clicks reverse from the current height; reduced motion
disables the animation.
Refresh failures keep the last successful lists and slow retries. Project
chips use their Linear names and emoji. Issue cards show the identifier,
workflow icon and title, with compact pills for priority, due date, estimate,
and colored labels. Workflow icons match Linear's dotted, empty, half-filled,
and filled status shapes, using the configured colors with stronger contrast
in light mode. Worker phases, proposed plans, and an awaiting-approval badge
appear on their issues.

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
the recorded In Progress breakdown with measured, concurrent wall, waiting, and
overlap values, retained GitHub workflow/job/attempt details, and a chronological
timeline. Coverage is labeled when records are partial, lower-bound, or
unavailable; active CI runs stop at their last observed GitHub response rather
than growing through unobserved time. Diagnostic worker-log and verification
excerpts are bounded, redacted server-side, and rendered as text; CI entries link
to their exact GitHub runs and job logs. Live refreshes preserve expanded
diagnostics, focus, selection, and scroll position. Closing the tab stops its
refresh. The app fills the viewport below the project header, keeping the page
itself fixed while the chat and issue panes scroll independently. Connection
failures appear as a temporary notice while Symphony reconnects.

Type `/` to see commands. Use the arrow keys to choose one, Tab to complete it,
or Enter to run it. Enter sends a message, Shift+Enter inserts a newline, and
the up arrow at the start of the prompt recalls previous inputs. Escape closes
command suggestions first. Clicking or tapping the composer leaves its border
neutral; keyboard navigation shows its focus ring.
Coordinator replies render Markdown links, emphasis, paragraphs, lists, and code;
external and relative link destinations are preserved. Raw HTML is escaped and
unsafe URL schemes are blocked. User messages and operational notices remain
plain text.
Press Escape anywhere on the page or use Interrupt
to stop an active turn. If Codex exits during a turn, Symphony clears the
partial response and reconnects to the conversation on the next message.
The composer shows the current model and reasoning effort, context used,
5-hour and weekly quota when available, and turn timing. `/status` also lists
approved work. Symphony uses the design guide palette and follows the
browser's light or dark preference. The favicon is a multi-size ICO.

A message sent during a turn in the same repo steers the active
turn. A message for a different repo queues for the next turn. The app uses the
selected project's repository working directory and includes its AGENTS.md and
current work when starting a turn. Codex uses its shell and live web search
inside the restricted pod. It can inspect checkouts and worker logs and use
`gh` to diagnose CI. OpenCode workers handle implementation by default.

OpenCode uses the Go subscription and caps concurrent workers at three. Each
attempt has a one-hour safety timeout. Every new and resumed implementation
worker receives default remote MCP servers for Context7
(`https://mcp.context7.com/mcp`) and Exa web search (`https://mcp.exa.ai/mcp`)
through `OPENCODE_CONFIG_CONTENT`, while OpenCode's interactive built-in
`websearch` tool, direct pushes, PR creation, and subagents are denied. Worker
environments strip GitHub, Linear, OpenAI, Codex, and OpenCode Go API key
variables. The container image includes standard CLI utilities (`jq`, `rg`,
`sqlite3`, `tmux`, `gawk`, `patch`, `file`, `zip`) and headless system
`chromium` (`/usr/bin/chromium`, invoked with `--headless --no-sandbox
--disable-dev-shm-usage --disable-gpu` under restricted PodSecurity); Docker,
QEMU, Go, and privileged networking tools remain out of scope for the Symphony
image. The checklist is a shell command file
that Symphony runs a second time, in the worktree, to get its own exit status.
The worker's notes and log live in `runs/<issue-id>/` on the PVC. The worktree
stays reserved through audit, rework, and CI. After verification exits 0 and
before the coordinator audit, Symphony runs one stateless read-only OpenCode
reviewer in a fresh session. It uses `opencode-go/muse-spark-1.3-contributor`
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
Deploy the full commit tag (or image digest) from the successful run. The
deployment uses a single restricted pod and an OpenEBS local PVC. Tailscale
Serve proxies the app's loopback listener over private HTTPS.

Create the namespace and provide these Kubernetes secrets:

| Secret | Required values |
| --- | --- |
| `symphony-api` | `LINEAR_API_KEY`, `GITHUB_TOKEN` with repository content and workflow write and Actions read access, and `OPENCODE_GO_API_KEY` |
| `symphony-tailnet` | Single-use, preauthorized auth key in `authkey` for `tag:guaranine-app-gateway` |

Store the input files outside this checkout with mode 0600. The Codex login and
the Tailscale node identity live on the PVC and persist across pod replacements.

```bash
kubectl apply -f symphony/kubernetes/namespace.yaml
kubectl apply -f symphony/kubernetes/storage-and-network.yaml
kubectl set image --local -f symphony/kubernetes/deployment.yaml \
  symphony=ghcr.io/kianbahasadri/symphony:FULL_COMMIT_SHA -o yaml | kubectl apply -f -
kubectl apply -f symphony/kubernetes/updater.yaml
kubectl -n symphony rollout status deployment/symphony
kubectl -n symphony exec deployment/symphony -c tailscale -- \
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

Open the HTTPS address reported by Tailscale and verify the project chips and panes load.
Kubernetes NetworkPolicy enforces default-deny in the namespace, allowing the
Symphony pod only DNS, public TCP 443 egress, and port 8081 ingress from the
updater. The gateway tag and Serve configuration control access to the UI.
[Deferred work](symphony-follow-ups.md) tracks ntfy, additional composer
commands, and hostname-level egress.
