# Symphony

Symphony runs Codex as one coordinator across Linear projects. Each project
needs a GitHub repository URL in its description or external links. Repositories
are cloned on first use. Projects that link to the same repository share its
worktree queue.

## Plan and execute

Open Symphony on the tailnet and sign in to Codex with the device flow if
prompted. Choose a repo tab, expand an issue's proposed plan, and refine it with
the coordinator. Approve the plans you want in chat, including plans across
repositories. The coordinator records each approval, locks the current Linear
plan, and moves the issue to In Progress before dispatch.
Completed, Canceled, and Duplicate are terminal states across issue lists,
approvals, and state transitions.

Symphony refreshes a shared cache of every mapped repository's open issues
every 30 seconds using a batched, paginated Linear query. The browser receives
the lists through its regular status requests when they change. Switching tabs
uses the cache and restores the sidebar scroll position and expanded plans.
All repositories combines the cached lists. Issues appear in collapsible status
groups in Linear's workflow order. Each group sorts from Urgent to Low priority,
with unprioritized issues last. Groups start expanded and remember their
collapsed state separately for each view.
Status groups open and close with a visible spring using the standard Web
Animations API. Rapid clicks reverse from the current height; reduced motion
disables the animation.
Refresh failures keep the last successful lists and slow retries. Project
chips use their Linear names and emoji. Issue cards show the identifier,
workflow icon and title, with compact pills for priority, due date, estimate,
and colored labels. Workflow icons match Linear's dotted, empty, half-filled,
and filled status shapes, using the configured colors with stronger contrast
in light mode. All repositories also shows each issue's project chip.
Project names appear in the chips above the list. Worker phases and proposed
plans appear on their issues.

Click an issue card to open its details in a modal. The description, attachments,
documents, comments, and activity appear beside its properties and related issues.
Related issue links open their context in the same modal. Details preload in the
background with at most two preloads running at once, prioritizing the selected
repository. Requests share an in-page cache and use versioned URLs based on
Linear's `updatedAt`, so the browser's private HTTP cache reuses unchanged details
across reloads. A changed issue preloads its new version without refetching the
sidebar. Mistune renders Markdown with raw HTML escaped.
Linear uploads stream through Symphony using its existing Linear credentials,
including private images and range requests. The key stays on the server.
Close the modal with Close, Escape, or the backdrop to return focus to its card.
The app fills the viewport without a branding or connection header. Connection
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
selected repo's working directory and includes its AGENTS.md and current work
when starting a turn. Codex uses its shell and live web search inside the
restricted pod. It can inspect checkouts and worker logs and use `gh` to diagnose
CI. OpenCode workers handle implementation by default.

OpenCode uses the Go subscription and caps concurrent workers at three. Each
attempt has a one-hour safety timeout. The checklist is a shell command file
that Symphony runs a second time, in the worktree, to get its own exit status.
The worker's notes and log live in `runs/<issue-id>/` on the PVC. The worktree
stays reserved through audit, rework, and CI. The coordinator reviews notes
before shipping. Worker errors, rejected pushes, and failed CI appear in chat
and the issue sidebar with the worktree preserved. OpenCode Go quota,
rate-limit, and authentication failures pause new dispatches. Use
`/retry KIAN-123` to revisit a parked issue and `/continue` when the provider
failure has been resolved.

Symphony writes one squash commit and pushes directly to `main`. It waits for
the exact revision's GitHub Actions workflows whose push filters match the
branch and changed paths. If none match, Symphony advances the repo after the
push. Keep deployment jobs dependent on successful CI in the repository's
workflow. After verified publication, Symphony sets Linear to Done and removes
the worktree. A restart preserves the conversation, approved plans, worker
sessions and pending CI watches. When the coordinator's tool definitions
change, Symphony starts a fresh Codex thread and records the change in the chat
history.

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
kubectl -n symphony rollout status deployment/symphony
kubectl -n symphony exec deployment/symphony -c tailscale -- \
  tailscale --socket=/tmp/tailscaled.sock serve status
```

Open the HTTPS address reported by Tailscale and verify the repo tabs load.
Kubernetes NetworkPolicy allows DNS and public TCP 443 egress. The gateway tag
and Serve configuration control access to the UI. [Deferred work](symphony-follow-ups.md)
tracks ntfy, additional composer commands, and hostname-level egress.
