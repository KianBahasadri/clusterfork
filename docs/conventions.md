# Conventions and validation

## Repository ownership

Edit configuration in this repository, then install it. Mapped files and trees
are replaced on reinstall; local edits to those destinations are temporary.
[Installation](installation.md#replacement-and-preservation) owns the exceptions
for merged settings, runtime state, and Codex-managed skills.

Keep secrets in the gitignored root `.env` or the relevant credential store.
Do not copy tokens, auth caches, or private keys into documentation or fixtures.

Shell modules are sourced Bash files without shebangs. Launch-specific
environment variables belong in a subshell or the launch command's environment.
Standalone scripts use `set -euo pipefail` where appropriate. Python utilities
use the standard library unless a topic explicitly lists runtime dependencies.

## Local validation

Run the repository suite from the root:

```bash
env -u NO_COLOR python3 -m unittest discover -s tests -v
```

The notification color tests explicitly force ANSI output; an inherited
`NO_COLOR` takes precedence over that request, so unset it for the suite.
Tests use temporary repositories, home directories, fake credentials, and
mocked services. They do not need real agent logins or cloud credentials.

Remote-service tests share `tests/call_budget.py`. Wrap the operation under test
in `CallBudget(self, linear=..., github=..., queue=..., exact={...}, at_most={...})`;
keys such as `linear.issue` and `queue.replace` count calls since entering the
scope, and every unlisted operation must remain zero. Native service mocks and
stateful fakes wrapped with `record_api_calls` retain their existing behavior.
`MemoryTable.operations` counts attempted `create`, `replace`, and `transaction`
calls, including conflicts and duplicate creates; `mutations` records affected
row keys, and `budget.mutated_rows("queue")` isolates keys touched in one scope.
Keep fixture setup and the first settling pass outside the measured scope.

`tests/test_reconciliation.py` exercises completed, canceled, and rejected
histories across the remote services, repeated quiet ticks/project scans, and
fixed budgets for one active worker with and without history. Selection and
steering crash-recovery tests include settled peers and constrain recovery to
the pending row (plus its steering observation control row). Check operation
counts as well as final state; unchanged data can still incur billed requests.

For shell changes, check each script separately:

```bash
for script in install-clusterfork.sh bash_profile.sh shell/*.sh bin/notify \
  bin/clusterfork-notify bin/elevenlabs-mcp statusline/*/*.sh; do
  bash -n "$script" || exit
done
```

Installer tests exercise the installer in temporary homes. For a manual install
check, use a disposable checkout and home with fixture `.env` values; the normal
installer writes configs and repairs existing auth links. Test installed launchers
in a fresh shell after an intentional real installation.

CI also compiles the codeview Python package and indexes the Azure Functions
with their pinned dependencies on Python 3.12. See
[remote setup](remote-development-setup.md#local-validation) for that check.
Design-guide reference build instructions and component checks belong to its
[skill](../skills/design-guide/SKILL.md).
