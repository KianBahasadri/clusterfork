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

## Retained Azure monitoring

[Azure infrastructure](azure-infrastructure.md) owns the existing Function App's
Application Insights/Log Analytics resources and retention/cap configuration.
Those resources retain historical records under their existing retention limits.
The Function App relay is retired; the VM journal is the current log destination.
Legacy tables were privately exported before runtime-storage removal. Current
task ownership and execution evidence come from SQLite and actual GitHub observations.
