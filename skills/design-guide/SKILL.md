---
name: design-guide
description: Apply exact UI component implementation specifications covering tokens, anatomy, dimensions, states, interaction, accessibility, and responsive behavior.
metadata:
  short-description: Exact UI implementation contract
---

## Components

### Shared Baseline

* Apply `box-sizing: border-box` to every element and pseudo-element.
* Set the minimum supported viewport width to 320px. Do not introduce horizontal page scrolling at 320px or at 200% browser zoom; only bounded data regions such as tables may scroll horizontally.
* Set body text in `--ui` at 15px with a 1.5 line-height. Set buttons, inputs, textareas, and custom comboboxes to inherit the surrounding font and color.
* Set `--ui` to `"IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif` and `--mono` to `"IBM Plex Mono", Consolas, monospace`.
* Use `--mono` only for code, tokens, identifiers, keyboard keys, timestamps, aligned numeric data, and compact ordinal headings.
* Render keyboard focus with a 2px solid `--focus` outline and a 2px offset. Do not remove focus without replacing it with this treatment.
* Keep source order identical to visual reading order. Every visible interactive element must be reachable and operable by keyboard.
* Limit routine color, border, opacity, and background transitions to 80–150ms. Limit overlay entrance transitions to 300ms. Disable non-essential animation when `prefers-reduced-motion: reduce` matches.

### Shared Tokens

#### Color Values

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

#### Geometry, Spacing, and Elevation

* Set `--radius` to 6px, `--radius-sm` to 4px, and `--radius-pill` to 9999px.
* Give good-state containers the most rounded geometry in a severity family: use `--radius-pill` for compact badges and `--radius` for larger surfaces.
* Give caution-state containers intermediate geometry: use `--radius-sm`.
* Give every element filled, outlined, or otherwise shaped with `--danger` or `--danger-soft` a 0px radius. Use square or angular danger markers and icons. Danger-colored text with no surrounding shape has no radius requirement.
* Use the spacing sequence 4, 8, 12, 16, 20, 24, 32, 40, and 56px. Do not introduce adjacent one-off spacing values unless required to align text optically inside a fixed-size control.
* Use `0 2px 6px rgba(0, 0, 0, 0.25)` for a small dark-theme shadow and `0 2px 6px rgba(23, 32, 39, 0.08)` in light theme.
* Use `0 16px 40px rgba(0, 0, 0, 0.42)` for a large dark-theme overlay shadow and `0 16px 36px rgba(23, 32, 39, 0.12)` in light theme.
* Apply shadows only to overlays such as menus, popovers, dialogs, and toasts. Do not shadow static page sections or metric groups.

### Icons

* Use Lucide as the only general-purpose icon set. Select icons by their official Lucide names.
* Preserve the Lucide 24×24 view box, 2px stroke, round caps, round joins, no fill, and `currentColor`. Scale the entire SVG uniformly.
* Render standard control icons at 16×16px, compact inline icons at 14×14px, and utility-control icons at 16–18px. Do not vary stroke width to compensate for size.
* Import, tree-shake, or embed only used icons. When vendoring SVG paths, retain the ISC notice in [assets/LUCIDE-LICENSE.txt](assets/LUCIDE-LICENSE.txt).
* Do not substitute Unicode characters, emoji, hand-drawn SVGs, or icons from another pack when Lucide contains the concept.
* Use a custom icon only when Lucide has no matching domain concept; construct it on the same 24×24 grid with the same stroke treatment.
* Set decorative SVGs to `aria-hidden="true"` and `focusable="false"`. Give an icon-only button an accessible name and a tooltip containing the same action label.
* Do not add an icon to an inline validation message or status callout when visible text, semantic color, and severity geometry already identify the state.

### Page Shell and Component Sections

#### Interactive Page Utilities

