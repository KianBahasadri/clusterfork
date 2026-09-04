# Data Display

## 5. Labels, Badges, and Metadata

* Render status badges as inline-flex containers with 12px text, weight 500, line-height 1.2, 3px vertical padding, and 8px horizontal padding.
* Do not prepend a colored dot or reserve space for one. Put the complete status in visible text.
* Nominal badge: use `--surface-raised`, `--muted`, a 1px `--line` border, and pill radius.
* Good badge: use `--good-soft`, `--good`, a 30%-mixed good border, and pill radius.
* Caution badge: use `--caution-soft`, `--caution`, a 30%-mixed caution border, and 4px radius.
* Danger badge: use `--danger-soft`, `--danger`, a 30%-mixed danger border, and 0px radius.
* Derived badge: use `--derived-soft`, `--derived`, a 30%-mixed derived border, and pill radius.
* Apply a slow live-state pulse with a 1600ms `ease-in-out` cycle between the base soft background and a 28% current-color tint. Set iteration count to infinite.
* Apply a fast live-state flash with a 600ms `steps(1, end)` cycle between the same two backgrounds. Set iteration count to infinite and keep the rate below three flashes per second.
* Continue either animation while the represented state remains active. Remove the animation immediately when that state clears. Under `prefers-reduced-motion: reduce`, remove the animation and keep the same static text, color, border, and geometry.
* Render non-status tags, versions, and commit hashes in `--mono` at 12px with 2px vertical and 6px horizontal padding, 4px radius, `--surface-raised`, `--muted`, and a 1px `--line` border.
* When a metadata value such as an IP address, version, identifier, commit hash, or token is useful elsewhere, render it as a copyable native button. In a complete reference, make every non-status metadata example copyable. Preserve the plain metadata-tag treatment, give it a minimum 28px height and `cursor: copy`, and provide an action-specific accessible name such as `Copy IP address 192.168.1.104`; use a matching `title` only when the value does not need an expansion tooltip. Do not add a copy icon, and do not make status badges copyable.
* Display commit hashes in their compact seven-character form. On pointer hover and keyboard focus, reveal the exact full hash after 150ms in the neutral tooltip treatment; hide it on pointer exit, blur, or `Escape`, and connect it through `aria-describedby` only while visible. Keep the tooltip within the viewport. Copy the full hash rather than the compact visible label.
* Copy the exact underlying value on click, `Enter`, or `Space`. Prefer `navigator.clipboard.writeText` in a secure context and provide a selection-based fallback for non-secure local previews; preserve focus on the copy button when using the fallback. On success, keep the action-oriented accessible name, tag width, visible value, and 4px radius fixed; use only temporary neutral `--ink-strong` text and a `--line-strong` border for 1600ms, without a copy/check icon or green/good styling. Show one non-undoable success toast reading `Copied to clipboard`; rely on the toast stack's polite live region rather than adding a second inline live announcement. On failure, omit the success state and show a non-undoable danger toast reading `Could not copy to clipboard`.
* Do not place keyboard shortcut labels in badge or metadata rows.

## 6. Tables and Data Grids

* Wrap a table in a full-width region with horizontal overflow. Use a 1px `--line` border, 6px radius, and `--surface` only on the table boundary, not on the containing page section.
* Use semantic `<table>`, `<thead>`, `<tbody>`, `<th scope="col">`, and `<td>` elements. Supply a caption, visible or screen-reader-only, that names the dataset.
* Set table text to 13.5px and collapse borders. Use 10px vertical and 14px horizontal header padding and 12px vertical and 14px horizontal cell padding.
* Use 12px uppercase header text, weight 600, letter-spacing 0.04em, `--muted`, `--surface-raised`, and a 1px `--line-strong` bottom border.
* Left-align prose and identifiers. Right-align numeric values, timestamps, currency, and measurements; use `font-variant-numeric: tabular-nums` and `--mono` for those cells. Align action controls to the right in the final column.
* Separate body rows with a 1px `--line` bottom border and remove that border from the final row. Use `--surface-raised` on row hover.
* Keep a header sticky only inside a vertically scrolling table region. Set its top offset to the region's scroll inset and preserve an opaque surface beneath it.
* Put an actual button inside a sortable header. Set `aria-sort="none"`, `ascending`, or `descending` on the corresponding `<th>`, update the direction icon, and apply a stable sort when activated. Support pointer click, `Enter`, and `Space` through native button behavior.
* Keep routine cell text neutral. Put semantic color only inside a status badge or on a threshold-breaching value that also has a textual status.
* For loading, keep column widths stable and set `aria-busy="true"` on the table region. For empty results, render one neutral message that states no records match and include a filter-reset action when filters are active.

