# Controller acceptance, 2026-10-30

This is a dated live acceptance fixture, not a current runtime specification.

sequence-one: established

sequence-two: observed preceding merge

The prerequisite was read from the task checkout.

runtime-change: observed after automatic deployment

The installer fix was read from this task's checkout.

deployment-completion: observed before admission

The deployment gate and signed webhook relay were read from this checkout.

two-job-sqlite: observed consolidated workflow and controller resources

The task checkout contained both phase jobs and the SQLite resource implementation.

sqlite-only-runtime: observed final cleanup after automatic deployment

This task read the preceding acceptance merge and the runtime without relay or migration adapters.
