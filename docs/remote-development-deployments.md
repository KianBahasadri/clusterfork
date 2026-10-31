# Controller deployment

This topic owns rollout, ingress and holds. [Controller](remote-development-controller.md)
owns SQLite/transports; [infrastructure](azure-infrastructure.md) owns host/resources.
[Functions cutover](archive/remote-development-functions-deployments.md) is historical.

## Required runtime and project freshness

`controller/release.py` identifies committed `.github`, `automation`, `infra` and
`scripts` trees. The manifest records full revision/digest, excluding worktree
files/credentials. Admission compares current main even after missed notifications.
Target-only commits need no rollout: the next task resolves/reviews the current
target branch. Runtime changes hold new attempts until tested runtime is active;
every job stays pinned to its admitted runtime without freezing target freshness.

## Automatic rollout

`.github/workflows/deploy-azure.yml` serializes runtime-input main pushes and manual
dispatches, using existing `azure-production` identity/backend. No application
secrets enter the repository/jobs. `automation/controller_deploy.py` waits for
exact-revision main CI and rechecks whether the runtime is still required. Newer
target-only commits may coexist; superseding runtime changes retain admission hold.

The private socket records `deployment:rollout` before drain. Required-runtime
detection independently holds admission before the workflow reaches that step.
Drain requires verified stop/publication and released leases; uncertainty, missing
observations, running attempts or open PRs block switching. Stopped retries may
retain grants. Network checks run outside transactions, preserving heartbeats.

Terraform applies the reviewed no-delete/no-replace plan; Run Command installs
the exact commit in a release directory after verifying VM identity and mounted
task disk. Source is root-owned; systemd runs an unprivileged user/private socket.
Reload the written unit, require successful stop and verify `inactive` before
changing the release symlink, including first install. Reuse the persistent database
with one mutation owner.

HTTPS health must match revision/digest. Private verification creates/verifies
exact commit tag `clusterfork-runtime-<full-sha>`. Automatic installs record their
GitHub deployment run ID; admission also requires that exact main revision's
whole deployment workflow, including cleanup/ingress, to succeed. Private tag/source
verification is independent so deployment need not admit work or wait on itself.
Only its rollout hold clears; operator holds survive restart/install/success.
CI, drain, plan, install and verification failures retain holds.

## Direct Linear webhook ingress

Linear signs deliveries directly to
`https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/linear`.
The controller verifies original signature/timestamp and commits the minimal inbox
event before acknowledging; repeats deduplicate, unavailability returns retryable
failure. No Function App relay remains. [Webhook administration](remote-development-setup.md#smallest-administrator-procedure-for-an-ingress-change)
owns URL changes, credentials and subscription/secret preservation.

## Operator commands

On the authorized host, root or the controller user uses the private local socket:

```bash
sudo clusterfork-controller deployment-status
sudo clusterfork-controller pause --owner operator:maintenance --reason 'Planned maintenance'
sudo clusterfork-controller resume --owner operator:maintenance
sudo clusterfork-controller reconcile
```

Releasing one named hold leaves other owners' holds intact. Public HTTP provides
no operator actions/arbitrary transitions. New databases start with an operator
hold; existing databases preserve holds. Routine rollout needs no manual resume.
[Migration](remote-development-controller.md#deployment-and-migration) is complete;
startup uses imported SQLite task/account/instruction state directly.