## 7. Charts and Data Graphics

* Render line and area charts as SVG with a responsive width, a 200px default plot height, neutral `--line` gridlines, and 11px `--mono` axis labels in `--muted`.
* Include units in axis titles or every applicable tick label. Do not require the surrounding prose to identify a unit.
* Use semantic color only to distinguish real series or provenance. Pair each series color with a unique solid, dashed, or dotted stroke and, when points are selectable, a distinct marker shape.
* Label every legend entry with the full series name. Match its line sample to the rendered stroke pattern.
* On pointer hover or keyboard focus of a point, show a crosshair and a neutral floating tooltip containing the exact timestamp, series names, values, and units. Keep the tooltip inside the chart's visible bounds.
* Give the graphic an accessible name and concise summary. Follow it with an expandable `<details>` table containing every plotted timestamp and exact value; keep the table data generated from the same source as the SVG.
* Do not encode observed and modeled data with color alone. Use `--accent` with a solid stroke for observed data and `--derived` with a dashed stroke for modeled data by default.

## 11. Metrics, Cards, and Panels

* For a metric group, render only a metric label and value. Put required units, scope, source, or time range into the label or value.
* Do not render a descriptive or supporting subline beneath the value.
* Keep each metric group transparent, borderless, shadowless, and without container padding. Do not use a card background to separate neighboring metrics.
* Lay out three metrics as equal columns with a 32px gap. At 600px and below, use one column with a 24px gap.
* Set metric labels in `--mono` at 12px, uppercase, letter-spacing 0.08em, and `--muted`. Set metric values at 26px, weight 700, line-height 1, `--muted`, and tabular numerals. Let size and weight establish hierarchy; do not use `--ink-strong` for large metric values.
* Add a bordered or filled card only for a real interactive, independently stateful, draggable, selectable, collapsible, or semantically bounded object. Do not add one for page-section separation.
* When a real card boundary is required, use `--surface`, a 1px `--line` border, 6px radius, and no shadow unless the card is temporarily elevated during drag.

## 13. Color Semantics

* Add a `Color Semantics` section to every complete component reference. Show every shared color token as a swatch with its exact token name, one short usage label, and both dark- and light-theme values; do not rely on a prose introduction to explain the palette.
* Include `--canvas`, `--surface`, `--surface-raised`, `--ink`, `--ink-strong`, `--muted`, `--faint`, `--line`, `--line-strong`, `--action-primary`, `--action-primary-hover`, `--control-selected`, `--control-selected-hover`, `--focus`, `--accent`, `--good`, `--caution`, `--danger`, and `--derived`. Show each semantic token's soft tint in the same swatch and label the tint opacity beside each theme value.
* Lay tokens out in a responsive grid with a 240px minimum column width, 32px column gaps, and 24px row gaps. At narrow widths, collapse to one column without horizontal overflow.
* Build each token as a 44px swatch beside a text block. Set the token name in 12px semibold `--mono`, the usage label in 13px `--muted`, and theme values in 10.5px `--faint` monospace text that may wrap only between complete values.
* Keep every swatch and soft-tint region borderless, dividerless, outlineless, and shadowless; the color fill itself provides the shape. Use a 6px default radius, keep good swatches at 6px, caution swatches at 4px, and danger swatches at 0px; never round a red swatch.
* Use `Viewport background`, `Controls and bounded surfaces`, `Overlays and active rows`, `Body text and data values`, `Headings and selected values`, `Secondary metadata and labels`, `Placeholders and inactive icons`, `Borders and row dividers`, `Active and overlay boundaries`, `Primary action fill`, `Hovered primary action fill`, `Selected control fill`, `Hovered selected control fill`, `Keyboard focus only`, `Observed data`, `Healthy or successful`, `Warning or degraded`, `Error, outage, or destructive`, and `Estimated, modeled, or synthetic` as the corresponding usage labels in token order.
