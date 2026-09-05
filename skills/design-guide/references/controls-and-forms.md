# Controls and Forms

## 1. Buttons and Action Triggers

* Use a native `<button>` and always set `type="button"`, `type="submit"`, or `type="reset"` explicitly.
* Use 14px text, weight 500, line-height 1, 8px internal gap, 9px vertical padding, 16px horizontal padding, a 1px transparent base border, and a 6px radius for non-danger text buttons.
* Primary: use solid `--action-primary` fill, a transparent 1px border, `--ink-strong` text, and weight 600. On hover, use `--action-primary-hover`. Keep the fill visibly distinct from `--surface` form controls and do not add an input-like outline. Place at most one primary action in an action group.
* Secondary: use a transparent fill, a very faint 1px border made with `color-mix(in srgb, var(--line) 25%, transparent)`, and `--ink` text. On hover, use `--surface-raised` and keep the same faint border color. Retain the global keyboard focus ring.
* Quiet: use no border, transparent fill, and `--muted` text. On hover, use `--ink-strong` text and `--surface-raised`.
* Danger: use `--danger-soft` fill, a transparent 1px border, `--danger` text, and 0px radius. On hover, use `--danger` fill and white text while keeping the border transparent. Every red button remains sharp-edged.
* Delayed danger: for a high-consequence destructive action, keep the danger button inert until the user deliberately engages it for 1 second. Treat uninterrupted pointer hover, keyboard focus, and touch press-and-hold as equivalent engagement paths. Animate a solid `--danger` fill from left to right over exactly 1000ms while preserving a readable action label; use white text over the filled portion. Use `cursor: not-allowed` while the button is unarmed or arming, then change to `cursor: pointer` only when it becomes armed. Reset the fill and disarm immediately on pointer exit, blur, touch cancellation, or touch movement outside the button. Block click, `Enter`, and `Space` until armed; after arming, permit one activation and then reset. Keep the native button focusable, describe the interaction with `aria-describedby`, and announce the arming and ready states through a polite live region. Under `prefers-reduced-motion: reduce`, show the same one-second progress in four discrete fill steps.
* Icon-only: use a 36×36px visual button with at least a 44×44px touch target. Keep its background, border, and shadow transparent in default, hover, and active states. Use icon color or opacity for hover feedback.
* On pointer activation, scale an enabled button to 0.98 while pressed. Do not apply the scale to disabled buttons.
* For native disabled controls, set `disabled`. For custom disabled controls, set `aria-disabled="true"`, block pointer and keyboard activation, use opacity 0.45, and use `cursor: not-allowed`.
* Before entering a loading state, preserve the button's rendered width as `min-width`. Set `aria-busy="true"`, disable repeated activation, and show a 14×14px neutral spinner with an action-specific present-participle label such as `Saving…`. Restore the final or idle state without changing width.
* Show the global focus-visible ring on every enabled button.

## 2. Editable Dropdowns and Menus

### Anatomy

* Do not use a browser-default `<select>` for these dropdowns.
* Include a visible label, an editable text input, a right-aligned Lucide `ChevronDown`, a popover listbox, options, an empty-result row, and a form value synchronized to the committed selection.
* Give the input `role="combobox"`, `aria-autocomplete="list"`, `aria-haspopup="listbox"`, `aria-expanded`, and `aria-controls`. Point `aria-activedescendant` to the active option while one exists.
* Give the popover `role="listbox"`. Give every selectable row a stable unique `id`, `role="option"`, and accurate `aria-selected`.
* Keep DOM focus in the text input while the listbox is open. Do not move focus into option rows.

### Geometry and Content

* Use 14px input text, 9px vertical padding, 14px left padding, 36px right padding, a transparent 1px border, `--surface` fill, and 6px radius. Keep the field borderless on hover and focus; use `--surface-raised` on hover and retain the global keyboard focus ring.
* Position the 14×14px chevron 13px from the right edge. Rotate it 180 degrees while expanded.
* Compute width from the larger of the current input or placeholder width plus 52px and the widest ordinary option label plus 28px. Clamp the result from 176px through 232px and cap it at the available viewport width.
* Match the popover width to the field. Place it 6px below the field and cap height at 240px with vertical scrolling. Use 2px internal padding, `--surface-raised`, a transparent 1px border, 6px radius, and the large overlay shadow.
* Render pointer-dense option rows at 13.5px, line-height 1.35, minimum height 28px, 3px vertical padding, and 10px horizontal padding. At coarse-pointer breakpoints, increase rows to at least 44px high with 10px vertical padding.
* Mark the selected option with `--ink-strong` and weight 600. Do not render a checkmark and do not reserve a trailing checkmark gutter.
* Wrap option labels at spaces with normal word breaking. Use overflow wrapping only when one token is too long to fit. Do not use `word-break: break-all`.

