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

**GUI (px, base 16, ratio ~1.25):**

Scale: `12, 14, 16, 20, 25, 31`.

- Body: 16, line-height 1.5
- Secondary/meta text: 14
- Small print / badges: 12
- Section heading: 20 bold
- Page title: 25–31 bold, line-height 1.2
- Body text measure: 60–75 characters per line — wider columns tire readers

Use the system font stack; do not require custom font downloads:

```css
font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; /* code */
```

Emphasis: **bold** for weight, never italic-at-small-sizes or ALL CAPS for body text (caps only for tiny labels/badges).

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
- **60/30/10:** ~60% neutral background, ~30% secondary surface/text, ~10% accent. One accent color. If two things compete in saturation, one of them is wrong.

**GUI palette shape** (see `reference.html` for the rendered version — GUI only):

```
background      #ffffff   secondary surface #f5f5f5   border #e0e0e0
text            #1a1a1a   secondary text    #5f5f5f
accent          #2563eb   accent-text-on    #ffffff
success #15803d   warning #b45309   danger #b91c1c   (on light bg; all ≥ 4.5:1)
```

Dark mode: invert roles, keep the contrast ratios; never just swap pure black/white (use `#0f172a`-style near-black and off-white text).

**TUI:** use the terminal's 16 ANSI colors, or 256/truecolor only when already detected as supported. Secondary text = dim/gray. Respect `NO_COLOR` and non-TTY: output must remain fully understandable monochrome.

**CLI:** same as TUI, and: color only goes to a TTY (check `isatty`, honor `NO_COLOR`, offer `--color=auto|always|never` when output is likely to be piped).

## Layout

**GUI:** content in a single readable column where possible; max content width ~1200px for dashboards, ~720px for prose. Grids of cards share identical heights per row or explicitly not (mixed heights must look deliberate, not broken). Whitespace is a layout element: cramped screens are redesigned, not shrunk.

**TUI:** design for 80×24 minimum, degrade gracefully below. Panels have a 1-cell gap or share connected borders — not both. Key hints go on a bottom bar (e.g. `q quit  / search  ? help`).

**CLI:** output reads top-down like a document: summary → detail → next actions. Line width ≤ 80 chars for help text; data output (tables, JSON) is allowed to be wide because it is consumed by tools.
