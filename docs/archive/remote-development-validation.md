# Remote development live validation

Recorded 2026-10-28. These are historical test receipts, not a current health
check. [Remote development](../remote-development.md) owns runtime behavior;
[setup](../remote-development-setup.md) owns resource names and provisioning.

## Routing pilot

[KIAN-891](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-891/verify-clusterforks-linear-azure-github-routing-pilot)
verified the Linear webhook → Azure queue → GitHub App → hosted runner path.
A real Todo issue update in the enrolled Clusterfork project triggered
[run 40607700052](https://github.com/KianBahasadri/clusterfork/actions/runs/40607700052)
as `clusterfork[bot]`. A second update and signed duplicate did not dispatch
again. [Python 3.12 CI](https://github.com/KianBahasadri/clusterfork/actions/runs/40607363546)
and a [direct smoke run](https://github.com/KianBahasadri/clusterfork/actions/runs/40606446755)
passed. Health, queue delivery, and Table claims passed; forged/expired requests
were rejected. Temporary storage records were removed.

## First coding worker and automatic merge

[KIAN-892](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-892/report-the-configured-codex-worker-mode-in-the-setup-checker)
triggered [worker run 40743398281](https://github.com/KianBahasadri/clusterfork/actions/runs/40743398281).
It inspected available accounts, selected Sol/max by quota, updated the setup
checker with tests/docs, and opened
[PR #1](https://github.com/KianBahasadri/clusterfork/pull/1).
Credentials persisted and account leases released. Repeated issue updates
produced no duplicate run.

A later verification used the worker's merge helper and repository-scoped App
token. PR #1 stayed open while required checks ran, then squash-merged as
`5b3474c` without a review or Administration grant.
[Post-merge CI](https://github.com/KianBahasadri/clusterfork/actions/runs/40755845581)
passed. At that time the repository enabled auto-merge and branch deletion;
`main` required `test` and `routing-runtime` from GitHub Actions (App ID 15368),
with zero reviews and non-strict checks. Administrator maintenance bypass
remained available; worker tokens had no bypass.

Reconciliation read the actual merged PR, moved KIAN-892 to Done, and created
one owned status comment. Repeating it produced no duplicate update.
