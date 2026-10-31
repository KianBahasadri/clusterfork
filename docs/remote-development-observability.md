# Remote development diagnostics

The systemd journal holds bounded controller logs; private SQLite retains task/
attempt/review evidence, Actions retains worker output, and Linear shows decisions
and safe failures. Diagnostics never advance lifecycle or authorize retry.
[Retired instrumentation](archive/remote-development-functions-observability.md) is historical.

## Following one attempt

Follow the Linear status comment's run/PR links. Worker success, actual GitHub
stop (including post-job cleanup), PR merge and task completion are distinct.
On the authorized host:

```bash
sudo clusterfork-controller deployment-status
sudo clusterfork-controller inspect --task-id TASK_ID
sudo journalctl -u clusterfork-controller --since '1 hour ago'
```

Private inspection includes context, sessions and publication/failure evidence;
publish only bounded explanations/links. `common/diagnostics.py` emits
`Clusterfork: ` JSON. Requests generate validated IDs, scoped to verified GitHub
run/attempt/dispatch, never caller-asserted run IDs, and cleared after each request.
Heartbeat/save/instruction polls are quiet/Debug; Azure SDK logging is Warning.
Failures retain safe classes; background errors add task prefixes. The HTTP handler
omits raw paths/headers. Actions summaries survive missing controller callbacks.

## Review and failure evidence

SQLite binds content, policy, target, images and runtime to verified attempts and
structured review results. Reviews describe readiness, not completed tests. Stable
per-review comments preserve missing decisions/resubmission guidance; later attempts
cannot overwrite them. Rejection moves to attention; returning to Todo requests
another review. Reporting failures cannot repeatedly resubmit rejected reviews.

Allowlisted worker diagnostics enter the coding session; public comments use safe
summaries. Missing callbacks retain generic reasons without invented usage/time.
Validation/Git/gh failures may retain up to 16,000 UTF-8 bytes of scrubbed private
output for authorized repair; WIP branch/SHA is separately validated. Neither this
output nor transcripts enter operational logs, Actions output/summaries or Linear.
Publication guards refresh auth caches and redact known credentials; shared redaction
also removes static credentials, keys, auth headers and credential URLs. Unreadable
caches withhold evidence; Codex/OIDC operations cannot attach failure output.
Release failure preserves an earlier failure or fails otherwise successful work.

## Controller telemetry and workbook

Open **Clusterfork controller** under **Workbooks** in retained workspace
`clusterfork-dispatch-7604cac329-logs`, resource group `rg-clusterfork-automation`,
or retrieve its resource ID/portal link:

```bash
terraform -chdir=infra/azure output -json controller_monitoring
```