* On every page containing interactive controls, navigation, or commands, place a utility group at the upper right. Put the search launcher immediately left of the theme toggle with an 8px gap.
* Size the search launcher to 72×36px. Give it a 1px `--line` border, `--surface` background, 6px radius, and 10px horizontal padding. Place a 15×15px Lucide `Search` icon at the left inset. Leave the rest of the launcher empty.
* Give the search launcher the accessible name `Open search and commands`. Do not render `Search page`, `Ctrl+K`, `⌘K`, or any other visible text inside or beside it.
* Size the theme toggle to 36×36px. Use a Lucide `Sun` or `Moon` icon. Keep the toggle background, border, and shadow transparent in every state; change only icon color or opacity on hover and active states.
* At viewport widths above 600px, position the utility group 20px from the top and 24px from the right. At 600px and below, use 12px top and right offsets and reserve at least 68px above page content.

#### Spotlight Search and Commands

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

#### Component Section Construction

* Wrap each top-level component example in a semantic `<section>` with a unique `id` and `aria-labelledby` pointing to its heading.
* Use one `h2` as the visible heading. Format it as a two-digit ordinal, one space, and the specific component name, for example `01 Buttons & Actions`.
* Put the rendered component content immediately after the heading. Use a 20px gap between heading and content and 56px between top-level sections.
* Do not place a slash, em dash, colon, or decorative separator between the ordinal and component name.
* Do not render `Component`, `Components`, `Specification`, `Variants`, `States`, `States: Disabled & Loading`, or another showcase taxonomy label.
* Do not render a subtitle, description, summary sentence, or horizontal rule beneath the section heading.
* Do not add a border, background fill, radius, or shadow to a page section or example wrapper solely to distinguish it from adjacent sections. Use the heading and spacing for separation.
* Preserve a border or fill only when it is part of the demonstrated component itself, such as an input boundary, menu, table, dialog, chart, alert, or toast.
* Build every example so its visible labels, values, and controls identify the component and its state without explanatory prose.

#### Page Layout

* On desktop, cap content at 1240px, center it, use 32px top, 24px horizontal, and 80px bottom padding, and use a 240px navigation column plus one flexible content column separated by 40px.
* Collapse to one column at 900px and below. Hide the component index at that breakpoint unless a mobile navigation replacement is implemented.
* Set content columns and flex children to `min-width: 0` so long labels cannot force page overflow.
* Mark the active side-navigation row with `--ink-strong`, weight 600, and an optional neutral surface. Do not add a leading stripe or border.
* When side navigation scrolls, add at least 4px of inner space on every clipped edge around its links. A 2px focus outline with a 2px offset must remain fully visible on the first, last, leftmost, and rightmost focused link; never let an overflow container crop it.

### 1. Buttons and Action Triggers

* Use a native `<button>` and always set `type="button"`, `type="submit"`, or `type="reset"` explicitly.
* Use 14px text, weight 500, line-height 1, 8px internal gap, 9px vertical padding, 16px horizontal padding, a 1px transparent base border, and a 6px radius for non-danger text buttons.
* Primary: use solid `--action-primary` fill, a transparent 1px border, `--ink-strong` text, and weight 600. On hover, use `--action-primary-hover`. Keep the fill visibly distinct from `--surface` form controls and do not add an input-like outline. Place at most one primary action in an action group.
* Secondary: use a transparent fill, 1px `--line` border, and `--ink` text. On hover, use `--surface-raised` and `--line-strong`.
* Quiet: use no border, transparent fill, and `--muted` text. On hover, use `--ink-strong` text and `--surface-raised`.
* Danger: use `--danger-soft` fill, a danger-tinted 1px border, `--danger` text, and 0px radius. On hover, use `--danger` fill and white text. Every red button remains sharp-edged.
* Delayed danger: for a high-consequence destructive action, keep the danger button inert until the user deliberately engages it for 1 second. Treat uninterrupted pointer hover, keyboard focus, and touch press-and-hold as equivalent engagement paths. Animate a solid `--danger` fill from left to right over exactly 1000ms while preserving a readable action label; use white text over the filled portion. Use `cursor: not-allowed` while the button is unarmed or arming, then change to `cursor: pointer` only when it becomes armed. Reset the fill and disarm immediately on pointer exit, blur, touch cancellation, or touch movement outside the button. Block click, `Enter`, and `Space` until armed; after arming, permit one activation and then reset. Keep the native button focusable, describe the interaction with `aria-describedby`, and announce the arming and ready states through a polite live region. Under `prefers-reduced-motion: reduce`, show the same one-second progress in four discrete fill steps.
* Icon-only: use a 36×36px visual button with at least a 44×44px touch target. Keep its background, border, and shadow transparent in default, hover, and active states. Use icon color or opacity for hover feedback.
* On pointer activation, scale an enabled button to 0.98 while pressed. Do not apply the scale to disabled buttons.
* For native disabled controls, set `disabled`. For custom disabled controls, set `aria-disabled="true"`, block pointer and keyboard activation, use opacity 0.45, and use `cursor: not-allowed`.
* Before entering a loading state, preserve the button's rendered width as `min-width`. Set `aria-busy="true"`, disable repeated activation, and show a 14×14px neutral spinner with an action-specific present-participle label such as `Saving…`. Restore the final or idle state without changing width.
* Show the global focus-visible ring on every enabled button.

