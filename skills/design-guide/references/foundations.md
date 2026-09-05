# Foundations

## Shared Baseline

* Apply `box-sizing: border-box` to every element and pseudo-element.
* Set the minimum supported viewport width to 320px. Do not introduce horizontal page scrolling at 320px or at 200% browser zoom; only bounded data regions such as tables may scroll horizontally.
* Keep the body fluid instead of enforcing a pixel minimum width that excludes scrollbar space. Check the full document width with overlays both hidden and visible, including mobile browser rendering; do not mask overflow by clipping the page or disabling user zoom.
* Set body text in `--ui` at 15px with a 1.5 line-height. Set buttons, inputs, textareas, and custom comboboxes to inherit the surrounding font and color.
* Set `--ui` to `"IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif` and `--mono` to `"IBM Plex Mono", Consolas, monospace`.
* Use `--mono` only for code, tokens, identifiers, keyboard keys, timestamps, aligned numeric data, and compact ordinal headings.
* Render keyboard focus with a 2px solid `--focus` outline and a 2px offset. Do not remove focus without replacing it with this treatment.
* Keep source order identical to visual reading order. Every visible interactive element must be reachable and operable by keyboard.
* Limit routine color, border, opacity, and background transitions to 80–150ms. Limit overlay entrance transitions to 300ms. Disable non-essential animation when `prefers-reduced-motion: reduce` matches.

## Shared Tokens

### Color Values

| Token | Dark | Light | Apply to |
| --- | --- | --- | --- |
| `--canvas` | `#080b0d` | `#ecefec` | Viewport background and inverted text |
| `--surface` | `#11171b` | `#f7f8f5` | Form controls and bounded content surfaces |
| `--surface-raised` | `#151d21` | `#ffffff` | Menus, dialogs, popovers, and hovered neutral rows |
| `--ink` | `#dbe3e6` | `#172027` | Body text and data values |
| `--ink-strong` | `#f3f6f5` | `#0a0d0f` | Headings, selected labels, and primary-action text |
| `--muted` | `#849094` | `#536168` | Secondary metadata and inactive labels |
| `--faint` | `#566166` | `#778287` | Placeholders and inactive icons |
| `--line` | `#263137` | `#c8d1d0` | Standard borders and row dividers |
| `--line-strong` | `#3a4a51` | `#9dacab` | Active borders, overlay borders, and tab rules |
| `--action-primary` | `#26343a` | `#d5dcdb` | Primary action fill |
| `--action-primary-hover` | `#314249` | `#c5cfce` | Hovered primary action fill |
| `--control-selected` | `#66777e` | `#596970` | Selected checkbox, radio, and switch fill |
| `--control-selected-hover` | `#74868d` | `#4b5c64` | Hovered selected checkbox, radio, and switch fill |
| `--focus` | `#9cc8ff` | `#005fcc` | Keyboard focus only |
| `--accent` | `#62c8d8` | `#006f7c` | One observed chart series |
| `--accent-soft` | `rgba(98, 200, 216, 0.12)` | `rgba(0, 111, 124, 0.10)` | Soft observed-series treatment |
| `--good` | `#79c99e` | `#1d6846` | Confirmed healthy or successful state |
| `--good-soft` | `rgba(121, 201, 158, 0.14)` | `rgba(29, 104, 70, 0.12)` | Healthy or successful surface |
| `--caution` | `#d6ad63` | `#805600` | Warning or degraded state |
| `--caution-soft` | `rgba(214, 173, 99, 0.14)` | `rgba(128, 86, 0, 0.12)` | Warning or degraded surface |
| `--danger` | `#df7e78` | `#9a332f` | Error, outage, or destructive action |
| `--danger-soft` | `rgba(223, 126, 120, 0.14)` | `rgba(154, 51, 47, 0.12)` | Error, outage, or destructive surface |
| `--derived` | `#b9aaef` | `#63559b` | Estimated, modeled, or synthetic data |
| `--derived-soft` | `rgba(185, 170, 239, 0.14)` | `rgba(99, 85, 155, 0.12)` | Estimated, modeled, or synthetic surface |

* Use `--canvas`, `--surface`, `--surface-raised`, `--ink`, `--ink-strong`, `--muted`, `--faint`, `--line`, `--line-strong`, `--action-primary`, `--action-primary-hover`, `--control-selected`, and `--control-selected-hover` for routine interface chrome.
* Use `--good`, `--caution`, `--danger`, and `--derived` only on elements that communicate the corresponding state or data provenance.
* Pair every semantic color with explicit text and, when the color is on a shaped element, the geometry specified below.

### Geometry, Spacing, and Elevation

