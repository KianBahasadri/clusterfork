# Remote development diagnostics

The GitHub worker and Azure broker emit small structured operational records.
GitHub Actions retains the worker's records and a readable failure summary;
Application Insights retains the Azure records and function request durations.
Linear's existing lifecycle comment displays the saved failure explanation.
This does not change dispatch, admission, retries, or the paused repository slot.

## Following one run

`automation/azure/diagnostics.py` owns the shared format. Records begin with
`Clusterfork: ` followed by JSON. Operations record their start, completion,
elapsed milliseconds, and safe failure classification. Normal heartbeats,
credential saves, instruction polls, and low-level worker commands use Debug;
their failures remain visible at Error. External Azure HTTP calls identify the
Linear GraphQL operation or GitHub endpoint category.

Worker calls carry a generated `request_id` in their broker payload. Azure
validates its shape and associates it with the cryptographically verified
GitHub `run_id` and `run_attempt`, plus the admitted `dispatch_key`. Search the
same run and request IDs in GitHub and Azure to distinguish a request that never
arrived from one that reached Azure and finished slowly. Context is scoped to
the invocation so unrelated requests cannot inherit an earlier run's IDs.

The worker records attempted stage changes before sending them, so a timeout
while entering publication is attributed to publication. Each configured
validation command gets a numbered operation such as `validation/1`; failures
retain its exit code and duration. Azure health reports `diagnostics_version: 1`
and whether its Application Insights connection is configured.

## Failure explanations

The worker's failed finish callback sends allowlisted diagnostic fields. Azure
validates and persists them in the attempt's existing worker or readiness record,
overriding run identity from verified claims. Linear reconciliation can replay
the explanation after a temporary tracker failure. A typical explanation is:

> Failed during publication: request timed out (broker/github); after 20s, timeout 20s.

GitHub's failure summary remains available if Azure cannot receive the callback.
An older runner, cancellation, or abrupt runner loss without a diagnostic retains
the existing generic explanation. These changes cannot recover details already
discarded by older runs. No extra Linear discussion messages are created for each
request; the owned lifecycle and attempt comments retain their existing identities.

Account-release failures are logged separately when another exception is already
being handled, preserving the original failure. A release failure on an otherwise
successful execution still fails that execution.

Logs and durable failure fields exclude request/response bodies, headers, tokens,
credential files, full URLs, command arguments/output, agent transcripts, exception
messages, source excerpts, and local variables. Safe stack locations contain only
repository Python basenames and line numbers. Unknown failure fields are dropped;
operation names and classifications must belong to the shared allowlist. Raw
subprocess output continues to be withheld from public Actions logs.

## Azure monitoring setup

Provision or repair the monitoring resources using the authenticated operator's
Azure CLI session; this does not require `.env` credentials:

```bash
python3 automation/pilot.py monitor
```

`automation/monitoring.json` creates an Application Insights component named
`<function-app>-insights`, backed by a dedicated Log Analytics workspace named
`<function-app>-logs`. Both use 30-day retention. The workspace has a 0.1 GB daily
ingestion cap. The helper attaches only `APPLICATIONINSIGHTS_CONNECTION_STRING`
to the existing Function App, preserving other settings and keeping the connection
string out of command arguments and output. Local provisioning via `pilot.py deploy`
also configures these resources; routine source deployment preserves the setting.

`host.json` enables function request results and operational Information logs.
Requests, exceptions, and traces are excluded from adaptive sampling so a failure's
diagnostic trail is retained. Other telemetry is sampled at five items/second per
host. Debug, live metrics, performance counters, and extended HTTP trigger details
are disabled. Volume control comes primarily from emitting bounded records and
keeping repetitive worker operations at Debug.

The daily cap is a backup against ingestion spikes, not a strict monetary budget:
Azure can overshoot it, and collection stops until reset if it is reached. Normal
Azure monitoring charges and allowances apply. The helper does not create email
notifications, a separate dashboard, or additional ingestion pipelines.

In the Application Insights Logs view, find a worker's records with:

```kusto
traces
| where timestamp > ago(1d)
| where message startswith "Clusterfork: "
| extend diagnostic = parse_json(substring(message, 13))
| where tostring(diagnostic.run_id) == "REPLACE_WITH_GITHUB_RUN_ID"
| project timestamp, diagnostic
| order by timestamp asc
```

The Requests view provides the Azure function's full invocation duration. The
`request_id` in operational records separates individual broker calls within a run.
Validate ingestion with a harmless health request; no coding worker needs to start.

References: [Azure Functions monitoring](https://learn.microsoft.com/azure/azure-functions/functions-monitoring),
[host logging settings](https://learn.microsoft.com/azure/azure-functions/functions-host-json#applicationinsights),
[workspace daily caps](https://learn.microsoft.com/azure/azure-monitor/logs/daily-cap).
