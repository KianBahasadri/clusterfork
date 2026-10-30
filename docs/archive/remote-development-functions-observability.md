# Retired Functions diagnostics, 2026-10-30

This is a dated record of the replaced coordinator. Current diagnostics are
documented in [remote diagnostics](../remote-development-observability.md).

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

## Readiness comments and audit records

A passed Linear readiness comment says the issue is ready for development,
briefly explains that the requirements and repository context are sufficient,
and links to the GitHub review run. It explicitly places tests during development:
readiness is a read-only assessment, not evidence that tests or validation passed.
Clarifications keep the missing decisions, consequences, and questions; rejected
reviewer output keeps the specific parsing, schema, consistency, or binding error.
Both retain resubmission instructions. Temporary failures keep their reason and
retry guidance. Rejection notifications and per-attempt comment identities stay
the same, preserving earlier discussions and replies.

Revision fingerprints, the consulted policy/context inventory, and rejected output
snapshots stay out of normal Linear comments. They remain in the private Azure
Storage table `ClusterforkReadiness`, partition `readiness`:

- `binding` JSON retains `issue_revision`, `policy_version`, `context_sha`, and
  `images_sha`, with the same approval and invalidation checks.
- `result` JSON retains the redacted model summary, criteria and reasons,
  blockers, and `policy_references`, including for a reviewer-requested retry.
- `rejected_result` retains the bounded, credential-redacted rejected output;
  `reason` retains the acceptance error or temporary hold explanation.
- `RowKey`, `issue_id`, `identifier`, `project_id`, `repository`, `created_at`,
  and the verified GitHub `owner` (`run_id:run_attempt`) identify the review.
  Older attempt rows remain available after a new review.

To investigate from a Linear comment, open **Readiness run** and take the run ID
and attempt from its URL. In the broker storage account's **Storage browser →
Tables → ClusterforkReadiness**, filter by:

```text
PartitionKey eq 'readiness' and repository eq 'OWNER/REPO' and owner eq 'RUN_ID:RUN_ATTEMPT'
```

The Actions run name also ends with the dispatch key, which is the review row's
`RowKey`. If no run started, filter by partition, repository, and issue `identifier`
instead. Read these records using existing operator access; do not copy their
contents to public Actions logs, summaries, or Linear comments. The existing
shared credential redaction and output bounds still apply; this presentation
change adds no model output, issue content, or policy inventories to operational
logs or public artifacts.

## Failure explanations

The worker's failed finish callback sends allowlisted diagnostic fields. Azure
validates and persists them in the attempt's existing worker or readiness record,
overriding run identity from verified claims. Linear reconciliation can replay
the explanation after a temporary tracker failure. A typical explanation is:

> Failed during publication: request timed out (broker/github); after 20s, timeout 20s.

GitHub's failure summary remains available if Azure cannot receive the callback.
Known workflow-permission failures retain an allowlisted reason code and explain
the missing grant in Actions and Linear instead of reporting only `WorkerFailure`.
An older runner, cancellation, or abrupt runner loss without a diagnostic retains
the existing generic explanation. These changes cannot recover details already
discarded by older runs. No extra Linear discussion messages are created for each
request; the owned lifecycle and attempt comments retain their existing identities.

Account-release failures are logged separately when another exception is already
being handled, preserving the original failure. A release failure on an otherwise
successful execution still fails that execution.

Failed validation commands and Git/gh subprocesses retain the last **16,000 bytes**
of combined stdout/stderr as optional `failure_output` in the private
`ClusterforkWorkers.failure` JSON. UTF-8 boundary errors are ignored and NULs removed.
Before retaining text, the publication guard refreshes live auth caches and redacts
all tracked credentials; shared review redaction also removes static tokens, private
keys, authorization headers, secret assignments, and credential-bearing URLs. Azure
validates the byte limit and NUL-free string and applies shared redaction again.
Unreadable auth caches withhold output. Codex and OIDC operations never attach it.
The same private evidence can return to the active agent during correction. It is
excluded from structured logs, Actions stdout/stderr, step summaries, and Linear
comments, including automatic repair reports. WIP push identity is stored separately
on the worker row; [runtime recovery](../remote-development.md#retries-and-troubleshooting)
owns that publication behavior.

Clusterfork operational records otherwise exclude request/response bodies, headers,
tokens, credential files, full URLs, command arguments/output, agent transcripts, exception
messages, source excerpts, and local variables. Safe stack locations contain only
repository Python basenames and line numbers. Unknown failure fields are dropped;
operation names and classifications must belong to the shared allowlist. Raw
subprocess output continues to be withheld from public Actions logs.

## Azure monitoring setup

Monitoring is owned by the [Azure Terraform stack](../azure-infrastructure.md).
Provisioning, adoption, state, and normal deployment follow that topic. To inspect
or reconcile configuration:

```bash
python3 automation/infrastructure.py plan
python3 automation/infrastructure.py apply
```

Terraform declares an Application Insights component named
`<function-app>-insights`, backed by a dedicated Log Analytics workspace named
`<function-app>-logs`. Both use 30-day retention. The workspace has a 0.1 GB daily
ingestion cap. The Function App's monitoring connection is also declared in
Terraform and reconciled during deployment.

`host.json` enables function request results and operational Information logs.
Requests, exceptions, and traces are excluded from adaptive sampling so a failure's
diagnostic trail is retained. Other telemetry is sampled at five items/second per
host. Debug, live metrics, performance counters, and extended HTTP trigger details
are disabled. Volume control comes primarily from emitting bounded records and
keeping repetitive worker operations at Debug.
Python Azure SDK logs are restricted to Warning and above; their verbose HTTP
request/response records are not collected.

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