### 2. Editable Dropdowns and Menus

#### Anatomy

* Do not use a browser-default `<select>` for these dropdowns.
* Include a visible label, an editable text input, a right-aligned Lucide `ChevronDown`, a popover listbox, options, an empty-result row, and a form value synchronized to the committed selection.
* Give the input `role="combobox"`, `aria-autocomplete="list"`, `aria-haspopup="listbox"`, `aria-expanded`, and `aria-controls`. Point `aria-activedescendant` to the active option while one exists.
* Give the popover `role="listbox"`. Give every selectable row a stable unique `id`, `role="option"`, and accurate `aria-selected`.
* Keep DOM focus in the text input while the listbox is open. Do not move focus into option rows.

#### Geometry and Content

* Use 14px input text, 9px vertical padding, 14px left padding, 36px right padding, a 1px `--line` border, `--surface` fill, and 6px radius.
* Position the 14×14px chevron 13px from the right edge. Rotate it 180 degrees while expanded.
* Compute width from the larger of the current input or placeholder width plus 52px and the widest ordinary option label plus 28px. Clamp the result from 176px through 232px and cap it at the available viewport width.
* Match the popover width to the field. Place it 6px below the field and cap height at 240px with vertical scrolling. Use 2px internal padding, `--surface-raised`, a 1px `--line-strong` border, 6px radius, and the large overlay shadow.
* Render pointer-dense option rows at 13.5px, line-height 1.35, minimum height 28px, 3px vertical padding, and 10px horizontal padding. At coarse-pointer breakpoints, increase rows to at least 44px high with 10px vertical padding.
* Mark the selected option with `--ink-strong` and weight 600. Do not render a checkmark and do not reserve a trailing checkmark gutter.
* Wrap option labels at spaces with normal word breaking. Use overflow wrapping only when one token is too long to fit. Do not use `word-break: break-all`.

#### Filtering and Commit Rules

* Filter options on every input event with trimmed, locale-lowercased, case-insensitive substring matching. Announce the number of available options through a polite live region.
* Open the popover when the input is activated or edited. When no fixed option matches, render `No matching options` in list-only mode.
* Configure each dropdown explicitly as list-only or free-entry.
* In list-only mode, commit only an exact option. On `Enter`, commit the active option. On `Escape`, outside click, `Tab`, or window blur with unmatched text, restore the last committed label and value.
* In free-entry mode, when non-empty text has no exact match, add one selectable custom option containing only the entered text in quotation marks. Do not prefix it with `Use`, `Create`, or another instruction. `Enter`, `Tab`, or outside dismissal commits the trimmed custom value according to the product's form policy.
* `ArrowDown` and `ArrowUp` move the active option and scroll it into view. `Enter` commits it. `Escape` restores the prior committed state and closes. Preserve normal cursor movement, deletion, selection, and composition behavior for all other text-editing input.
* Synchronize the visible committed label, submitted value, selected class, and `aria-selected` state in one operation. Dispatch one change event only when the committed value changes.

