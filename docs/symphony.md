# Symphony

Symphony runs a per-repository **Antigravity CLI (`agy`)** coordinator alongside a
zero-ceremony, sequential 2-tier coding pipeline (**DeepSeek-V4.1-Flash** via
OpenCode Go with automatic escalation to **Gemini 4 Hydrogen XHigh** via `agy`),
while retaining cross-repository read and planning access across mapped projects.
Each Linear project needs a GitHub repository URL in its description or external
links. Repositories are cloned on first use. Projects that link to the same
repository share its worktree queue, coordinator conversation, and chat history.
Project chips select a Linear project ID, so projects that share a repository
remain distinct in the issue panes while showing an unread dot on unselected
repository chips when new activity arrives elsewhere. Use `/new` to start a fresh
Antigravity conversation for the active repository without clearing the stored
chat transcript.

## Queue, plan, and execute

Open Symphony on the tailnet and sign in to Antigravity in **Config** (or via
`/login`) if prompted. Choose a project chip in the page header. The desktop
layout puts that project's Unsure and Backlog groups on the left, coordinator
chat in the middle, and Todo, In Progress, Done, Canceled, and Duplicate in the
right-hand Queue panel. Each side panel has an independent header control that
collapses it to a compact rail on desktop or a header row on narrow screens,
giving the reclaimed space to chat; either panel, both, or neither can be
collapsed. The choice lasts for the page lifetime, including project switches,
refreshes, and viewport changes, and resets on reload. Narrow screens stack the
same panes.

Tasks enter a single unified sequential queue per repository (`1` active task per
repository, up to `3` concurrent workers across repositories) through three doors:

1. **Linear `Todo` (zero-ceremony auto-queue):** Any Linear issue in `Todo` is
   automatically queued (`sync_todo_issues`) and dispatched to a Flash worker
   with `0` Antigravity tokens—no mandatory `## Proposed Plan`, no two-step
   approval ceremony, and no coordinator audit turn on passing runs. Moving an
   unstarted issue out of `Todo` removes it from the queue; reopening a `Done`
   issue into `Todo` re-queues it. Completed, Canceled, and Duplicate remain
   terminal states across issue lists and state transitions.
2. **Ad-hoc via Antigravity chat (`enqueue_task`):** Ask the coordinator to
   discuss tradeoffs, verify files, or kick off ad-hoc work; it calls
   `enqueue_task(repo, prompt, title)` over MCP to queue an `ADHOC-N` task
   directly onto the repository's Flash queue without requiring a Linear issue.
3. **Direct `/run <prompt>` slash command:** Queues an `ADHOC-N` task straight
   onto the active repository's Flash queue with `0` Antigravity tokens.

For work that genuinely spans repositories or belongs in the Backlog, use
`create_issue` to create one Backlog issue per repository and link them with
child or blocking relationships. `create_issue` requires `repo`, `title`,
`description`, an explicit `priority` from 1 to 4, and an `estimate` from the
target team's configured scale. Optional `project_id` selects among several
projects mapped to the repository, while `parent_id`, `blocked_by`, and `labels`
reuse existing issues and labels. It rejects unmapped or archived projects,
ambiguous teams or labels, invalid metadata, and related issues outside enrolled
projects. Creation never moves work to `Todo` or dispatches a worker. If
blocking links partly fail, the created issue's ID, identifier, and URL are
still returned with the failed links; reconcile them in Linear instead of
recreating the issue.

