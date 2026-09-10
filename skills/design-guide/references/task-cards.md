# Task Cards

Use these cards for independently identifiable tasks or issues whose project, title, state, and deadline need to be scanned together. They provide the compact, high-density record layout used by the Linear desktop overlay and queues or task grids in a dashboard. The [Cards & Panels example](../assets/component-reference/index.html#cards) keeps the existing metric groups and presents six fictional task cards, with dates relative to 05 September 2026.

## Layout and Content

- Use a grid with no horizontal or vertical gaps and as many columns as fit at a preferred minimum width of 252px. Below that width, let a single card shrink to the container. Use `repeat(auto-fit, minmax(min(100%, 252px), 1fr))` and `gap: 0`; do not create page-level horizontal scrolling.
- Give each card 6px vertical and 12px horizontal padding, a 50px compact height, no border, and no shadow. The layout uses two rows separated by 2px (`grid-template-rows: 16px 20px`): upstairs header metadata and task title.
- **Upstairs metadata row (16px line-height):**
  - Left-aligned source group: displays the project name/acronym, a separator dot (`·`), and the issue ID in 11px text and `--muted`, with 4px gaps. The ID renders in `--mono`. An optional project icon/emoji may precede the project name. The project name truncates with an ellipsis if space is constrained; keep the icon, separator, and issue ID from shrinking.
  - Right-aligned state text: displays urgency states (`Urgent`, `Today`, `Blocked`) or deadline text (`Today`, `Tomorrow`, `08 Sep`) in 11px text with weight 500 colored by tone. Omit the redundant `Due` prefix; combine urgency and a date as `Urgent · 08 Sep` when both apply. Separate it from the source group by 8px, cap it at 55% of the header width, and ellipsize overflow. Explicit workflow state labels (`In progress`, `Todo`) and `Done` labels are omitted from the preview header; retain the actual state in `data-state`, including `Due today` when the visible label is `Today`.
- **Title and progress contrast row (20px line-height):**
  - Single-line title starting at 15px UI text (weight 500) that progressively steps down to 14px, then 13px when overflowing before truncating with an ellipsis.
  - Title contrast is independent of the card's fill tone. `In progress`, `Due today`, and `Overdue` use `--ink` in either theme; other states, including `Todo`, `Done`, `Backlog`, `Urgent`, and `Blocked`, use the default `--muted`. The styles also recognize the corresponding hyphenated `data-state` values and `is-in-progress`, `is-due-today`, and `is-overdue` classes; `data-workflow="in-progress"` enables the active title treatment independently of the header label.
- Use the full card's translucent soft fill and its visible state text together. Apply opacity to the background color only: neutral fills use 50% opacity and semantic tints 7%, with the surrounding page showing through. Keep title and metadata opacity independent of the fill. Omit decorative rails, inset frames, separator rules, status dots, and duplicate state badges inside the card.

## State Treatments

| `data-tone` | Fill | State text | Radius | Typical use |
| --- | --- | --- | --- | --- |
| `neutral` or omitted | `color-mix(in srgb, var(--surface) 50%, transparent)` | `--muted` | 6px | Todo, in progress, ordinary workflow state |
| `caution` | `color-mix(in srgb, var(--caution) 7%, transparent)` | `--caution` | 4px | Due soon or elevated priority |
| `danger` | `color-mix(in srgb, var(--danger) 7%, transparent)` | `--danger` | 0px | Overdue, blocked, or work requiring immediate attention |
| `good` | `color-mix(in srgb, var(--good) 7%, transparent)` | `--good` | 6px | Confirmed completion |

Choose the tone from the record's actual state and the product's deadline policy. A workflow state such as `In progress` is neutral, not a warning or forecast. Keep the title and metadata readable on every fill. Do not fade completed cards, hide other work, reorder records, or infer deadlines as part of the card component; those lifecycle decisions belong to the host application.

## Interaction and Reuse

- For a card that opens details, use one native `<button type="button" class="task-card" data-task-card aria-haspopup="dialog">` containing spans for its header and title. The whole card is the click/tap target and responds to native Enter/Space activation. Do not nest links, copy buttons, or other interactive controls inside it. Keep cards in ordinary DOM/tab order.
- On devices that support hover, mix the card's base fill with 8% `--ink` using `color-mix(in srgb, var(--task-card-fill) 92%, var(--ink) 8%)`. Keep state and geometry fixed. Render a 2px `--focus` outline inset 2px around the whole card so adjacent cards cannot cover it. Button cards use the shared 0.98 pressed scale; omit transitions under reduced motion.
- A native disabled button retains its state, uses opacity 0.45 and `cursor: not-allowed`, and has no hover/pressed treatment. Completion alone does not disable a card.
- Open a labelled native dialog with the complete project, ID, title, and header status. Put additional task-specific content in the source card's sibling `<template data-task-details>`, including the exact date and any workflow state omitted when the header shows only a deadline. Escape, the close button, and backdrop activation dismiss the dialog and restore focus to the originating card; keep Tab/Shift+Tab inside it.
- For a card that navigates elsewhere, use an `<a class="task-card" href="…">` with a real destination instead of the dialog attributes. For a passive desktop readout, use `<article class="task-card">` with the same compact rows and omit interactive attributes and hover effects.

Copy `components/cards.css` and the `.task-cards` markup from `components/cards.html`, together with the shared tokens/base styles. The example's outer `.card-examples` wrapper only adds 24px between the existing metric grid and the task grid; it is not a required card dependency.

For dialog inspection, also copy `components/cards.js`, the existing dialog/button/badge styles, and the Lucide `X` symbol. Each list item contains its card button followed by its optional details template. The controller reads project, title, state, and ID from the card's header and title elements, falling back to `data-state` when the card header omits visible state text.

Load the script after the markup. It initializes each wrapper marked `data-task-cards` and needs no catalog IDs or sample data. For a wrapper added later, call `ComponentReference.initTaskCards(root)`. Initialization is idempotent; each root gets an independent dialog. Click handling is delegated, so replacing or adding card items within an initialized root needs no rebinding. Templates contain trusted application markup; insert external task text with `textContent` rather than interpreting it as HTML.

The script fits titles on initialization and root resize, resetting each title to 15px before trying 14px and then 13px. Call the returned instance's `adjustTitles()` after changing titles, adding records, or loading fonts when the wrapper's size has not changed. CSS alone keeps the 15px title and ellipsis; a passive copy that omits the dialog controller needs the title-fitting helper if it should use the same size steps. Call `destroy()` before removing the wrapper to disconnect resize observation and detach its handler and dialog.