### 3. Form Inputs, Search Fields, and Textareas

* Pair every form control with a visible `<label>` whose `for` matches the control `id`. Do not use a placeholder as the label.
* Use 13.5px label text, weight 500, and `--ink-strong`. Put 6px between label, control, and associated message.
* Mark required controls with visible `(required)` text or an `aria-hidden="true"` symbol plus required state available in the accessible name. Set the control's native `required` attribute when submission requires it.
* Use `--surface`, a 1px `--line` border, 6px radius, 9px vertical padding, 12px horizontal padding, and 14px text for one-line inputs.
* Use `--mono` at 13px for code, tokens, API keys, and machine identifiers. Do not use monospace for ordinary names, email addresses, search terms, or prose.
* Give textareas the same border and typography, a minimum height of 96px, and vertical-only resizing unless the surrounding layout supports both axes.
* On hover, change only the border to `--line-strong`. On focus, keep a strong border and render the global 2px focus ring.
* On disabled controls, set the native `disabled` attribute, opacity 0.5, `cursor: not-allowed`, and `--canvas` fill.
* Add helper text only for a non-obvious format, constraint, consequence, or recovery step and associate it through `aria-describedby`. Do not include copy that merely restates the label, value, editability, data type, or organizational scope. Specifically omit generic lines such as `Unique identifier within your organization` and `Read-only live credentials token`.
* For an invalid field, set `aria-invalid="true"`, point `aria-describedby` to its message, use a 1px `--danger` border, and set radius to 0px. Render the correction beneath the field in 12px `--danger` text with `role="alert"`.
* Do not prepend an error icon. Write one direct corrective instruction and omit its trailing period. For an email format error, use `Enter a valid email address`.
* For email validation triggered by a button, wrap one `type="email"` input and a secondary submit button labeled `Validate Email` in a form with `novalidate`. Put the button beside the input with an 8px gap, stack it beneath the input at 600px and below, and run the same validation path when the user presses `Enter`. Do not show a validation result on initial render or blur.
* On submit, use the input's native validity state. For an empty required email, show `Enter an email address`; for an invalid format, show `Enter a valid email address`. Apply the invalid-field treatment, focus the input, and expose the correction through `aria-describedby` and `role="alert"`. On subsequent input, clear the error and return the field to its neutral state without revalidating.
* For a valid email, keep the input visually neutral and make it read-only for the duration of submission. Preserve the submit button's rendered width as `min-width`, set `aria-busy="true"`, disable repeated activation, and replace its label with a 14×14px neutral spinner and `Submitting…`. Do not turn the input green or show inline success text. When submission succeeds, show an operation-specific success toast, clear the input, restore its editability, restore the button's label and state, and return focus to the control that initiated submission.
* Keep ordinary text inputs free of a leading icon.
* Put a 15×15px Lucide `Search` icon 11px from the left edge of every search input and increase left padding to 36px. The icon, not placeholder wording, distinguishes search from ordinary text entry.
* When a search field is clearable, show a borderless and backgroundless Lucide `X` button at the right only while the field contains a value. Give the input 36px right padding. Activating clear empties the value, dispatches an input event, hides the button, preserves focus in the field, and updates filtered results immediately.

### 4. Selection Controls