* Set `--radius` to 6px, `--radius-sm` to 4px, and `--radius-pill` to 9999px.
* Give good-state containers the most rounded geometry in a severity family: use `--radius-pill` for compact badges and `--radius` for larger surfaces.
* Give caution-state containers intermediate geometry: use `--radius-sm`.
* Give every element filled, outlined, or otherwise shaped with `--danger` or `--danger-soft` a 0px radius. Use square or angular danger markers and icons. Danger-colored text with no surrounding shape has no radius requirement.
* Use the spacing sequence 4, 8, 12, 16, 20, 24, 32, 40, and 56px. Do not introduce adjacent one-off spacing values unless required to align text optically inside a fixed-size control.
* Use `0 2px 6px rgba(0, 0, 0, 0.25)` for a small dark-theme shadow and `0 2px 6px rgba(23, 32, 39, 0.08)` in light theme.
* Use `0 16px 40px rgba(0, 0, 0, 0.42)` for a large dark-theme overlay shadow and `0 16px 36px rgba(23, 32, 39, 0.12)` in light theme.
* Apply shadows only to overlays such as menus, popovers, dialogs, and toasts. Do not shadow static page sections or metric groups.

## Icons

* Use Lucide as the only general-purpose icon set. Select icons by their official Lucide names.
* Preserve the Lucide 24×24 view box, 2px stroke, round caps, round joins, no fill, and `currentColor`. Scale the entire SVG uniformly.
* Render standard control icons at 16×16px, compact inline icons at 14×14px, and utility-control icons at 16–18px. Do not vary stroke width to compensate for size.
* Import, tree-shake, or embed only used icons. When vendoring SVG paths, retain the [Lucide ISC license](../assets/component-reference/LUCIDE-LICENSE.txt).
* Do not substitute Unicode characters, emoji, hand-drawn SVGs, or icons from another pack when Lucide contains the concept.
* Use a custom icon only when Lucide has no matching domain concept; construct it on the same 24×24 grid with the same stroke treatment.
* Set decorative SVGs to `aria-hidden="true"` and `focusable="false"`. Give an icon-only button an accessible name and a tooltip containing the same action label.
* Do not add an icon to an inline validation message or status callout when visible text, semantic color, and severity geometry already identify the state.

## Page Shell and Component Sections

### Interactive Page Utilities

* On every page containing interactive controls, navigation, or commands, place a utility group at the upper right. Put the search launcher immediately left of the theme toggle with an 8px gap.
* Size the search launcher to 72×36px. Give it a 1px `--line` border, `--surface` background, 6px radius, and 10px horizontal padding. Place a 15×15px Lucide `Search` icon at the left inset. Leave the rest of the launcher empty.
* Give the search launcher the accessible name `Open search and commands`. Do not render `Search page`, `Ctrl+K`, `⌘K`, or any other visible text inside or beside it.
* Size the theme toggle to 36×36px. Use a Lucide `Sun` or `Moon` icon. Keep the toggle background, border, and shadow transparent in every state; change only icon color or opacity on hover and active states.
* At viewport widths above 600px, position the utility group 20px from the top and 24px from the right. At 600px and below, use 12px top and right offsets and reserve at least 68px above page content.

### Spotlight Search and Commands

