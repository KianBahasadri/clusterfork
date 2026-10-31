# Conventions and validation

## Repository ownership

Edit configuration here, then install it. Reinstallation replaces mapped files;
[Installation](installation.md#replacement-and-preservation) owns exceptions for
merged settings, runtime state and Codex-managed skills. Keep secrets in the
gitignored root `.env` or credential store, never in documentation or fixtures.

Shell modules are sourced Bash files without shebangs. Scope launch variables to
a subshell or command environment. Standalone scripts use `set -euo pipefail`
where appropriate. Python utilities use the standard library unless documented.

## Local validation

Run from the repository root:

```bash
env -u NO_COLOR python3 -m unittest discover -s tests -v
```

Unset `NO_COLOR` because it overrides the notification tests' forced ANSI output.
Tests use temporary repositories/homes, fake credentials and mocked services.
Manual install checks need a disposable checkout/home and fixture `.env`:
installation writes configs and repairs auth links.

Remote-service tests use `tests/call_budget.py`: wrap the measured operation in
`CallBudget(self, linear=..., github=..., queue=..., exact={...}, at_most={...})`.
Unlisted operations must remain zero. `record_api_calls` preserves fake/mock
behavior. Keep setup and settling outside the scope; check request counts as
well as final state to catch billed no-op traffic.

Check shell scripts separately:

```bash
for script in install-clusterfork.sh bash_profile.sh shell/*.sh bin/notify \
  bin/clusterfork-notify bin/elevenlabs-mcp statusline/*/*.sh; do
  bash -n "$script" || exit
done
```

CI also compiles `bin/codeview` and `scripts/codeview/*.py`, checks Terraform and
workbook queries, imports the controller with pinned dependencies on Python 3.12,
and checks Cursor's read-only sandbox. [Remote setup](remote-development-setup.md#local-validation)
and [diagnostics](remote-development-observability.md#validation-and-post-deployment-smoke-check)
own those checks; design-guide builds belong to its [skill](../skills/design-guide/SKILL.md).