* Use native checkbox and radio inputs for state and form submission. A visually hidden native input must remain focusable and must receive the global focus-visible treatment on its visible companion.
* Render checkboxes at 18×18px with a 1px `--line-strong` border, `--surface` fill, and 4px radius. Checked and indeterminate states use tonal `--control-selected` fill and border with a 14×14px Lucide `Check` or `Minus` in `--canvas`; use `--control-selected-hover` while the selected row is hovered. Do not use `--ink-strong` as the selected fill.
* Render radio controls at 18×18px with a circular `--line-strong` border. Use `--control-selected` for both the selected border and the 8×8px selected dot, and `--control-selected-hover` for both while the selected row is hovered. Do not use `--ink-strong` for the selected dot.
* Wrap related radios in `<fieldset>` and provide a visible `<legend>`.
* Render switches with a 38×22px pill track and a 16×16px circular thumb inset 3px. Use `--line` for the off track with a `--muted` thumb; on hover, use `--line-strong` for the off track. Use `--control-selected` for the on track with a `--canvas` thumb, use `--control-selected-hover` while the on row is hovered, and translate the thumb 16px when on. Do not use `--ink-strong` for the track or thumb.
* Use a checkbox with `role="switch"` and maintain `aria-checked` only when native checked semantics do not already expose the required switch role.
* Put the input and visible label in one clickable row with a 10px gap and a minimum 44px target height. Clicking the text toggles the control.
* Support `Space` for checkboxes and switches and arrow-key movement within a native radio group.
* Set the native `disabled` attribute on disabled checkboxes, radios, and switches. Preserve whether the control is checked or unchecked, apply opacity 0.45 and `cursor: not-allowed` to its visible indicator and label, suppress every hover treatment, and prevent toggling. In a complete selection-control reference, show a disabled example of all three control types and include both checked and unchecked disabled states across the set.

### 5. Labels, Badges, and Metadata

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

### 6. Tables and Data Grids

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

### 7. Charts and Data Graphics

* Render line and area charts as SVG with a responsive width, a 200px default plot height, neutral `--line` gridlines, and 11px `--mono` axis labels in `--muted`.
* Include units in axis titles or every applicable tick label. Do not require the surrounding prose to identify a unit.
* Use semantic color only to distinguish real series or provenance. Pair each series color with a unique solid, dashed, or dotted stroke and, when points are selectable, a distinct marker shape.
* Label every legend entry with the full series name. Match its line sample to the rendered stroke pattern.
* On pointer hover or keyboard focus of a point, show a crosshair and a neutral floating tooltip containing the exact timestamp, series names, values, and units. Keep the tooltip inside the chart's visible bounds.
* Give the graphic an accessible name and concise summary. Follow it with an expandable `<details>` table containing every plotted timestamp and exact value; keep the table data generated from the same source as the SVG.
* Do not encode observed and modeled data with color alone. Use `--accent` with a solid stroke for observed data and `--derived` with a dashed stroke for modeled data by default.

### 8. Dialogs and Modals

* Use the native `<dialog>` element with `showModal()` where supported. Give it `aria-labelledby` pointing to a visible title and use `aria-describedby` when body copy is present.
* Use `--surface-raised`, a 1px `--line-strong` border, 6px radius, the large overlay shadow, 24px padding, `min(480px, 90vw)` width, and no viewport-edge overflow.
* Use a neutral backdrop at `rgba(0, 0, 0, 0.65)`. Apply up to 4px backdrop blur only when the runtime supports it without degrading scrolling.
* On open, save the invoking element and focus the first task-specific interactive control. If none exists, focus the close button. Keep `Tab` and `Shift+Tab` inside the dialog.
* `Escape` closes a dismissible dialog. Closing by any method restores focus to the invoking element without scrolling it out of place.
* Use an 18px, weight-600 title. Put 16px below the header, 24px below the body, and 12px between footer actions.
* Align footer actions to the right. Place cancel before confirm in DOM and visual order. Use the danger-button treatment with 0px radius for a destructive confirmation.
* Give every icon-only close control a Lucide `X`, accessible name `Close`, and tooltip. Keep its background and border transparent.

### 9. Tooltips and Popovers

