# Components

Per-component rules with good/bad examples. Every family below is rendered in `reference.html`: components under `#components`, data display under `#data`, TUI under `#tui`, CLI under `#cli`.

## Buttons (GUI)

- One **primary** (`.btn-primary`, filled accent) per view — it is the only element that gets an offset shadow (FOUNDATIONS.md § Structure), so the rule enforces itself visually. Everything else is secondary (`.btn`, outlined), ghost (`.btn-ghost`, borderless), or danger (`.btn-danger`, red outline).
- Modifiers: `.btn-sm` / `.btn-lg` size, `.btn-icon` icon-only (requires `aria-label`), `.btn-block` full-width, `.btn-group` joined set.
- Danger actions are confirmed and placed away from the primary action (never next to "Save" with equal weight).
- Labels are verbs: "Save changes", not "OK"/"Submit". No ambiguity about what gets destroyed: "Delete 3 files", not "Yes".
- Disabled must still *explain*: use a tooltip or helper text for why.
- Every button: hover, focus-visible, active, disabled states. Focus ring is never `outline: none` without a replacement.

```html
<!-- bad: equal weight, vague labels, no feedback -->
<button style="background:#ccc">OK</button>
<button style="background:red">Confirm</button>

<!-- good -->
<button class="btn-primary">Save changes</button>
<button class="btn">Cancel</button>
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

## Menus & dropdowns (GUI)

- The trigger looks like a button; the popup is an overlay (offset shadow, FOUNDATIONS.md § Structure). Click outside and `Esc` both close it — silence reads as broken.
- Group long menus with section headers and separators; the destructive item is separated from the safe ones, at the bottom.
- Disabled items stay visible and say why ("no access") — they don't disappear.
- Shortcut hints sit right-aligned per item, and never contradict the action.

### TUI equivalent

Menu bar or key-driven palette: items are rows navigated with arrows and taken with `Enter`. Same grouping and danger-at-the-bottom rules.

### CLI equivalent

No menus — the analogues are subcommands and flags. Never park required input behind an interactive menu.

## Combobox (GUI)

- Text input with a filtered list attached; the query is highlighted inside the matches and the match count is printed ("3 of 128").
- Keyboard is the primary path: type to filter, arrows to move, `Enter` selects, `Esc` closes. The current item is marked, not just hovered.

### TUI equivalent

Filter-as-you-type over a list; input line and list stay adjacent, current row reverse-video.

### CLI equivalent

A flag takes the value; on invalid input the error lists the valid ones.

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

## Cards & KPIs (GUI)

- Cards are flat bordered panels — no shadow, no rounding; hierarchy comes from border weight and content, not elevation.
- A KPI card is label → value → delta: the value is the largest text on it, in tabular figures; the delta states direction with a symbol/word too (▲ 12.4%), never colour alone.
- Cards in a row share one height, or the difference is deliberate.
- A fully-clickable card is one link, not a link inside a card inside a link.

### TUI equivalent

A bordered box with a title line; KPI chrome is unnecessary — `label: value` rows do the same job.

### CLI equivalent

No cards — a record is one table line or a short key/value block.

## Gallery (GUI)

- Tiles show generated artwork or local/`data:` images — never a network fetch.
- Hover may reveal actions, but selection is always marked on the tile itself (accent edge + pick mark) and counted in the footer — keyboard users get the same via focus/selection.
- Metadata (name, dimensions) sits in a caption strip, not floating over the art.

### TUI equivalent

A list: name, dimensions, size. No thumbnails; selection is a marked row.

### CLI equivalent

A plain listing; media specifics belong to whatever opens the file.

## Dialogs & modals (GUI)

- Use for one decision, not for hosting a workflow (wizards are pages, not modals).
- Always reachable: Esc cancels, focus is trapped inside, and the default button is the safe one (Cancel), activated by Enter — except destructive confirms, where Enter does nothing until an explicit choice.
- Title states the decision: "Delete branch 'main'?" — never "Are you sure?" alone.

### TUI equivalent

Centered bordered box, dimmed rest of screen if possible, `Esc` cancels, hint bar inside shows the keys.

### CLI equivalent

A confirmation prompt or a dedicated subcommand (`app remove x`), never an interactive wizard mid-pipeline.

## Drawer, popover & tooltip (GUI)

- **Drawer:** a side panel inside the page frame, not a modal — for filters/inspectors that need the main view visible. `Esc` or an explicit close exits.
- **Popover:** anchored detail for one object, opened by click or hover. It carries *data* (key/value rows); action lists belong to menus. Closes on outside click and `Esc`.
- **Tooltip:** one short line for icon-only controls and truncated text. Never the only place required information lives; never on touch.
- All three are overlays: 1px text-colour frame plus offset shadow (FOUNDATIONS.md § Structure).

### TUI equivalent

Drawer → split pane or overlay box; tooltip → the status-bar hint; popover → a small bordered box. `Esc` closes all of them.

### CLI equivalent

Drawer/popover → flags and subcommands (`app node inspect x`); tooltip → the flag description in `--help`.

## Navigation (GUI)

- Current location is visibly highlighted and non-clickable-looking (or clickable to reset — pick one, everywhere the same).
- Persistent nav stays put; context switches via tabs within the page, not full nav swaps.
- Depth comes from grouping (sidebars with sections), not from nesting menus three levels deep.
- **Tabs** swap the panel in place; the active tab is marked with the 2px accent edge and carries `aria-selected` — colour alone is not the marker.
- **Segmented controls** are radio-like filters of one dimension (Day/Week/Month); exactly one pressed per group.
- **Breadcrumbs** render ancestors as links, the current page as plain text (`aria-current`), separated consistently (`/`).
- **Pagers** show the current page (`aria-current`), disable impossible moves, and collapse long ranges (`1 2 3 … 12`).

### TUI equivalent

Keybindings are the nav. They are consistent across every screen (`q` quits the current view, never the whole app on one screen and a panel on another), discoverable via `?`, and shown in the bottom bar. Tab order and focus indication (reverse video) must be visible at all times — a TUI without a visible focus is unusable. Tabs/segments are a reverse-video row; a pager marks the current page the same way.

### CLI equivalent

Subcommands are the nav: noun-verb (`app deploy`), consistent aliases, and `--help` at every level. Unknown input never silently "does something close": print the closest match suggestion and exit non-zero.

## Badges, tags, kbd & avatars (GUI)

- **Badges** carry status and always pair the colour with a word ("Passing") or a dot — never colour alone. Solid accent badge = attention/new, not status.
- **Tags** are user-assigned and removable; the remove control is a real named button (`aria-label="Remove infra"`).
- **`kbd`** marks literal keys only — never prose emphasis.
- **Avatars** fall back to initials when no image; stacked avatars overlap with a hard cut so each stays countable.

### TUI equivalent

Status is a word plus a symbol (`✓ passing`, `! degraded`, `✗ failed`); keys are shown `^S`/`F1` style. Identities are plain text.

### CLI equivalent

Status words and symbols; no badges, tags, or avatars.

## Progress (GUI)

- **Determinate bar:** the numbers live in the label ("68% · 1.4 of 2.1 gb · 40s left") — a bare bar is not feedback.
- **Indeterminate bar:** its label says duration is unknown; a moving bar alone is the last resort.
- **Segmented meter:** capacity against a threshold; segments past the threshold turn danger.
- **Step indicator:** done/current/upcoming get different marks; current is the accent one and says what it is.

### TUI equivalent

Block bar (`████░░░░`) + `k/n` + current item on one line, updated in place — the behavior rules are PATTERNS.md's.

### CLI equivalent

`k/n` and stage names to stderr; no animated bars when output is piped.

## Feedback surfaces (GUI)

- **Toast** for success/confirmations of background actions: short, auto-dismiss (4–8s), stacked bottom or top-right, dismissible.
- **Inline** for validation and field-level issues — never a toast for form errors.
- **Banner/alert** for page-level conditions (outage, unsaved changes): color carries the meaning word too ("Error:").
- Loading: skeletons for >300ms expected loads, spinners only for indeterminate blocking actions; buttons show in-progress state and disable.

### TUI equivalent

Status line (bottom bar) for transient messages with the key to dismiss; flash/bell sparingly; long operations show a progress bar or at minimum "step k/n — current item" updated in place (never scrolling a wall of printouts).

### CLI equivalent

Exit code (0 success, non-zero failure, distinct codes per failure class when practical), human error to **stderr**, data to **stdout**, and progress to stderr so pipes stay clean. `--verbose`/`--quiet` control noise. Silence between start and finish is a bug: print what stage is running.

## Tree, code & log (GUI)

- **Tree:** indentation is position, chevrons show expand state, the selected row is marked, and rows may carry a right-aligned meta value (size, count).
- **Code block:** line numbers live in a separate gutter; syntax colour is limited to a few roles (keyword / string / comment) — never a rainbow.
- **Log:** one event per line — time, level, message. The level is a fixed-width word (`INFO`/`WARN`/`ERR`/`OK`) coloured consistently; timestamps are the dim column so messages scan.

### TUI equivalent

Tree with ASCII indent guides (`├─`); the log pane uses the same level words. Show line numbers only when referring to lines.

### CLI equivalent

Tree → `--tree` output or a plain recursive listing. Log → one grep-able line per event, level as a word, sortable timestamps.

## Charts (GUI)

- Series take the four chart colours from FOUNDATIONS.md § Color (luminance steps), told apart further by pattern — solid / dashed / hatch. Status colours are never a series.
- One accent highlight per chart: the series or bar that *is the answer* gets the accent; everything else stays neutral.
- Values are printed on the data ("90" over the bar, "p95 · 184ms" at the marker) — a chart unreadable without its legend is unfinished; keep legends for multi-series only.
- Axis labels are micro uppercase mono; gridlines are hairlines behind the data; axes are plain lines, no arrows or ticks.
- Sparklines have no axes or labels — they sit beside the number they summarise.

### TUI equivalent

Block bars (`████░░░░ 4/5`) and monochrome-plus-accent, same as GUI. A TUI "chart" is the numbers, arranged.

### CLI equivalent

No charts — emit the data (table/JSON) and let the consumer draw.
