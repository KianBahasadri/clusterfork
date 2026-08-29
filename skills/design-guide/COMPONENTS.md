# Components

Per-component rules with good/bad examples. GUI examples are rendered in `reference.html` (GUI only — TUI/CLI sections here are their canonical reference instead).

## Buttons (GUI)

- One **primary** button per view (filled accent). Everything else is secondary (outlined) or tertiary (text-only).
- Danger actions are red, confirmed, and placed away from the primary action (never next to "Save" with equal weight).
- Labels are verbs: "Save changes", not "OK"/"Submit". No ambiguity about what gets destroyed: "Delete 3 files", not "Yes".
- Disabled must still *explain*: use a tooltip or helper text for why.
- Every button: hover, focus-visible, active, disabled states. Focus ring is never `outline: none` without a replacement.

```html
<!-- bad: equal weight, vague labels, no feedback -->
<button style="background:#ccc">OK</button>
<button style="background:red">Confirm</button>

<!-- good -->
<button class="btn-primary">Save changes</button>
<button class="btn-secondary">Cancel</button>
```

### TUI equivalent

Actions live in a menu, keybinding bar, or dialog box. The "primary" action is the one bound to `Enter`; destructive ones get a typed confirmation (`type yes to confirm`) and are never on a single keypress of an easy key. Show key hints right-aligned: `^S Save  ^Q Quit`.

### CLI equivalent

No buttons — the analogues are confirmation prompts and exit behavior. Confirmations default to *no* on plain Enter, restate what will be destroyed, and accept an exact word (`yes`), not any key. Non-interactive runs (piped stdin) skip prompts and require an explicit flag (`--yes`) instead of hanging.

## Forms (GUI)

- Label **above** the input (placeholder text is not a label — it disappears on typing).
- Helper text below the input, error text replaces it in the danger color with an icon, and the input gets a danger border.
- Validate on blur or submit, not on every keystroke. Show errors **next to** the field and in a summary for long forms.
- One column. Two-column forms have measurably more field-skipping errors.
- Required is marked the sparse way (mark optional, not required) or not at all when everything is required.

```html
<!-- bad -->
<input placeholder="Email">          <!-- label vanishes while typing -->

<!-- good -->
<label for="email">Email</label>
<input id="email" type="email" aria-describedby="email-help">
<p id="email-help">We only use this for login recovery.</p>
```

### TUI equivalent

One prompt per line, `[label]:` with the current value inline; edits happen in place. Validation message prints below the prompt and re-prompts; never clears the user's input on error.

### CLI equivalent

Inputs are flags (`--email a@b.c`) — the form analog is a `--help` block where every flag has a one-line description with a metavariable (`--email <address>`). Prompts (when unavoidable) print to stderr so piped stdout stays clean data.

## Tables & lists (GUI)

- Numbers right-aligned, text left-aligned. Headers sticky when the table scrolls.
- Row actions appear on hover/selection, not as a column of 5 icon buttons per row.
- Dense is fine: 32–40px rows; never pad tables to airy card proportions.
- Sortable headers show the current sort direction; empty tables show an empty state (PATTERNS.md), not a blank box.

### TUI / CLI equivalent

This is *the* core TUI/CLI component — align columns, right-align numbers, abbreviate consistently, truncate with `…` rather than wrap mid-cell:

```
bad:                          good:
name  size  modified          NAME      SIZE  MODIFIED
app.py  1203  2026-08-01      app.py    1203  Aug 01
a-very-long-name…
```

CLI: `--json` (or similar) for machine output; the pretty table is for humans only.

## Dialogs & modals (GUI)

- Use for one decision, not for hosting a workflow (wizards are pages, not modals).
- Always reachable: Esc cancels, focus is trapped inside, and the default button is the safe one (Cancel), activated by Enter — except destructive confirms, where Enter does nothing until an explicit choice.
- Title states the decision: "Delete branch 'main'?" — never "Are you sure?" alone.

### TUI equivalent

Centered bordered box, dimmed rest of screen if possible, `Esc` cancels, hint bar inside shows the keys.

### CLI equivalent

A confirmation prompt or a dedicated subcommand (`app remove x`), never an interactive wizard mid-pipeline.

## Navigation (GUI)

- Current location is visibly highlighted and non-clickable-looking (or clickable to reset — pick one, everywhere the same).
- Persistent nav stays put; context switches via tabs within the page, not full nav swaps.
- Depth comes from grouping (sidebars with sections), not from nesting menus three levels deep.

### TUI equivalent

Keybindings are the nav. They are consistent across every screen (`q` quits the current view, never the whole app on one screen and a panel on another), discoverable via `?`, and shown in the bottom bar. Tab order and focus indication (reverse video) must be visible at all times — a TUI without a visible focus is unusable.

### CLI equivalent

Subcommands are the nav: noun-verb (`app deploy`), consistent aliases, and `--help` at every level. Unknown input never silently "does something close": print the closest match suggestion and exit non-zero.

## Feedback surfaces (GUI)

- **Toast** for success/confirmations of background actions: short, auto-dismiss (4–8s), stacked bottom or top-right, dismissible.
- **Inline** for validation and field-level issues — never a toast for form errors.
- **Banner/alert** for page-level conditions (outage, unsaved changes): color carries the meaning word too ("Error:").
- Loading: skeletons for >300ms expected loads, spinners only for indeterminate blocking actions; buttons show in-progress state and disable.

### TUI equivalent

Status line (bottom bar) for transient messages with the key to dismiss; flash/bell sparingly; long operations show a progress bar or at minimum "step k/n — current item" updated in place (never scrolling a wall of printouts).

### CLI equivalent

Exit code (0 success, non-zero failure, distinct codes per failure class when practical), human error to **stderr**, data to **stdout**, and progress to stderr so pipes stay clean. `--verbose`/`--quiet` control noise. Silence between start and finish is a bug: print what stage is running.
