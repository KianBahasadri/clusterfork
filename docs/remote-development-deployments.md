# Controller deployment

This topic owns runtime rollout and hold semantics. [Controller implementation](remote-development-controller.md)
owns state, transports, migration and dated live evidence. [Azure infrastructure](azure-infrastructure.md)
owns the VM, persistent disk, identity and Terraform resources. The retired
Functions cutover logic remains a [dated record](archive/remote-development-functions-deployments.md).

## Required runtime and project freshness

`controller/release.py` identifies runtime inputs by the committed Git trees for
`.github`, `automation`, `infra` and `scripts`. The deployed manifest contains the
full source revision and digest. It excludes local worktree files and credentials.
The controller checks current main against these trees before admission, including
when a merge notification was missed.

A target-only commit does not require a runtime rollout: the next task resolves
its current repository branch and reviews that checkout. An automation change
holds the next attempt until the tested required runtime is active. Every job in
one attempt stays pinned to its admitted immutable runtime; that pin never freezes
the target checkout at an old project commit.

## Automatic rollout

`.github/workflows/deploy-azure.yml` runs on main pushes changing runtime inputs
and supports manual dispatch. Deployments are serialized. It uses the existing
`azure-production` identity and Terraform backend; no new application secrets are
put in the repository or worker jobs.

`automation/controller_deploy.py` waits for exact-revision main CI and checks
whether the candidate's runtime inputs remain required. A newer target-only commit
can coexist with that runtime; a superseding runtime keeps admission held for its
new deployment. The controller's private operator socket records the distinct
`deployment:rollout` hold before draining. Required-runtime detection supplies an
independent hold before a workflow has reached that step.

Drain requires verified stop/publication facts for active attempts and released
account leases. Uncertain dispatch, missing observations, a running attempt or an
open PR prevents the switch. A stopped retry can retain its repository grant
through rollout. Network calls occur outside task transactions, so drain and CI
checks do not block credential heartbeats.

Terraform reconciles the reviewed no-delete/no-replace plan. Azure Run Command
then installs the exact commit into its own release directory. The installer
verifies VM identity and the dedicated mounted task disk. Source is root-owned;
the supervised service has a separate unprivileged user and private operator
socket. The written unit is reloaded, its stop must succeed, and its state must be
verified as `inactive` before the current-release symlink changes, including
on first installation.
Only one database mutation owner is permitted; the persistent database is reused.

HTTPS health must identify the expected revision/digest. Private verification
creates or verifies `clusterfork-runtime-<full-sha>` as an exact commit reference
before dispatch can resume. An automatic installation records its GitHub
deployment run ID. Admission also requires that exact main revision's deployment
workflow to complete successfully, including retained-ingress verification.
Private source/tag verification is independent of that completion gate, so the
deployment can finish without admitting work early or waiting on itself.
Only the rollout's hold is cleared. Independent
operator holds persist across installation, restart and successful deployment.
CI, drain, infrastructure, install or verification failures retain the holds.

## Existing Linear webhook URL

`automation/webhook_relay` is the retained Function App's stateless ingress package.
It forwards original signed request bytes to the controller and returns success
only after the controller accepts the event. Controller unavailability returns a
retryable failure; event identity deduplicates repeated delivery after acceptance.
The package contains only HTTP forwarding and metadata health functions, with no
queue consumer, timer, task database, worker RPC or account access.

The deployment workflow packages only its committed allowlisted files, publishes
them with the existing Functions action, and verifies the exact ingress revision.
Package entries are regular Unix files with mode `0644`, including deployment
metadata, so the unprivileged Functions host can read the mounted archive.
The legacy lifecycle trigger names remain disabled in Terraform. Keeping the
existing URL needs no Linear webhook administration. A webhook already pointed
directly at the controller also uses the same signed intake contract.

## Operator commands

On the authorized host, root or the controller user can inspect and control it
through the local socket:

```bash
sudo clusterfork-controller deployment-status
sudo clusterfork-controller pause --owner operator:maintenance --reason 'Planned maintenance'
sudo clusterfork-controller resume --owner operator:maintenance
sudo clusterfork-controller reconcile
```

Releasing one named hold does not release another. These are private host commands;
public HTTP exposes neither operator actions nor arbitrary state transitions.
The normal workflow handles routine code rollout without a manual resume step.

Initial cutover additionally requires an explicit operator halt, verified idle
legacy references, preserved credential storage, proven queue generations and a
private migration snapshot. It stops old lifecycle writers before starting the
new service. [Migration](remote-development-controller.md#deployment-and-migration)
owns that once-only import and its audit history.
