# Fix browser CLI arg-order parsing

## Goal
Make the browser tool commands parse flags and positional arguments reliably regardless of argument order, so day-to-day usage does not fail unpredictably.

## Stages

### Stage 1: Confirm failure surface
Identify exactly which scripts are affected and how argument order currently breaks behavior, so we fix the right scope without regressions.

#### mini-researcher
Inspect `tools/browser-tools/*.js` argument parsing and return a precise bug report with minimal reproduction commands and affected files.

### Stage 2: Implement robust parsing
Update the affected scripts to consume flag values correctly and avoid treating consumed values as positional arguments, while preserving current CLI behavior.

#### mini-implementer
Patch the relevant browser tool scripts in `tools/browser-tools/` to make argument parsing order-independent for `--port` and existing flags.

### Stage 3: Verify end-to-end behavior
Run command-level smoke tests for both flag-before-positional and flag-after-positional forms to prove the fix works in the installed environment.

#### mini-tester
Run `browser-start`, `browser-nav`, and `browser-eval` checks covering both argument orders and report pass/fail evidence.
