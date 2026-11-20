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

Use `uv` for local Python commands, dependency installation and virtual
environments. Run checks from the repository root with the pinned requirements:

```bash
env -u NO_COLOR uv run --no-project \
  --with-requirements tests/requirements.txt \
  python -m unittest discover -s tests -v
```

Use the same `uv run` prefix for targeted tests and Python utilities. If a
persistent environment is needed, create it with `uv venv` and install with
`uv pip`.

Unset `NO_COLOR` because it overrides the notification tests' forced ANSI output.
Tests use temporary repositories/homes, fake credentials and mocked services.
Manual install checks need a disposable checkout/home and fixture `.env`:
installation writes configs and repairs auth links.

Check shell scripts separately:

```bash
for script in install-clusterfork.sh bash_profile.sh shell/*.sh bin/notify \
  bin/clusterfork-notify bin/elevenlabs-mcp statusline/*/*.sh; do
  bash -n "$script" || exit
done
```

CI also compiles `bin/codeview` and `scripts/codeview/*.py` and verifies upstream
skill snapshots offline. Design-guide builds belong to its [skill](../skills/design-guide/SKILL.md).