* Use a tooltip only for non-interactive explanatory text attached to an icon-only control or a visually truncated value.
* Show it after 150ms on both pointer hover and keyboard focus. Hide it on pointer exit, blur, or `Escape`.
* Give the bubble `role="tooltip"`, a stable `id`, and connect the trigger with `aria-describedby` while it is visible.
* Use 12px text, 5px vertical and 10px horizontal padding, `--surface-raised`, `--ink-strong`, a 1px `--line-strong` border, 4px radius, and the small overlay shadow.
* Keep tooltip content on one line only while it fits within the viewport. Constrain long content and allow normal word wrapping.
* Set tooltip pointer events to none. Do not place links, buttons, inputs, or other interactive content inside it.
* Use a popover for interactive floating content. Move focus into it when required by its task, support expected arrow-key behavior for menus, close on `Escape` and outside activation, and restore focus to its trigger.

### 10. Navigation, Tabs, and Breadcrumbs

* For side navigation, use a semantic `<nav>` with an accessible name and list markup. Mark the current link with `aria-current="page"`.
* Render side-navigation links at 13.5px with 5px vertical and 8px horizontal padding and 4px radius. Use `--muted` by default and `--ink-strong`, weight 600, and an optional `--surface-raised` fill for the current row. Do not add a white or colored stripe to the current row.
* Give a tab container `role="tablist"`. Give each tab `role="tab"`, a matching `aria-controls`, and accurate `aria-selected`. Give each panel `role="tabpanel"` and `aria-labelledby`.
* Use roving tab stops: the active tab has `tabindex="0"`; all other tabs have `tabindex="-1"`.
* Render tabs at 14px with 10px vertical and 4px horizontal padding and 20px between tabs. Use a 1px `--line` rule under the list and a 2px `--ink-strong` underline on the selected tab.
* `ArrowRight` and `ArrowLeft` move focus and selection cyclically. `Home` selects the first tab and `End` selects the last. Hide inactive panels with the `hidden` attribute.
* Use `<nav aria-label="Breadcrumb">` containing an ordered list for breadcrumbs. Mark the current item with `aria-current="page"` and do not link it.
* Render breadcrumb separators as neutral `/` characters hidden from assistive technology. These separators are allowed only in breadcrumb trails, not component section titles.

### 11. Metrics, Cards, and Panels

* For a metric group, render only a metric label and value. Put required units, scope, source, or time range into the label or value.
* Do not render a descriptive or supporting subline beneath the value.
* Keep each metric group transparent, borderless, shadowless, and without container padding. Do not use a card background to separate neighboring metrics.
* Lay out three metrics as equal columns with a 32px gap. At 600px and below, use one column with a 24px gap.
* Set metric labels in `--mono` at 12px, uppercase, letter-spacing 0.08em, and `--muted`. Set metric values at 26px, weight 700, line-height 1, `--muted`, and tabular numerals. Let size and weight establish hierarchy; do not use `--ink-strong` for large metric values.
* Add a bordered or filled card only for a real interactive, independently stateful, draggable, selectable, collapsible, or semantically bounded object. Do not add one for page-section separation.
* When a real card boundary is required, use `--surface`, a 1px `--line` border, 6px radius, and no shadow unless the card is temporarily elevated during drag.

### 12. Alerts, Callouts, and Toasts

