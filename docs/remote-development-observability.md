# Remote development diagnostics

GitHub Actions retains worker records and summaries. The controller writes bounded
operational records to its systemd journal, and its private SQLite database keeps
task/attempt/review evidence. Linear presents committed task decisions and saved
failure explanations. Diagnostics never advance lifecycle or authorize retries.
The previous coordinator's instrumentation is a [dated record](archive/remote-development-functions-observability.md).

## Following one attempt

Start with the run and PR links in the task's Linear status comment. The combined
GitHub workflow has one review job and one coding job;
post-job cleanup is part of each job's result. Task success, worker success and
PR merge are distinct observations.

On the authorized controller host:

```bash
sudo clusterfork-controller deployment-status
sudo clusterfork-controller inspect --task-id TASK_ID
sudo journalctl -u clusterfork-controller --since '1 hour ago'
```

The operator socket is private. Inspection includes task state, admitted context,
attempt identity, sessions and publication evidence. Private failure output can
be present in these records; publish only the bounded explanation and links.

`automation/common/diagnostics.py` retains the shared `Clusterfork: ` JSON format.
Worker requests carry a generated request ID. The HTTP adapter scopes logging to
verified GitHub run/attempt claims and a validated dispatch/request identity;
client-provided run IDs cannot replace the authenticated ones. Context is cleared
after the request. Normal heartbeat, save and instruction polling use quiet/Debug
operations; failures retain safe classifications. Azure SDK logging is Warning.
Raw request paths/headers are not emitted by the HTTP handler.

Background observation errors include the affected task prefix and error class.
Saved worker failure identities are bound to verified run claims. GitHub's summary
remains available when an unavailable controller cannot receive a finish callback.

## Review and failure evidence

The SQLite task specification binds content, policy, target context, images and
runtime revision. Each attempt retains its verified GitHub run/attempt and
structured review result. Review feedback describes readiness, not completed tests.
Clarification and rejection retain the missing decisions and resubmission guidance.
Each review has its own stable comment, so later attempts cannot overwrite that
feedback. A confirmed rejection moves the issue to its attention state; moving it
back to Todo authorizes another review. Failed status reporting cannot repeatedly
resubmit the same rejected review.

Worker diagnostic fields are allowlisted and validated before being stored in the
coding session. Linear status and usage/repair comments use the safe failure
summary. An abrupt or canceled worker with no callback retains a generic reason;
discarded details and missing usage/time measurements are not invented.

Failed validation and Git/gh commands can retain up to 16,000 UTF-8 bytes of
scrubbed private failure output. It stays nested in the session's failure data and
may enter the original task's authorized repair payload. WIP branch/SHA evidence
is separately validated. Neither private output nor agent transcripts are copied
to operational logs, public Actions output, summaries or Linear repair reports.

The publication guard refreshes known authentication caches and removes tracked
credentials before retaining evidence; shared redaction also removes static
credentials, private keys, authorization headers and credential-bearing URLs.
Unreadable caches withhold evidence. Codex/OIDC operations cannot attach failure
output. Account release failures preserve the original failure where one already
exists; release failure after otherwise successful work still fails that worker.

## Controller telemetry and workbook

