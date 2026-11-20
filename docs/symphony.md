# Symphony

Symphony runs Codex as one coordinator across Linear projects. Each project
needs a GitHub repository URL in its description or external links. Repositories
are cloned on first use. Projects that link to the same repository share its
worktree queue.

## Plan and execute

Open Symphony on the tailnet and sign in to Codex with the device flow if
prompted. Choose a repo tab, expand an issue's proposed plan, and refine it with
the coordinator. Send `Approve KIAN-123` in chat to lock the current Linear plan
and move the issue to In Progress. The coordinator sequences approved issues
across repositories in the same conversation.

The composer supports `/compact`, `/status`, `/model <id>`, `/effort <level>`, and
`/interrupt`. A message sent during a turn in the same repo steers the active
turn. A message for a different repo queues for the next turn. The app uses the
selected repo's working directory and includes its AGENTS.md and current work
when starting a turn.

OpenCode uses the Go subscription and caps concurrent workers at three. Each
attempt has a one-hour safety timeout. The checklist is a shell command file
that Symphony runs a second time, in the worktree, to get its own exit status.
The worker's notes and log live in `runs/<issue-id>/` on the PVC. The worktree
stays reserved through audit, rework, and CI. The coordinator reviews notes
before shipping. A rejected push or failed CI appears in chat and the issue
sidebar with its worktree preserved. Use `/retry KIAN-123` to revisit a parked
issue and `/continue` when a shared provider failure has been resolved.

Symphony writes one squash commit, pushes directly to `main`, and waits for the
exact revision's GitHub Actions runs before advancing that repo. No workflow
files means no CI wait. A repository with workflow files needs a workflow that
runs on every push to `main`. Keep any deployment jobs dependent on successful
CI in the repository's workflow. After verified publication, Symphony sets
Linear to Done and removes the worktree. A restart preserves the conversation,
approved plans, worker sessions and pending CI watches.

## Deploy on Guaranine

The `test` workflow publishes the image to GHCR from `main` after tests pass.
Deploy the full commit tag (or image digest) from the successful run. The
deployment uses a single restricted pod and an OpenEBS local PVC. Tailscale
Serve proxies the app's loopback listener over private HTTPS.

Create the namespace and provide these Kubernetes secrets:

| Secret | Required values |
| --- | --- |
| `symphony-api` | `LINEAR_API_KEY`, `GITHUB_TOKEN` with repository content and workflow write and Actions read access, and `OPENCODE_GO_API_KEY` |
| `symphony-ghcr` | Docker registry config with read access to the private GHCR package |
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