### Filtering and Commit Rules

* Filter options on every input event with trimmed, locale-lowercased, case-insensitive substring matching. Announce the number of available options through a polite live region.
* Open the popover when the input is activated or edited. When no fixed option matches, render `No matching options` in list-only mode.
* Configure each dropdown explicitly as list-only or free-entry.
* In list-only mode, commit only an exact option. On `Enter`, commit the active option. On `Escape`, outside click, `Tab`, or window blur with unmatched text, restore the last committed label and value.
* In free-entry mode, when non-empty text has no exact match, add one selectable custom option containing only the entered text in quotation marks. Do not prefix it with `Use`, `Create`, or another instruction. `Enter`, `Tab`, or outside dismissal commits the trimmed custom value according to the product's form policy.
* `ArrowDown` and `ArrowUp` move the active option and scroll it into view. `Enter` commits it. `Escape` restores the prior committed state and closes. Preserve normal cursor movement, deletion, selection, and composition behavior for all other text-editing input.
* Synchronize the visible committed label, submitted value, selected class, and `aria-selected` state in one operation. Dispatch one change event only when the committed value changes.

## 3. Form Inputs, Search Fields, and Textareas

* Pair every form control with a visible `<label>` whose `for` matches the control `id`. Do not use a placeholder as the label.
* Use 13.5px label text, weight 500, and `--ink-strong`. Put 6px between label, control, and associated message.
* Mark required controls with visible `(required)` text or an `aria-hidden="true"` symbol plus required state available in the accessible name. Set the control's native `required` attribute when submission requires it.
* Use `--surface`, a transparent 1px border, 6px radius, 9px vertical padding, 12px horizontal padding, and 14px text for one-line inputs.
* Use `--mono` at 13px for code, tokens, API keys, and machine identifiers. Do not use monospace for ordinary names, email addresses, search terms, or prose.
* Give textareas the same border and typography, a minimum height of 96px, and vertical-only resizing unless the surrounding layout supports both axes.
* Let form groups and their inputs shrink to the available width with `min-width: 0`; a preferred field width must not become a page-width minimum.
* Keep neutral fields borderless on hover and focus. On hover, change the fill to `--surface-raised`. On focus, render the shared 2px `--focus` ring. Preserve the danger border on invalid fields in both states.
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

## 4. Selection Controls

* Use native checkbox and radio inputs for state and form submission. A visually hidden native input must remain focusable and must receive the global focus-visible treatment on its visible companion.
* Render checkboxes at 18×18px with a 1px `--line-strong` border, `--surface` fill, and 4px radius. Checked and indeterminate states use tonal `--control-selected` fill and border with a 14×14px Lucide `Check` or `Minus` in `--canvas`; use `--control-selected-hover` while the selected row is hovered. Do not use `--ink-strong` as the selected fill.
* Render radio controls at 18×18px with a circular `--line-strong` border. Use `--control-selected` for both the selected border and the 8×8px selected dot, and `--control-selected-hover` for both while the selected row is hovered. Do not use `--ink-strong` for the selected dot.
* Wrap related radios in `<fieldset>` and provide a visible `<legend>`.
* Render switches with a 38×22px pill track and a 16×16px circular thumb inset 3px. Use `--line` for the off track with a `--muted` thumb; on hover, use `--line-strong` for the off track. Use `--control-selected` for the on track with a `--canvas` thumb, use `--control-selected-hover` while the on row is hovered, and translate the thumb 16px when on. Do not use `--ink-strong` for the track or thumb.
* Use a checkbox with `role="switch"` and maintain `aria-checked` only when native checked semantics do not already expose the required switch role.
* Put the input and visible label in one clickable row with a 10px gap and a minimum 44px target height. Clicking the text toggles the control.
* Let long selection labels wrap within the available width while keeping the checkbox, radio, or switch indicator at its specified size.
* Support `Space` for checkboxes and switches and arrow-key movement within a native radio group.
* Set the native `disabled` attribute on disabled checkboxes, radios, and switches. Preserve whether the control is checked or unchecked, apply opacity 0.45 and `cursor: not-allowed` to its visible indicator and label, suppress every hover treatment, and prevent toggling. In a complete selection-control reference, show a disabled example of all three control types and include both checked and unchecked disabled states across the set.