The **Clusterfork controller** Azure Monitor workbook is in
`rg-clusterfork-automation`, attached to the retained
`clusterfork-dispatch-7604cac329-logs` workspace. Open it under the workspace's
**Workbooks**, or use `terraform -chdir=infra/azure output -json controller_monitoring`
for its resource ID and portal link. Viewing requires workbook/resource read and
workspace query access through the authorized operator identity. Infrastructure,
permissions, Azure retention and ingestion quota are owned by
[Azure infrastructure](azure-infrastructure.md#stacks-and-ownership).

The current source is `automation/controller/telemetry.py`, running in a separate
thread of the systemd controller. It sends selected records through the Logs
Ingestion API to `ClusterforkController_CL` in that workspace using the VM's
system-assigned managed identity. Application Insights retains historical
Function App records; the retired coordinator and relay produce no new traffic.
The journal remains the controller's operational log, SQLite remains its private
evidence store, and GitHub Actions retains detailed worker logs. Neither SQLite
files nor the raw journal are uploaded. Legacy tables were privately exported
before runtime-storage removal.

The workbook's coverage panel displays **TelemetryStarted** from the first
enabled startup, persisted in the controller database and carried on every event.
It also displays the last received record and cumulative dropped/omitted count.
There is no historical backfill. A run that predates that date, or a gap caused
by an outage, queue expiry, quota or retention, may have no telemetry. Absence
does not establish success or failure. The historical Function App panel is
explicitly separate from current controller HTTP charts.

Choose the time range for recent review/coding starts and results, GitHub stop
observations, publication observations, and the latest committed task decisions
in that range. Each available run link opens its exact GitHub Actions attempt.
`phase_preflight_started` marks account selection; `phase_started` marks agent
execution. A structured review verdict or finish callback produces `phase_result`;
it does not prove that GitHub cleanup stopped. `github_stopped` requires an actual
committed GitHub conclusion. `publication_observed` records checked PR state.
`task_outcome` records the controller's decision after complete stop/publication
evidence; `TaskFinal` distinguishes completed/canceled tasks from held or retry
decisions. A merged PR can coexist with a failed phase/cleanup observation.
Missing callbacks never become invented agent outcomes.

Enter a numeric **GitHub run ID** and **GitHub attempt** (or `*`) above the
chronological view. These accept old IDs even when no records exist; the selection
coverage panel explains missing telemetry. The timeline includes phases/stages,
validated request IDs and failure request IDs, safe classifications, and Actions
links. Stage records show the first committed visit to each stage; repeated polls
and later revisits do not add stage rows. Narrow the range when its first 1,000
rows are insufficient. Recent tables
show at most 200 rows. Detailed runner output is available through Actions.

HTTP charts replace the retired Function App request view. They count controller
requests by finite operation and status class, including all successful heartbeat,
save, instruction and health polls. Failures are HTTP 4xx/5xx. Latency is a
count-weighted mean and maximum, with each request capped at 120 seconds.
Measurements cover requests reaching the Python transport, including its capacity
rejections; Caddy/TLS/network failures before that point are outside this stream.
Unsupported operations use a single unknown classification. One-minute aggregate
records preserve volume without exporting one event per successful poll. Request
details are sampled separately, so aggregates and timeline rows have different
counts. HTTP retries count as actual requests, independently of lifecycle deduplication.

## Delivery, identity and privacy boundaries

Lifecycle projections share the transaction that saves their controller evidence.
An isolated savepoint allows projection failure without rejecting the command;
outer rollback removes both evidence and its projection. Stable SHA-256 event IDs
bind schema version, controller attempt, event type, phase/stage, outcome and retry
decision. A fixed set of receipts stays with each attempt's existing evidence;
repeated callbacks/reconciliation cannot recreate delivered or expired records.
New attempts have new identities. The evidence sequence preserves committed order
when timestamps tie. Queries deduplicate delivery by `EventId`; task views choose
the latest committed decision by evidence sequence. Restart/ambiguous delivery can
resend the same record, without inflating counts.

The exporter performs token acquisition and HTTPS outside every database
transaction and owner lock. Requests only update bounded memory measurements.
Delivery failure neither dispatches work nor changes task state, retry policy or
worker responses. The queue has at most 1,024 records, each at most 4 KiB, expiring
after six hours. At most 64 records/256 KiB are attempted once per minute, with
five attempts per record and backoff capped at 15 minutes. A persistent UTC daily
budget limits attempted payload bytes, including retries, to 16 MiB across
restarts, below the existing workspace quota. Delivery is best effort; overflow,
expiry and exhausted retries discard telemetry. Receipts prevent repeated
reconciliation from refilling it. HTTP memory has at most 256 aggregate entries
and 32 detail samples; detail admission is also limited to 32 per minute. Quiet
successful polls never receive detail samples. Heartbeats report cumulative
dropped/omitted telemetry every five minutes. An abrupt process stop can lose
unflushed HTTP measurements; committed queued lifecycle records survive restart.

Exports reuse `common/diagnostics.py` validation and authenticated correlation.
Worker run/attempt identity comes from verified GitHub claims or committed
controller bindings, never client-provided run IDs. HTTP correlation is attached
only after the broker verifies the workflow, phase and dispatch/run binding.
Failure classifications use
the diagnostic allowlist, including known operation/kind/type/reason/status and
validated request IDs. Credentials, bodies, raw paths/headers, exception messages,
review text, agent transcripts, raw command output and private failure evidence
are excluded. Unauthenticated request failures have no client-asserted run or
request identity. Do not use Azure telemetry as lifecycle authority or as a
replacement for private inspection.

## Validation and post-deployment smoke check

Local checks require no Azure credentials:

```bash
python3 -m unittest discover -s tests -p 'test_controller*.py'
dotnet restore tests/monitoring/MonitoringQueries.csproj --locked-mode
dotnet run --project tests/monitoring/MonitoringQueries.csproj --configuration Release --no-restore
terraform fmt -check -recursive infra
terraform -chdir=infra/azure init -backend=false -lockfile=readonly
terraform -chdir=infra/azure validate
```

Python tests exercise real committed SQLite evidence, rollback, deduplication,
retries/restarts, bounded delivery, authenticated HTTP correlation and exporter
failure isolation. The .NET 8 check uses Microsoft's pinned Kusto language library
to parse and bind the rendered workbook queries to the ingestion schema, including
wildcard/exact selections. Both checks run in CI.

The existing main-branch deployment workflow reconciles Terraform and installs
the configured exporter, then runs `automation/controller_monitoring.py` through
its existing `azure-production` identity. This non-gating step sends harmless
HTTPS health/unknown-operation requests, waits up to ten minutes for the deployed
revision's startup/heartbeat plus successful/failed HTTP aggregates, and executes
every query from the **saved Azure workbook**, including the coverage and HTTP
panels. It verifies queryability/display data; phase tables can be empty before
the first new task. Inspect that step's result even when runtime deployment passes.
Ingestion/RBAC propagation or a reached quota can make the smoke check fail without
failing rollout or worker operations. No coding runner receives Azure credentials.

An operator can repeat it from an initialized Terraform workspace with the
existing authorized Azure CLI login:

```bash
python3 automation/controller_monitoring.py --revision DEPLOYED_FULL_SHA
python3 automation/controller_monitoring.py --revision DEPLOYED_FULL_SHA --run-id GITHUB_RUN_ID --attempt 1
```

After a new ordinary task, open the workbook, choose its run/attempt and verify
review/coding records, separate GitHub stop/publication/task decisions, request IDs
when present, and the Actions links. For an older run, verify the explicit missing
coverage message. On failure, inspect the controller journal for bounded exporter
defer messages, check the DCR-scoped VM grant and workspace cap through the existing
operator path, and rerun the smoke check. Do not enable raw journal/SQLite ingestion.
