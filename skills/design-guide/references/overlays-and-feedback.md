# Overlays and Feedback

## 8. Dialogs and Modals

* Use the native `<dialog>` element with `showModal()` where supported. Give it `aria-labelledby` pointing to a visible title and use `aria-describedby` when body copy is present.
* Use `--surface-raised`, a 1px `--line-strong` border, 6px radius, the large overlay shadow, 24px padding, `min(480px, 90vw)` width, and no viewport-edge overflow.
* Use a neutral backdrop at `rgba(0, 0, 0, 0.65)`. Apply up to 4px backdrop blur only when the runtime supports it without degrading scrolling.
* On open, save the invoking element and focus the first task-specific interactive control. If none exists, focus the close button. Keep `Tab` and `Shift+Tab` inside the dialog.
* `Escape` closes a dismissible dialog. Closing by any method restores focus to the invoking element without scrolling it out of place.
* Use an 18px, weight-600 title. Put 16px below the header, 24px below the body, and 12px between footer actions.
* Align footer actions to the right. Place cancel before confirm in DOM and visual order. Use the danger-button treatment with 0px radius for a destructive confirmation.
* Give every icon-only close control a Lucide `X`, accessible name `Close`, and tooltip. Keep its background and border transparent.

## 9. Tooltips and Popovers

* Use a tooltip only for non-interactive explanatory text attached to an icon-only control or a visually truncated value.
* Show it after 150ms on both pointer hover and keyboard focus. Hide it on pointer exit, blur, or `Escape`.
* Give the bubble `role="tooltip"`, a stable `id`, and connect the trigger with `aria-describedby` while it is visible.
* Use 12px text, 5px vertical and 10px horizontal padding, `--surface-raised`, `--ink-strong`, a 1px `--line-strong` border, 4px radius, and the small overlay shadow.
* Keep tooltip content on one line only while it fits within the viewport. Constrain long content and allow normal word wrapping.
* Set tooltip pointer events to none. Do not place links, buttons, inputs, or other interactive content inside it.
* Use a popover for interactive floating content. Move focus into it when required by its task, support expected arrow-key behavior for menus, close on `Escape` and outside activation, and restore focus to its trigger.

## 10. Navigation, Tabs, and Breadcrumbs

* For side navigation, use a semantic `<nav>` with an accessible name and list markup. Mark the current link with `aria-current="page"`.
* Render side-navigation links at 13.5px with 5px vertical and 8px horizontal padding and 4px radius. Use `--muted` by default and `--ink-strong`, weight 600, and an optional `--surface-raised` fill for the current row. Do not add a white or colored stripe to the current row.
* Give a tab container `role="tablist"`. Give each tab `role="tab"`, a matching `aria-controls`, and accurate `aria-selected`. Give each panel `role="tabpanel"` and `aria-labelledby`.
* Use roving tab stops: the active tab has `tabindex="0"`; all other tabs have `tabindex="-1"`.
* Render tabs at 14px with 10px vertical and 4px horizontal padding and 20px between tabs. Use a 1px `--line` rule under the list and a 2px `--ink-strong` underline on the selected tab.
* `ArrowRight` and `ArrowLeft` move focus and selection cyclically. `Home` selects the first tab and `End` selects the last. Hide inactive panels with the `hidden` attribute.
* Use `<nav aria-label="Breadcrumb">` containing an ordered list for breadcrumbs. Mark the current item with `aria-current="page"` and do not link it.
* Render breadcrumb separators as neutral `/` characters hidden from assistive technology. These separators are allowed only in breadcrumb trails, not component section titles.

## 12. Alerts, Callouts, and Toasts

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
