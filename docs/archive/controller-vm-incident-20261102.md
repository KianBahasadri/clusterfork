# Controller VM incident, 2026-11-02–03

This is a dated incident record. Recheck current health before taking action;
the Azure resource name was reused for a replacement VM. Commands and operation
receipts for the old VM must not be replayed against the replacement.

## Impact and observed failure

The controller continued serving HTTPS from revision
`5c8dd78ad458177ade7fc88697b270e34a332a25`, but Symphony could not admit new tasks.
`deployment:required-runtime` correctly held admission because newer runtime
inputs had not deployed. Observations showed zero active tasks; working HTTPS
did not establish that deployment or Azure VM management worked.

CI passed for the blocked runtime, including
`a95743ee438c32166fbff8dd35ec3d5394ef99e8`. Deployments failed at their first Azure
host operation with `Conflict: Run command extension execution is in progress`,
before pause/drain, Terraform or installation. Failed deployment receipts include
[41538878161](https://github.com/KianBahasadri/clusterfork/actions/runs/41538878161),
[41539938651](https://github.com/KianBahasadri/clusterfork/actions/runs/41539938651),
[41540239072](https://github.com/KianBahasadri/clusterfork/actions/runs/41540239072)
and [41540676031](https://github.com/KianBahasadri/clusterfork/actions/runs/41540676031).

An interactive `tailscale up` client was confirmed waiting inside an Azure
Run Command launched during host maintenance. The verified process chain was
`653654 -> 653653 -> 653652 -> 653645`, ending at `run-command-ext`. Terminating
the waiting shell and client did not restore the VM management channel. Azure
continued reporting `Updating`, no `PowerState`, and later `VMStartTimedOut`,
while the guest agent heartbeat and application remained available.

The hung client explains the occupied command channel. The precise Azure-side
reason that later VM operations remained stuck was not established; guest
heartbeat and HTTPS alone could not distinguish agent, host and control-plane
failures. Azure Support's suggested cause was not a confirmed platform diagnosis.

## Timeline (UTC)

| Time | Evidence or action | Result |
| --- | --- | --- |
| Nov 2, 21:29:11 | Azure accepted the host-maintenance Run Command. | The interactive Tailscale login remained waiting. |
| Nov 2, 22:45–22:53 | Repeated deployment failures despite passing CI. | Azure's busy action channel blocked the first host request. |
| Nov 2, 22:58:23 | Earlier restart/reapply/VM write attempts reached `VMStartTimedOut`. | The guest continued serving its old runtime. |
| Nov 2, 23:00:47 | One documented `RemoveRunCommandLinuxExtension` action was accepted. | No recovery was established; terminating its local waiter did not cancel the remote operation. |
| Nov 2, 23:02:57 | A bounded managed Run Command inspected the guest independently. | Controller, Caddy and `walinuxagent` were active; guest uptime exceeded three days. The stuck process ancestry was verified. |
| Nov 2, 23:04:44 | Sent `SIGTERM` to waiting shell `653653`, then client `653654`. | The old script was prevented from continuing. `tailscaled` and the controller were left running. Later guest commands could not independently confirm process state. |
| Nov 2, 23:11:18 | One Azure host redeploy was accepted. | The observation window ended without a completed recovery; VM/disk identities remained unchanged. |
| Nov 2, 23:20–23:46 | Independent extension, SSH and Serial Console recovery attempts. | None established usable guest access; details below. |
| Nov 2, 23:32–23:33 | Protected snapshots of task and OS disks completed. | Recovery copies existed independently of VM management. |
| Nov 2, 23:34:20 | One supported `powerOff(skipShutdown)` request was accepted. | It remained pending during observation. Force-deallocate had been rejected as unsupported for this VM. |
| Nov 2, 23:44 | Submitted Azure Support case `2611020040009877` with explicit user authorization. | Case opened; no credentials, database or raw guest logs were sent. |
| Nov 2, 23:46 | Started a bounded, non-LLM recovery watcher. | At most two hours of observation, with durable guards against repeated start/deploy actions. |
| Nov 2, 23:51:33 | Azure Support sent its first response. | AI-generated recovery guidance, described below; no platform-side repair was confirmed. |
| Nov 3, 01:46:30 | Recovery watcher expired. | Management had not recovered; it sent neither VM start nor deployment retry. It stopped. |
| Nov 3, 03:12:45 | A later VM start request failed with `VMStartTimedOut`. | Subsequent checks still found no usable power state. |
| Nov 3, 12:32 | Read-only status check. | Old runtime still reachable, admission held, zero active tasks; support case open. |
| Nov 3, 16:19:21 | After explicit authorization to remove the old VM, created a fresh protected task-disk snapshot and saved private Terraform state. | Current task data had a recovery copy before deletion. |
| Nov 3, 16:20:10 | Submitted one ordinary Azure Compute VM `DELETE`. | Azure accepted it. No force-deletion request was necessary. |
| Nov 3, 16:20:35 | Checked VM, NIC, task disk and old endpoint. | VM returned `404 ResourceNotFound`, NIC had no VM association, task disk was unattached, and HTTPS no longer served. |
| Nov 3, 16:22–16:23 | Applied a narrowly reviewed Terraform recovery plan. | Fresh VM running, provisioning succeeded, guest agent ready; retained task disk attached and new identity grants created. |
| Nov 3, 16:24–16:26 | Checked retained database and service ownership. | Integrity/foreign keys passed; schema 12 and 56 task records retained. Original numeric service UID/GID preserved. Old detached OS disk deleted. |
| Nov 3, 16:26–16:35 | Initialized the replacement from CI-passed main `53032d14ceb6a208ea94282d9cf3684d0571af89`. | Exact HTTPS runtime and private release checks passed; task admission stayed held. |
| Nov 3, 16:35–16:40 | Reran deployment `41649331109` once, as attempt 2. | All workflow steps passed, including telemetry/workbook verification. |
| Nov 3, 16:42 | Final acceptance and one resume/reconcile operation. | No admission holds, `deployment_paused=false`, one healthy controller; final Terraform plan had no changes. |

## Recovery attempts that did not restore the old VM

- Temporary managed Run Commands initially supplied diagnostics and the targeted
  process-stop receipt. Later commands remained pending. Deletion was requested
  for the five diagnostic/recovery resources; they were not recreated in a loop.
- The separate `clusterfork-agent-recovery` CustomScript extension remained
  creating without an execution status. Its deletion was requested.
- A temporary NSG rule allowed SSH from only the operator's public `/32`.
  The local key matched the VM's configured public key, but SSH timed out. The
  rule was removed in cleanup; public SSH was not left exposed.
- Serial Console required enabling boot diagnostics temporarily. Both CLI and
  direct connection attempts timed out. Boot diagnostics was restored to its
  previous disabled setting. No SysRq restart was sent.
- Restart/reapply, host redeploy and power-off attempts failed or remained
  unresolved during their observation windows. A local CLI timeout was never
  treated as proof that the corresponding Azure operation had been canceled.
- `force-deallocate` was rejected for the non-resilient VM and did not execute.
  **Force deletion was not tried:** ordinary deletion later succeeded.

These attempts did not demonstrate disk corruption. No replacement controller
was enabled alongside the old VM. After the user requested stopping recovery
activity, checks found no active Clusterfork agent jobs or deployment workflows;
the expired watcher stayed stopped.

## Support response and steps that could not be completed

Case `2611020040009877` was last observed open; this recovery did not request
closure. Its response
identified the interactive Run Command as the likely cause and advised avoiding
additional VM writes, using SSH/Serial Console, stopping the exact waiting
client, restarting `walinuxagent`, and checking reconciliation after 10–15
minutes. It also acknowledged that Azure might need to forcibly cancel a stuck
operation. The email identified itself as AI-generated.

The client-stop action had already been sent. **The full response procedure was
not completed:** SSH and Serial Console were unavailable, so the agent could not
be restarted and the requested fresh guest logs could not be collected. The
later conditional kernel/package checks and upgrades were not reached. No
additional reply or platform-side cancellation was recorded. Recovery ultimately
used the user's later authorization to delete and rebuild the host.

## Successful replacement and retained state

The ordinary deletion used Compute API `2024-11-01`. Its request ID was
`c49f37bf-00f8-41bd-9fa3-ea46ffe42a9a`; correlation ID was
`d6155c34-cf5e-48c9-90c5-7a71d43a194c`. Absence of the old VM and detachment of its
resources were established before provisioning the replacement.

The reviewed saved Terraform plan created exactly the VM and task-disk
attachment, and replaced exactly its two Key Vault/telemetry identity grants.
The routine no-delete/no-replace gate was not weakened. The task disk, NIC,
static public IP, DNS and Linear webhook URL were retained. The old VM and OS
disk were removed; the final inventory contained one controller VM, one NIC,
one new OS disk and the original task disk.

The replacement retained resource name `clusterfork-controller-7604cac329` in
`rg-clusterfork-automation`, `mexicocentral`. Its new VM instance ID was
`7140c815-0424-4b45-a1c8-76d8da06e680`. The database's service UID `997` and GID
`987` were created before installation to preserve access to private files.
The existing database and account state were reused directly, without restoring
the previous night's snapshot or resetting tasks/accounts.

Bootstrap used a clean checkout of the tested commit and bounded deployment
transport. Its configuration was bound to the failed deployment run that would
be rerun, preventing required-runtime admission from clearing prematurely.
`operator:vm-rebuild` then held admission through the full workflow rerun and
final private checks. Only that recovery hold was explicitly released.

## Verification and limits

- [Deployment `41649331109`, attempt 2](https://github.com/KianBahasadri/clusterfork/actions/runs/41649331109)
  completed successfully, including controller deployment and telemetry/workbook
  smoke checks. Its exact revision had passed CI.
- HTTPS served revision `53032d14ceb6a208ea94282d9cf3684d0571af89` with the expected
  runtime digest, readiness protocol and registry digest.
- Both enrolled repositories, `KianBahasadri/clusterfork` and
  `KianBahasadri/conky-linear-HUP`, passed live read-only evidence for App access,
  immutable runtime tag, active central workflow and exact workflow source.
- Private `deployment-status` reported the expected release and `drained=true`;
  `verify-release` passed. Final public health had no holds,
  `deployment_paused=false` and zero active tasks.
- Azure reported provisioning succeeded, `PowerState/running` and guest agent
  ready. The final Terraform plan proposed no changes.
- No synthetic Linear event or model task was launched. This acceptance did not
  establish a new end-to-end coding-task completion or observe a fresh native
  webhook delivery. The existing webhook URL and secret references were retained.

## Backups, cleanup and prevention

Protected snapshots retained in the automation resource group:

- `clusterfork-recovery-os-20261102`
- `clusterfork-recovery-tasks-20261102`
- `clusterfork-recovery-tasks-predelete-20261103`

Snapshots have export denied and public network access disabled. Private incident
receipts and the pre-deletion Terraform state backup remain under
`~/.local/state/clusterfork/vm-rebuild-20261103/`; earlier diagnostics are in
`~/.local/state/clusterfork/deploy-incident-20261102.md`. Credentials, database
content, signed operation-polling URLs and raw Terraform plans are not committed.
The temporary checkout and reviewed plan files were removed. The old deletion
helper was retired so its unused force-delete path could not target the new VM
under the reused resource name. No recovery watcher or retry process was left
running.

The accompanying deployment change adds independent guest/client deadlines,
operator socket timeouts and a redacted, explicit busy error without automatic
resubmission. Its tests cover actual client and guest timeout behavior, one-shot
busy rejection, output redaction and successful output handling.
[Deployment documentation](../remote-development-deployments.md) owns the current
timeout policy and recovery rules. Do not use interactive login flows inside
unattended host commands, infer management health from HTTPS alone, or replay
historical repair commands against a replacement host.
