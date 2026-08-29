# Patterns

Cross-cutting situations every surface must handle. A missing state here is the most common "unfinished UI" smell.

## Empty states

Never render a blank region. An empty state says: (1) what belongs here, (2) why it is empty, (3) the single next action.

```
GUI:   icon + "No projects yet" + "Projects collect your deployments."
       + [Create project] button
TUI:   centered box: "no projects — press n to create one"
CLI:   "No projects found. Create one with: app project create"
```

Distinguish "empty because nothing yet" from "empty because a filter matched nothing" — the second offers "Clear filter", not "Create".

## Loading

- <300ms expected: show nothing (a flash of spinner is worse than a beat of stillness).
- Blocking actions of unknown length: progress indication with the operation name ("Deploying api… step 2/4").
- GUI: skeleton shapes matching the final layout beat generic spinners.
- TUI: update one line in place (`\r`), show k/n and the current item; a spinner frame cycle is fine.
- CLI: stages to stderr; resumable, pipe-safe, and never leaves a dangling progress bar on failure.

## Error states

Every error message has three parts: what happened, why (if knowable), what to do. Show them at the point of action.

```
bad:  "Error 500"
good: "Couldn't save your changes: the server was unreachable.
       Check your connection and try again — your edits are kept."
```

- GUI: keep user input on error; never make them retype the form.
- TUI: print the error in a visible panel/status area, not scrolled away; leave input intact.
- CLI: message to stderr, non-zero exit, suggestion line with the exact command to fix/continue (`Run --retry to resume from step 3`).

Partial failures state exactly what succeeded — "3 of 5 files uploaded; retry the remaining 2" beats all-or-nothing erroring.

## Destructive actions

- Confirm with the *consequence* restated and the specific object named ("Delete branch 'main'? This cannot be undone.").
- Type-to-confirm (`yes`/branch name) when the blast radius is large or irreversible.
- Prefer recoverable: trash/undo window beats confirmation dialogs. Confirmation is the floor, not the goal.
- GUI: danger button is not adjacent to the safe one with equal weight; focus starts on the safe choice.
- CLI: non-interactive contexts require `--yes`/`--force` rather than hanging on a prompt.

## Progress & long operations

- Always show scope: total steps, elapsed, and the current item. Unknown totals say so ("Scanning… 1,240 files so far") instead of a fake percentage.
- Operations can be cancelled (GUI button, TUI key, Ctrl-C in CLI) — and cancellation itself is confirmed for multi-step mutations, or made resumable so it does not need confirming.
- CLI long output: `--quiet` for scripts, default summary at the end ("Done in 42s: 5 built, 0 failed").

## Help & discoverability

- GUI: tooltips for icon-only controls; a visible help/docs path; keyboard shortcuts listed where they work.
- TUI: `?` toggles the keybinding help everywhere; the bottom bar always shows the currently available keys.
- CLI: `--help` is the product — every flag documented with an example, usage line shows the happy path, `-h` exit code 0. Errors print the 2–3 most likely commands after the failure ("Did you mean: app logs --tail?").

## Onboarding & first run

Show the happy path once, inline, and get out of the way: a GUI empty state with one CTA, a TUI first-run hint line, a CLI `app init` scaffold suggestion printed after a first successful command in an uninitialized directory. Never gate core functionality behind a tour.
