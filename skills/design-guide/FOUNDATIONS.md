# Foundations

Shared values for every surface. When a value you need is not here, derive it from the scale — never invent one.

## Spacing

**GUI (px, base unit 4):**

Scale: `4, 8, 12, 16, 24, 32, 48, 64`.

- Inside a component (padding of button, input): `8–12`
- Between related items (label to input, list rows): `8–16`
- Between groups (sections, cards): `24–32`
- Page gutter and major regions: `24–48`

Rule: pick from the scale by *relationship* — things related are closer than things unrelated. If two gaps differ, the difference must mean something.

```css
/* good: from the scale */
.card  { padding: 24px; }
.card h2 + p { margin-top: 8px; }
.card + .card { margin-top: 16px; }

/* bad: improvised values */
.card  { padding: 22px; }
.card h2 + p { margin-top: 7px; }   /* scale exists to prevent this */
```

**TUI (cells):** padding inside boxes: 1 cell (2 for emphasis panels). Separation between groups: one blank line; between sections: two. Vertical rhythm is blank lines — do not scatter them randomly; keep the same blank-line pattern between every group of the same kind.

**CLI (lines):** one blank line between logical blocks (options list, examples, footer in `--help`). Never more than two. Aligned columns are spacing: keep column gaps at 2+ spaces and consistent per column.

## Type

**GUI (px):**

Scale: `10, 11, 13, 15, 20, 30, 48`.

- Body: 15, line-height 1.5
- Secondary/meta text: 13
- Small print / badges: 10; mono labels (uppercase, tracked): 11
- Mono data (tables, code, log lines, values): 13
- Section heading: 20 bold
- Page title: 30 bold; display numerals (glance-distance counters): 48
- Body text measure: 60–75 characters per line — wider columns tire readers

Three font roles; system stacks only, no custom font downloads:

```css
--sans: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;   /* prose, sans headings */
--mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;         /* labels, data, code, page title */
--mono-term: "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", Menlo, Consolas, ui-monospace, monospace;
```

`--mono-term` is a separate stack used **only** for TUI/CLI views: box-drawing and block elements must be single-cell, which the GUI stacks do not guarantee.

Emphasis: **bold** for weight, never italic-at-small-sizes or ALL CAPS for body text (caps are for labels, headings and badges — see Structure).

**TUI:** one font, so hierarchy comes from attributes and placement, not size:

- Bold for titles and key values
- Reverse video (or a filled header row) for column headers and panel titles
- Dim/low-intensity color for secondary text
- `UPPERCASE` only for single-word section labels, never whole sentences

**CLI:** plain text. Hierarchy = ordering and wording, plus the TUI tricks that survive non-TTY output (caps section labels in `--help`, e.g. `Options:`, `Examples:`). Assume the output may be piped: never depend on formatting to convey meaning.

## Color

Universal rules:

- **Contrast:** text meets 4.5:1 against its background (3:1 for text ≥ 24px/19px bold). Borders of interactive elements ≥ 3:1.
- **Never color alone:** color communicates *in addition to* a word or icon — `✗ Failed`, not a red row by itself. This is also what makes TUI/CLI (16 colors, or none) and color-blind users workable.
- **Meaning is reserved:** success/warning/danger colors mean exactly that, everywhere, in every surface. Do not use the danger red for a delete *icon* theme or marketing accent.
- **Chart series are not status:** series take `--accent`, `--text`, `--border-hi`, `--border` — luminance steps, told apart further by pattern (solid / dashed / hatch). Status colors never appear as a series color. This is why the reference charts are monochrome plus one accent.
- **60/30/10:** ~60% neutral background, ~30% secondary surface/text, ~10% accent. One accent color. If two things compete in saturation, one of them is wrong.

**GUI palette** (dark is the base; no light theme ships. Rendered in `reference.html#foundations`; ratios are against `--bg`):

```
--bg        #0a0a0a   page
--surface   #131313   panel
--raised    #1b1b1b   hover
--border    #2e2e2e   hairline inside a panel
--border-hi #6b645a   3.4:1    panel edge and every interactive border
--text      #e8e5e0   15.8:1
--text-2    #8f8a82   5.8:1
--accent    #ff5900   6.3:1    (--accent-text #0a0a0a sits on it)
--success   #2fe08a   11.5:1
--warning   #ffd000   13.5:1
--danger    #ff2b55   5.4:1
```

**TUI:** use the terminal's 16 ANSI colors, or 256/truecolor only when already detected as supported. Secondary text = dim/gray. Respect `NO_COLOR` and non-TTY: output must remain fully understandable monochrome.

**CLI:** same as TUI, and: color only goes to a TTY (check `isatty`, honor `NO_COLOR`, offer `--color=auto|always|never` when output is likely to be piped).

## Structure (GUI)

The brutalist invariants the reference render never breaks:

- **Radius 0.** Nothing is rounded anywhere. (The reference's only `border-radius` declaration is a `0` reset on `::-moz-range-thumb`, which Firefox rounds by default.)
- **Border weight is meaning:** 1px `--border` = hairline inside a panel; 1px `--border-hi` = panel edge and every interactive border; 1px `--text` = region split and dialog frame; 2px `--border-hi` = interactive edge; 2px `--accent` = focus ring and active tab.
- **Shadows are hard offsets, never blurred — and scarce:** `4px 4px 0 var(--border-hi)` on the primary button only; `6px 6px 0 rgba(0,0,0,.55)` on overlays (menu, toast, popover); `8px 8px 0` on the mobile phone frames; `inset 3px 0 0 var(--accent)` marks a selected row's first cell. Nothing else gets a shadow.
- **No soft gradients.** Gradients appear only as hard-edged patterns (`repeating-linear-gradient`, `conic-gradient`, `radial-gradient` with coincident stops) for generated artwork.
- **Motion is stepped** (`steps(1)`, `steps(8)`, `steps(9)`, `steps(10)`), never eased, and all of it is killed under `prefers-reduced-motion: reduce`.
- **UPPERCASE is for labels, headings and badges** — never body copy.

## Layout

**GUI:** doc-style views are a single readable column capped at **1040px**; app shells (dashboard, desktop, console, terminal screens) are full-viewport frames with their own chrome and no page scroll on the body. Grids of cards share identical heights per row or explicitly not (mixed heights must look deliberate, not broken). Whitespace is a layout element: cramped screens are redesigned, not shrunk.

**TUI:** design for 80×24 minimum, degrade gracefully below. Panels have a 1-cell gap or share connected borders — not both. Key hints go on a bottom bar (e.g. `q quit  / search  ? help`). Rendered: [reference.html](reference.html)`#tui`.

**CLI:** output reads top-down like a document: summary → detail → next actions. Line width ≤ 80 chars for help text; data output (tables, JSON) is allowed to be wide because it is consumed by tools. Rendered: [reference.html](reference.html)`#cli`.
