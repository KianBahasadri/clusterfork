# Deployment pauses and recovery

The [runtime overview](remote-development.md) covers readiness and worker execution.
This topic owns deployment draining, startup deferral, resume, and the KIAN-923 investigation.

## Verified deployments

`.github/workflows/deploy-azure.yml` runs on pushes to main and supports manual
recovery with `workflow_dispatch`. Every run reconciles the
[Azure Terraform stack](azure-infrastructure.md) before publishing the package.
Deployment runs are serialized, with later pushes retaining the newest pending
run. [Setup](remote-development-setup.md#automatic-deployment-identity) owns the
GitHub environment and Azure identity configuration.

Before deployment, `automation/deploy.py` writes a durable pause to
`ClusterforkOperations` (partition `control`). New readiness/development
dispatches stop, while existing assessments and coding sessions retain their
broker access and can finish. The workflow waits for the exact main revision's
`test` CI workflow to succeed and for all worker runs, including queued runs and
older manual reruns, to stop. Draining allows 330 minutes within a six-hour job.

Only the allowlisted Function App ZIP is published using Flex Consumption's remote
build. `deployment.json` stamps its Git revision and broker source digest.
The health endpoint returns this metadata, the serial-admission capability, and
the deployment pause state. Startup failures return 503 with a `runtime_error`
class, without credentials or raw exception messages. Dispatch resumes only after
the live revision/digest match the package, configuration is complete, and main
still points to that revision. If main advances, the newer deployment inherits
the pause. CI, drain, deployment, or health failures leave it in place; a corrected
main push or manual deployment workflow run performs recovery. The pause never expires.

## Startup deferral and resume

A readiness start or account preflight/handoff overlapping the pause records
`deployment_deferred`, `reason_code=deployment_paused`, and no retry deadline.
Its stable review comment explains the deployment wait. The runner exits
successfully without starting assessment/coding or reporting a failed reviewer,
allowing deployment to drain. Account cleanup remains available. Coding startup
also retains a durable deployment wait if it has not begun coding. Repository
admission loss, unresolved/unverifiable prerequisites, authorization errors, and
actual reviewer failures remain distinct. Ordinary infrastructure and quota
backoffs retain their existing 15-minute delay.

Verification queues a `DeploymentResume` event in the existing `clusterfork-events`
queue before explicitly resuming its matching owner/revision. It reuses the app's
existing storage connection; no authentication or permission changes are required.
A failed notification leaves dispatch paused. The consumer checks the current
deployment record, ignores superseded events, and holds work until explicit resume.
It reconciles deferred readiness and repository admission immediately, with delayed
15-second queue events while prior attempts remain active or undiscovered. No GitHub
runner waits for deployment, and recovery does not depend on the 15-minute timer.

Every matching GitHub run's latest attempt and any recorded owner must be stopped
before a replacement. Missing/incomplete observations hold work for discovery.
Retries retain the durable repository slot, recheck current eligibility/dependencies,
and bind a new review to current issue, policy, image, and repository revisions.
A changed binding also cannot bypass discovery of an ambiguous earlier dispatch.
Repeated events and reconciliation reuse durable ownership and dispatch receipts.

## Status-reporting investigation

KIAN-923 regression coverage confirms a secondary reporting defect: two handlers
can both observe a missing deterministic readiness comment; the losing
`commentCreate` rejection escaped after GitHub accepted dispatch and was mislabeled
as an infrastructure/context lookup failure. The exact 2026-10-29 GraphQL response
was not retained, so duplicate creation remains an incident hypothesis.

The shared adapter rereads the same deterministic IDs after a failed or ambiguous
create, accepting an existing matching body or updating that comment while
preserving replies and the KIAN-921 identity migration. Without confirmed existence
it retains the original bounded error classification and retries the same ID.
Readiness reporting errors log `readiness_reporting_deferred`; they cannot replace
accepted dispatch with a generic context-failure comment. GraphQL/recovery logs
exclude raw bodies, error messages, variables, headers, and credentials.
