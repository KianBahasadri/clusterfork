# Retired KIAN-922 Cursor probe, 2026-10-30

This records the old once-only Functions validation path. Its workflow is now
disabled; the controller does not invoke it during rollout. Existing staging
metadata and credential storage were preserved.

## KIAN-922 post-deployment validation

The one-off KIAN-922 cloud record for Cursor `21kb60` uses a staged cloud state
until its integration probe passes. This historical probe is independent of
ordinary reservations and local saved-profile visibility; local recovery does
not alter that cloud state. The resumed, health-verified deployment event
automatically asks the App to dispatch `clusterfork-cursor-smoke.yml` on main,
bound to that broker/worker revision and digest. A durable once-only receipt in `ClusterforkOperations`'s
`validation` partition precedes dispatch. Repeated events, timer ticks, deployments,
lost dispatch responses and manual reruns cannot authorize another agent turn.

This workflow runs on a fresh hosted VM. Its signed main-workflow OIDC claims,
revision, App actor, run ID/attempt and receipt are verified before binding it to
the reserved Cursor identity. Its broker role can claim only that account, save
its refreshed login, and report validation. It cannot list the pool, claim another
identity, obtain checkout/publication tokens, or admit repository work. Azure
storage credentials remain broker-side; the coding VM cannot assume this role.

The probe rechecks authentication, live usage, eligibility and the exact profile,
then requests permission for one turn with a 120-second deadline. It verifies a
harmless file creation and a completed read-tool result in a disposable workspace.
There is no turn retry, repository publication, or spending-setting change. A
successful fenced release preserves the latest tokens before activating the staged
record for normal selection. Reclaim can also restore the latest staged cloud login
before validation; no stale credential upload is needed.

One stable KIAN-922 comment records the credential-free pending/pass/fail result,
revision, provider/account/profile, model/effort, actual usage when available, and
run link after discovery. Missing credentials, unknown usage, unavailable profiles,
setup failures, ambiguous dispatch and missing finish callbacks remain visible
failures or pending discovery. Reporting failures retain durable retry intent.
Unit tests and CI use synthetic credentials; they are separate evidence from this
post-deployment live result. Production Codex capacity is never altered by the probe.