* Activating the search launcher, pressing `Ctrl+K` or `Command+K`, or pressing `Ctrl+Space` opens one modal command palette and moves focus to its search input. Put all three combinations in the launcher's `aria-keyshortcuts` value and list them together in the shortcuts popup.
* Size the palette to `min(600px, viewport width minus 32px)`. Position its top edge at 12vh, cap its height at the smaller of 560px and the viewport height minus 24vh, and use `--surface-raised`, a 1px `--line-strong` border, 6px radius, and the large overlay shadow.
* Give the palette a neutral backdrop at `rgba(0, 0, 0, 0.65)`. Keep focus inside the palette while open.
* Place a 15×15px Lucide `Search` icon 16px from the input's left edge. Size the input to 52px high with 42px left padding and 16px right padding. Use only a 1px `--line` bottom border.
* Open Spotlight in command mode. Show one `Jump to…` option followed by only commands the page implements; do not place every section heading in the default result list. Standard commands are `Export as Markdown`, `Export as PDF`, `Toggle theme`, and `Keyboard shortcuts`. Do not add a copy-page-link command because the browser address bar already provides that action.
* Activating `Jump to…` switches the same palette to destination mode, clears the query, changes the dialog and input names plus the input placeholder to `Jump to a component`, changes the listbox name to `Components`, and displays only top-level component destinations. Keep the section ordinal and component name in each destination label.
* Filter only the options in the active mode with case-insensitive substring matching on every input event. Show `No matching commands` in command mode and `No matching components` in destination mode.
* Render each result row as a flex row with its label on the left and shortcut on the right, separated by 24px. Use 14px text with 7px vertical and 10px horizontal padding; reduce the internal gap to 12px at 600px and below. Let the label wrap normally and keep the shortcut from shrinking.
* Render each shortcut as separate neutral `<kbd>` elements in `--mono` with 11px text, 2px vertical and 6px horizontal padding, 4px radius, `--surface-raised` fill, a 1px `--line-strong` border, `--ink` text, `0 1px 0 var(--line-strong)` shadow, and 3px between keys. Keep the keycap treatment unchanged on the active or hovered row.
* Give every result a working page-wide shortcut. Use `Alt+J` to open Spotlight directly in destination mode, `Alt+Shift+M` for Markdown export, `Alt+Shift+P` for PDF export, `Alt+Shift+T` for theme toggle, and `Alt+/` for the shortcut popup. Jump to sections 01–10 with `Alt+1` through `Alt+0`, section 11 with `Alt+Shift+1`, section 12 with `Alt+Shift+2`, and section 13 with `Alt+Shift+3`.
* Handle these shortcuts whether Spotlight is open or closed. Ignore them while focus is in an ordinary input, textarea, select, editable region, or non-Spotlight modal; continue handling them from the Spotlight search input.
* Render `Alt` as `⌥` on Apple platforms and `Alt` elsewhere. Set each result's `aria-keyshortcuts` to its `Alt` combination. Hide the visual keycap group from assistive technology so the option label is not duplicated.
* Add page-wide component traversal with `Alt+ArrowUp` and `Alt+ArrowDown`. Skip to the previous or next top-level component, stop at the first and last component instead of wrapping, align the destination heading to the viewport start, and update the URL fragment to that section. Ignore traversal while the user is editing text or a non-Spotlight modal is open.
* Use `--surface` plus `--ink-strong` and weight 600 for the active row.
* Keep DOM focus in the search input. `ArrowDown` and `ArrowUp` move the active result, `Enter` executes it, and `Escape` closes the palette. Closing returns focus to the launcher.
* Give the input `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded`, `aria-controls`, and `aria-activedescendant`. Give results `role="option"` and maintain `aria-selected`.
* The `Keyboard shortcuts` command opens a popup containing every shortcut active on that page, including the palette accelerators. Shortcut labels may appear only at the right edge of Spotlight results and inside this popup. Do not show them in persistent chrome, buttons, tooltips, labels, navigation items, metadata, or placeholders.
* Close Spotlight and the keyboard-shortcuts popup when the user activates their backdrop. In each dialog's click handler, close only when the event target is the `<dialog>` itself; a click inside the dialog content must not dismiss it. Restore focus to the element that opened the dismissed dialog.

### Component Section Construction

* Wrap each top-level component example in a semantic `<section>` with a unique `id` and `aria-labelledby` pointing to its heading.
* Use one `h2` as the visible heading. Format it as a two-digit ordinal, one space, and the specific component name, for example `01 Buttons & Actions`.
* Put the rendered component content immediately after the heading. Use a 20px gap between heading and content and 56px between top-level sections.
* Do not place a slash, em dash, colon, or decorative separator between the ordinal and component name.
* Do not render `Component`, `Components`, `Specification`, `Variants`, `States`, `States: Disabled & Loading`, or another showcase taxonomy label.
* Do not render a subtitle, description, summary sentence, or horizontal rule beneath the section heading.
* Do not add a border, background fill, radius, or shadow to a page section or example wrapper solely to distinguish it from adjacent sections. Use the heading and spacing for separation.
* Preserve a border or fill only when it is part of the demonstrated component itself, such as an input boundary, menu, table, dialog, chart, alert, or toast.
* Build every example so its visible labels, values, and controls identify the component and its state without explanatory prose.

### Page Layout

* On desktop, cap content at 1240px, center it, use 32px top, 24px horizontal, and 80px bottom padding, and use a 240px navigation column plus one flexible content column separated by 40px.
* Collapse to one column at 900px and below. Hide the component index at that breakpoint unless a mobile navigation replacement is implemented.
* At 600px and below, use 16px horizontal page padding and the reserved 68px top space for utilities. Reduce static example wrappers to 16px vertical padding with no extra horizontal inset.
* Use `minmax(0, 1fr)` for flexible grid tracks and `min-width: 0` on content columns, example groups, and flex children so long labels cannot force page overflow. Cap nested controls at their available width.
* Mark the active side-navigation row with `--ink-strong`, weight 600, and an optional neutral surface. Do not add a leading stripe or border.
* When side navigation scrolls, add at least 4px of inner space on every clipped edge around its links. A 2px focus outline with a 2px offset must remain fully visible on the first, last, leftmost, and rightmost focused link; never let an overflow container crop it.