* Start every inline callout with a visible weight-600 status label followed by its message, such as `Telemetry Delay:` or `Outage Detected:`.
* Do not prepend an info, warning, error, or success icon to an inline callout. Do not reserve an icon column.
* Use 13.5px text, line-height 1.5, 14px vertical padding, and 16px horizontal padding.
* Info callout: use `--surface-raised`, a 1px `--line-strong` border, `--ink`, and 6px radius.
* Caution callout: use `--caution-soft`, a 35%-mixed caution border, `--ink`, and 4px radius.
* Danger callout: use `--danger-soft`, a 35%-mixed danger border, `--ink`, and 0px radius.
* Success callout: use `--good-soft`, a 35%-mixed good border, `--ink`, and 6px radius.
* Position toast stacks 24px from the bottom and right with 10px between toasts. At narrow mobile widths, use 12px side offsets and cap each toast to the available width.
* Size a toast from 280px through 360px, use 12px vertical and 16px horizontal padding, a 1px `--line-strong` border, `--surface-raised`, and the large overlay shadow.
* Use 6px radius for neutral or success toasts, 4px for caution toasts, and 0px for danger toasts.
* Animate toast entrance over 280ms with `cubic-bezier(0.16, 1, 0.3, 1)`, moving from 16px below its resting position while fading from transparent to opaque. Remove the entrance animation when `prefers-reduced-motion: reduce` matches.
* Put an operation-specific message and optional neutral `Undo` action in each toast. Make the message area a native button that opens toast history and keep `Undo` as its sibling, never as a nested interactive element. If a status icon is included, use Lucide and apply the same severity geometry; use an angular icon for danger.
* Announce non-urgent toasts through `role="status"` or `aria-live="polite"`. Use `role="alert"` only when immediate interruption is required.
* Give a non-critical toast a 4–6 second live action window. Pause the remaining countdown on hover and whenever focus is inside the toast, then resume from the remaining duration. When the window ends, remove transient actions such as `Undo` and archive the toast into the corner tray instead of deleting it. Do not archive a critical error that still requires action.
* Recede an archived toast toward the bottom-right corner over 620ms, lowering it to 20% opacity, translating it 18px right and 42px down until it is partly clipped by the viewport, and reducing its shadow. Keep the tray's corner hit area stationary so revealing it does not break hover. Under reduced motion, apply the archived end state without a transition.
* While the archived tray is hovered or contains keyboard focus, restore its toasts to their resting positions and 86% opacity, expand the tray to at most 320px or the available viewport height, and enable vertical scrolling with contained overscroll. At 600px and below, keep 12px side padding, use the available width, and leave a tappable portion of the resting tray visible.
* Open the full toast history when the user clicks a toast's message area or activates it with the keyboard. Use a labelled modal dialog with a visible close button, list every toast from the current page session newest first, and show each message, severity, and creation timestamp. Render the visible local date and time in a semantic `<time>` whose `datetime` contains the exact ISO timestamp; return focus to the originating toast when the dialog closes.

### 13. Color Semantics

* Add a `Color Semantics` section to every complete component reference. Show every shared color token as a swatch with its exact token name, one short usage label, and both dark- and light-theme values; do not rely on a prose introduction to explain the palette.
* Include `--canvas`, `--surface`, `--surface-raised`, `--ink`, `--ink-strong`, `--muted`, `--faint`, `--line`, `--line-strong`, `--action-primary`, `--action-primary-hover`, `--control-selected`, `--control-selected-hover`, `--focus`, `--accent`, `--good`, `--caution`, `--danger`, and `--derived`. Show each semantic token's soft tint in the same swatch and label the tint opacity beside each theme value.
* Lay tokens out in a responsive grid with a 240px minimum column width, 32px column gaps, and 24px row gaps. At narrow widths, collapse to one column without horizontal overflow.
* Build each token as a 44px swatch beside a text block. Set the token name in 12px semibold `--mono`, the usage label in 13px `--muted`, and theme values in 10.5px `--faint` monospace text that may wrap only between complete values.
* Keep every swatch and soft-tint region borderless, dividerless, outlineless, and shadowless; the color fill itself provides the shape. Use a 6px default radius, keep good swatches at 6px, caution swatches at 4px, and danger swatches at 0px; never round a red swatch.
* Use `Viewport background`, `Controls and bounded surfaces`, `Overlays and active rows`, `Body text and data values`, `Headings and selected values`, `Secondary metadata and labels`, `Placeholders and inactive icons`, `Borders and row dividers`, `Active and overlay boundaries`, `Primary action fill`, `Hovered primary action fill`, `Selected control fill`, `Hovered selected control fill`, `Keyboard focus only`, `Observed data`, `Healthy or successful`, `Warning or degraded`, `Error, outage, or destructive`, and `Estimated, modeled, or synthetic` as the corresponding usage labels in token order.

## Reference Asset

* Open [assets/component-reference.html](assets/component-reference.html)