Planning in Backlog remains optional. A round arrow on an unstarted Backlog card
asks the coordinator for a proposed plan in chat while preserving the composer
draft. When `set_plan` saves a `## Proposed Plan` section, Symphony posts a
proposal entry in chat with a one-click **Accept** button (and shows **Accept**
inside the card's `Proposed plan` dropdown), backed by same-origin
`POST /api/approve` with live `planHash` verification, which moves the issue
straight to Linear `Todo` for automatic Flash dispatch.

Symphony keeps a shared cache of every mapped project's issues using a batched,
paginated Linear query. It reconciles every 300 seconds, promptly after a
successful local Linear write, and after a verified Issue or Project webhook.
Local writes plus overlapping webhook deliveries coalesce for about 250 ms into
one serialized refresh. Project webhooks also invalidate the 60-second project
mapping cache, so renamed projects and changed repository links appear promptly.
The cache includes open issues, Done issues completed within the last rolling 24
hours (`completedAt`), and Canceled/Duplicate issues canceled within the last
rolling 24 hours (`canceledAt`). Visible idle tabs poll `/api/state` every
second, hidden idle tabs every two seconds, and active coordinator turns every
200 ms. Switching projects uses the cache and restores each pane's scroll
position and expanded plans.

Unsure sits above Backlog in collapsible left-pane groups. Todo, In Progress,
Done, Canceled, and Duplicate are collapsible groups in the right-hand Queue
panel. Unsure, Backlog, Todo, and In Progress sort from Urgent to Low priority
with unprioritized issues last; recent Done sorts newest completed first, and
recent Canceled/Duplicate sort newest canceled first. Groups open and close with
a Web Animations API spring (disabled under reduced motion). Refresh failures
keep the last successful lists and retry with bounded backoff. Project chips use
their Linear names and emoji. Issue cards show the identifier, workflow icon,
title, priority, due date, estimate, colored labels, live stage duration (from
paginated Linear status history), optional proposed plan, and active worker
phase/tier badge.

Click an issue card to open its details in a modal (Issue tab and on-demand
Execution tab). Details preload in the background (at most two at once) and
share a versioned private cache keyed by `updatedAt`. Linear uploads stream
through Symphony using server-side credentials. The Execution tab refreshes live
while open and shows tracked stage totals, worker attempts, bounded redacted
`notes.md` and `worker.log` diagnostics, verification excerpts, retained GitHub
workflow/job/attempt details, and a collapsible Timeline while preserving
expanded sections, focus, selection, and scroll position.

## Coordinator, commands, and Config

The coordinator runs `agy --print --output-format stream-json --dangerously-skip-permissions`
per turn (resuming the repository's saved conversation ID via `--conversation`),
defaulting to `gemini-4-neon-high` and switchable via `/model <id>`. Symphony
registers a stdlib stdio MCP bridge (`symphony/mcp_bridge.py`) in
`$HOME/.gemini/config/mcp_config.json` (mode `0600`) that forwards Antigravity
tool calls to loopback `POST http://127.0.0.1:8080/internal/tool` authenticated
by a per-process bearer token (`SYMPHONY_INTERNAL_TOKEN`). Exposed MCP tools are
`enqueue_task`, `list_repos`, `list_issues`, `get_issue`, `set_plan`,
`worker_status`, `retry_task`, `create_issue`, `list_repo_buttons`,
`set_repo_button`, `delete_repo_button`, and `render_diagram`.

Type `/` to see commands: `/run `, `/stats`, `/model `, `/new`, `/compact`,
`/status`, `/mcp`, `/interrupt`, `/login`, `/continue`, and `/retry `. Enter
sends a message, Shift+Enter inserts a newline, and Up recalls previous inputs.
Paste up to four PNG, JPEG, or WebP images (5 MiB each, 10 MiB total); accepted
images are saved on the Symphony data volume and passed as file paths for the
coordinator to inspect with `read_file`. Slash commands do not accept images.
Submitting shows an accessible Sending status immediately and displays the
canonical server-saved message without waiting for the next poll. Assistant
Markdown renders incrementally in place with raw HTML escaped and unsafe URL
schemes blocked; user and worker messages remain plain text. Use **Interrupt**
(or `/interrupt`) to terminate an active coordinator subprocess cleanly.

The composer status line shows the active Antigravity model, 5-hour and weekly
quota windows (polled from Code Assist `loadCodeAssist` /
`retrieveUserQuotaSummary`), and turn timing (with pure-CSS animations disabled
under reduced motion). When Antigravity quota reaches `0%` or a turn fails with
a quota/rate-limit error, Symphony displays an alert banner with the reset
time, posts a one-shot chat notice, highlights `document.title`, optionally
fires one background browser `Notification`, and clears the alert automatically
once quota recovers or the account changes.

The coordinator can manage per-repository quick prompt buttons above the
composer (`attach` or `send` mode) and display Mermaid diagrams in a dedicated
panel above chat (`render_diagram`, surfacing syntax/render errors inline).
Symphony injects the shared `agents/AGENTS.md` defaults before the active
repository's `AGENTS.md` on the first turn of a session or when either file
changes, keeping subsequent turns lean (and adding a one-shot reminder after
`/compact`).

The **Config** button opens a dialog for managing **OpenCode Go** credentials
and **Antigravity** authentication alongside a read-only formatted Markdown view
of `agents/AGENTS.md` without starting a coordinator turn:

- **OpenCode Go:** Stored in OpenCode's native PVC credential store under
  `worker-data/opencode/opencode.db` (mode `0600`, with read-only recognition of
  pre-migration `auth.json`), managed via `opencode auth --standalone` over
  stdin so unrelated provider integrations survive restarts. Startup never reads
  `OPENCODE_GO_API_KEY` from the environment. Mutations return `409` while a
  worker is active; when unconfigured, queued work waits without consuming
  attempts.
- **Antigravity:** Sign-in runs `agy` headless with
  `DBUS_SESSION_BUS_ADDRESS=unix:path=<home>/.no-dbus` so `agy` writes
  `$HOME/.gemini/antigravity-cli/antigravity-oauth-token` (mode `0600`) on the
  PVC with no D-Bus or keyring daemon. Clicking **Sign in** surfaces the Google
  OAuth URL and an authorization-code paste form (both in Config and in the
  inline login banner); pasting the code completes sign-in and persists the
  refresh token across restarts. Sign-out removes the token while preserving
  saved SQLite conversation IDs. Mutations return `409` while the coordinator is
  busy.

Symphony links `agents/AGENTS.md` into `worker-config/opencode/AGENTS.md` so
OpenCode workers discover the shared defaults natively. A missing, unreadable,
or oversized shared file prevents startup or appears as a viewer error.

## 2-tier workers, auto-escalation, and shipping

1. **Tier 1 (Flash):** Every queued Linear `Todo` or `ADHOC-N` task starts on
   OpenCode Go (`opencode-go/deepseek-v4.1-flash#max`) in a fresh Git worktree
   reset to `origin/main` with a 15-minute safety timeout. Workers receive
   Context7 (`https://mcp.context7.com/mcp`) and Exa (`https://mcp.exa.ai/mcp`)
   remote MCP servers via `OPENCODE_CONFIG_CONTENT`, while built-in `websearch`,
   `git push`, `gh pr create`, and subagents are denied. Worker environments
   strip GitHub, Linear, OpenAI, OpenCode Go API key, webhook secret, and
   internal Symphony bridge token variables. Targeted verification in
   `runs/<id>/checks.sh` is optional (run when present; omitted cleanly when no
   local checks apply).
2. **Tier 2 (Auto-Escalation to Hydrogen):** If Flash fails (`timeout`,
   `check_failed`, `worker_error`, or `blocked`), Symphony cold-resets the
   worktree to `origin/main`, builds a compact failure receipt (`git diff --stat`
   + redacted `worker.log` tail + trigger), and runs one fresh headless
   `agy --model gemini-4-hydrogen-xhigh` worker (20-minute timeout, secrets
   stripped). Two guards protect quota:
   - If Antigravity is signed out, has an active usage error, or either `5h` or
     `weekly` quota is at `<= 20%` remaining (80% guard), auto-escalation is
     skipped and the task parks with the quota reason.
   - If OpenCode Go fails with a provider auth/quota/rate-limit error (401/429),
     Symphony sets `paused` and parks the task without wasting Antigravity
     escalation quota. Use `/continue` after resolving the provider issue.
3. **Auto-rebase push and CI:** When Tier 1 or Tier 2 passes, Symphony creates
   one squash commit. If `origin/main` advanced during the run, Symphony
   auto-rebases onto `origin/main` and re-runs optional `checks.sh` before
   pushing directly to `main`. It then waits for the exact revision's matching
   push-triggered GitHub Actions workflows (or finishes immediately if none
   match), marks Linear `Done` (skipped for `ADHOC-N`), and cleans up the
   worktree.
4. **Non-blocking `parked` branches:** If both tiers fail, auto-rebase hits a
   merge conflict, git push fails, or CI fails, Symphony pushes any unpushed WIP
   diff to `symphony/parked-<id>`, removes the pushed worktree, marks the task
   `phase='parked'`, posts the concrete error and branch in chat, and
   immediately releases the repository lock so the next queued task starts.
   Use `/retry <id>` to re-queue a parked task and `/stats` to review recent
   Flash pass rates, Hydrogen escalation triggers, and parked counts.

The container image installs `agy` (SHA512-verified from the official release
manifest), pinned `@opencode/cli`, `pnpm`, `mermaid`, standard CLI utilities
(`jq`, `rg`, `sqlite3`, `tmux`, `gawk`, `patch`, `file`, `zip`, `gh`), and
pinned Python Playwright with managed Chromium under `/opt/playwright-browsers`
(launch with `--no-sandbox --disable-dev-shm-usage --disable-gpu`).

Symphony records attempts, startup, worker, verification, explicitly attributed
coordinator turns, publication, CI waiting, failures, completion, and restart
gaps in SQLite alongside `coordinator_turns` latency/status rows and
`token_usage` rows (never fabricating USD costs for subscription plans). On
startup, legacy database rows (`approving`/`approved` -> `queued`,
`review`/`blocked` -> `parked`) migrate cleanly, interrupted `working` tasks
park cleanly for `/retry`, and interrupted `publishing`/`ci`/`finishing` tasks
recover automatically.

## Deploy on Guaranine

The `image` workflow (`workflow_run` after `test` succeeds on `main`) builds,
verifies, and publishes the image to GHCR asynchronously without holding
Symphony's push-CI repository lock. The main deployment uses a single restricted
pod (`runAsNonRoot`, UID `10001`, `readOnlyRootFilesystem: true`) and an OpenEBS
local PVC at `/data/symphony` (`HOME=/data/symphony/home`). A private Tailscale
Serve sidecar proxies the app's loopback listener (`127.0.0.1:8080`) over
tailnet HTTPS. A separate restricted userspace Tailscale gateway exposes only
the Funnel route `/webhooks/linear` to the Symphony Service on port 8082.

Create the namespace and provide these Kubernetes secrets:

| Secret | Required values |
| --- | --- |
| `symphony-api` | `LINEAR_API_KEY`, `GITHUB_TOKEN` with repository content and workflow write and Actions read access, and `LINEAR_WEBHOOK_SECRET` matching the Linear webhook signing secret |
| `symphony-tailnet` | Single-use, preauthorized auth key in `authkey` for `tag:guaranine-app-gateway` |
| `symphony-webhook-tailnet` | Single-use, preauthorized auth key in `authkey` for `tag:guaranine-webhook-gateway` |

Store input files outside this checkout with mode 0600. The Antigravity OAuth
token (`home/.gemini/antigravity-cli/antigravity-oauth-token`), OpenCode Go
credentials (`worker-data/opencode/opencode.db`), and Tailscale node identities
live on their PVCs and persist across pod replacements; configure or rotate both
through the Config dialog rather than Kubernetes secrets.

```bash
kubectl apply -f symphony/kubernetes/namespace.yaml
kubectl apply -f symphony/kubernetes/storage-and-network.yaml
kubectl set image --local -f symphony/kubernetes/deployment.yaml \
  symphony=ghcr.io/kianbahasadri/symphony:FULL_COMMIT_SHA -o yaml | kubectl apply -f -
kubectl apply -f symphony/kubernetes/webhook-gateway.yaml
kubectl apply -f symphony/kubernetes/updater.yaml
kubectl -n symphony rollout status deployment/symphony
kubectl -n symphony rollout status deployment/symphony-webhook
```

`symphony/kubernetes/updater.yaml` installs an idle-aware `CronJob`
(`symphony-updater`, every two minutes) in a separate restricted pod with RBAC
limited to `get` and `patch` on `deployment/symphony`. When the published
`ghcr.io/kianbahasadri/symphony:main` digest changes, the updater checks
Symphony's internal read-only `/idle` endpoint on port 8081 and patches the
deployment to the pinned `@sha256:...` digest once no coordinator turn, queued
message, or active task (`working`, `publishing`, `ci`, `finishing`, plus
`finish_failed` while draining) is running. If `deployment/symphony` has zero
ready replicas while scaled up, the updater rolls forward even when `/idle` is
unreachable. The Symphony pod keeps `automountServiceAccountToken: false` and
has no Kubernetes API egress.

Each image bakes its source commit as `SYMPHONY_COMMIT`. Symphony checks GHCR
every 60 seconds and offers **Update** in chat with a compare link and commit
summary when a newer `:main` digest exists. Clicking **Update** drains new
dispatches until active work finishes (**Cancel** restores dispatch
immediately; draining resets on restart). Browser tabs automatically reload
when a newer server `version` is detected once the composer and modals are idle.

Kubernetes NetworkPolicy enforces default-deny in the namespace, allowing the
Symphony pod only DNS, public TCP 443 egress, port 8081 ingress from
`symphony-updater` and Guaranine Prometheus (`monitoring` namespace, scraping
`GET /metrics` in Prometheus 0.0.4 format including
`symphony_worker_escalations_total{repo,trigger}`,
`symphony_coordinator_rate_limit_remaining_percent{window}`, and explicit
`symphony_metric_available` indicators), and port 8082 ingress from
`symphony-webhook`. Linear webhooks verify `Linear-Signature` (HMAC-SHA256) and
a 60-second timestamp window before waking the refresh loop.
[Deferred work](symphony-follow-ups.md) tracks ntfy alerts and hostname-level
egress.