The authorized viewer needs workbook/resource read and workspace query access.
[Infrastructure](azure-infrastructure.md#stacks-and-ownership) owns grants, quota and
retention. A separate controller thread (`controller/telemetry.py`) sends selected
records through Logs Ingestion API to `ClusterforkController_CL` using VM managed
identity. SQLite/raw journal are never uploaded. Historical Function App Insights
has a separate panel; the retired services produce no new traffic.

Coverage shows database-persisted **TelemetryStarted** from first enabled startup,
last received record and cumulative dropped/omitted count. There is no backfill;
pre-start runs and outage/expiry/quota/retention gaps may lack data. Absence proves
neither success nor failure. Time-range views distinguish:

| Event | Meaning |
| --- | --- |
| `phase_preflight_started` | Account selection began |
| `phase_started` | Agent execution began |
| `phase_result` | Review verdict or finish callback; not GitHub stop proof |
| `github_stopped` | Actual committed GitHub conclusion |
| `publication_observed` | Checked PR state |
| `task_outcome` | Controller decision after stop/publication evidence; `TaskFinal` distinguishes completed/canceled from held/retry |

A merged PR may coexist with failed cleanup; missing callbacks invent no outcomes.
Select numeric **GitHub run ID** and **GitHub attempt** (or `*`) for a chronological
view with validated request/failure IDs, safe classes and exact Actions-attempt
links. Old IDs remain selectable; coverage explains missing data. Stages show only
first committed visits. Timeline caps at 1,000 rows (narrow the range); recent tables
cap at 200. Detailed output stays in Actions.

HTTP charts count actual controller requests, including successful polls/retries,
by finite operation/status class; failures are 4xx/5xx. Latency is count-weighted
mean/maximum with each request capped at 120 seconds. Python transport capacity
rejections count; preceding Caddy/TLS/network failures do not. Unsupported operations
share one unknown class. One-minute aggregates preserve volume; sampled details
have different counts. HTTP retries count independently of lifecycle deduplication.

## Delivery, identity and privacy boundaries

Lifecycle projections share the evidence transaction. A savepoint isolates projection
failure; outer rollback removes both. Stable SHA-256 IDs bind schema, controller
attempt, type, phase/stage, outcome and retry decision. Fixed evidence receipts
prevent reconciliation from recreating delivered/expired records. New attempts
have new identities; evidence sequence orders tied timestamps. Queries dedupe by
`EventId` and use sequence for latest decisions; ambiguous delivery may resend an ID.

Token acquisition/HTTPS run outside transactions and owner locks; requests only
update bounded memory. Delivery failure cannot change tasks, dispatch, retries or
worker responses. Best-effort limits are:

| Limit | Value |
| --- | --- |
| Durable queue | 1,024 records; 4 KiB each; six-hour expiry |
| Delivery batch | Once/minute; at most 64 records/256 KiB |
| Retry | Five attempts/record; backoff capped at 15 minutes |
| Persistent UTC daily attempted-byte budget, including retries | 16 MiB across restarts, below workspace quota |
| HTTP memory | 256 aggregate entries; 32 detail samples; at most 32 new details/minute |
| Dropped/omitted heartbeat | Every five minutes |

Overflow/expiry/exhausted retries discard telemetry; receipts prevent refill.
Quiet successful polls have no details. Abrupt stops may lose unflushed HTTP
measurements; committed lifecycle queues survive. Exports reuse diagnostic validation
and verified claims/committed bindings; HTTP correlation begins only after workflow,
phase and dispatch/run authorization. Allowlisted classes/IDs exclude credentials,
bodies, paths/headers, exception messages, review text, transcripts, raw commands
and private failure evidence. Unauthenticated failures have no caller-asserted
run/request identity. Telemetry is not lifecycle authority or private inspection.

## Validation and post-deployment smoke check

Local checks need no Azure credentials:

```bash
python3 -m unittest discover -s tests -p 'test_controller*.py'
dotnet restore tests/monitoring/MonitoringQueries.csproj --locked-mode
dotnet run --project tests/monitoring/MonitoringQueries.csproj --configuration Release --no-restore
terraform fmt -check -recursive infra
terraform -chdir=infra/azure init -backend=false -lockfile=readonly
terraform -chdir=infra/azure validate
```

Python covers committed evidence, rollback/deduplication, bounds, restart/retry,
authenticated correlation and failure isolation. .NET 8 binds rendered workbook
queries, including exact/wildcard selections, to schema with Microsoft's pinned
Kusto library. Both run in CI.

After installing Terraform/exporter configuration, deployment runs non-gating
`automation/controller_monitoring.py` with existing `azure-production` identity.
It sends harmless health/unknown-operation requests, waits up to ten minutes for
the deployed revision's startup/heartbeat and success/failure HTTP aggregates,
then executes every query from the **saved Azure workbook**. Phase tables may be
empty before a new task. Inspect this step even when rollout passes; ingestion/RBAC
propagation or quota can fail it without failing workers/deployment. Coding runners
receive no Azure credentials. Repeat from initialized Terraform with authorized CLI:

```bash
python3 automation/controller_monitoring.py --revision DEPLOYED_FULL_SHA
python3 automation/controller_monitoring.py --revision DEPLOYED_FULL_SHA --run-id GITHUB_RUN_ID --attempt 1
```

After an ordinary task, check phase, stop, publication and task decisions separately,
request IDs and Actions links; old runs should explain missing coverage. On failure,
inspect bounded exporter defer messages in the journal, DCR-scoped VM grant and
workspace cap, then rerun. Do not enable raw journal/SQLite ingestion.
