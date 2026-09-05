# Task Cards

Use these cards for independently identifiable tasks or issues whose project, title, state, and deadline need to be scanned together. They provide the compact record layout needed by the Linear desktop overlay and also work for queues or task grids in a dashboard. The [Cards & Panels example](../assets/component-reference/index.html#cards) keeps the existing metric groups and adds six fictional task cards, with dates relative to 05 September 2026.

## Layout and Content

- Use a grid with no horizontal or vertical gaps and as many columns as fit at a preferred minimum width of 252px. Below that width, let a single card shrink to the container. Use `repeat(auto-fit, minmax(min(100%, 252px), 1fr))` and `gap: 0`; do not create page-level horizontal scrolling.
- Give each card 12px padding, a 104px minimum height, no border, and no shadow. Use three rows separated by 4px: project and state, task title, then issue ID and optional metadata. Keep cards in a grid row equal in height, while allowing long metadata to increase their height.
- Keep the project at the top left and a short, explicit state at the top right. Use 12px UI text with a 16px line-height; project text uses `--muted`, and state text uses the tone below with weight 500. Let the project ellipsize before the state, and retain its full text for accessibility and inspection.
- Set the left-aligned title in 15px UI text, weight 500, 20px line-height, and `--ink`. Reserve two lines and clamp after the second in an interactive preview. Keep the complete title in the DOM and show it without truncation in the details dialog. Do not shrink the font for longer titles. A passive card with no way to inspect more detail must allow its title to wrap fully.
- Put the issue ID at the bottom left in 12px `--mono` and `--muted`. Put a useful label or date at the bottom right in 12px UI text. Retain prefixes such as `Due` or `Done` so the date has an explicit meaning, and use a semantic `<time datetime="…">`. Let the footer wrap when needed rather than overlap or drop the ID or deadline.
- Use the full card's soft fill and its visible state text together. Omit decorative rails, inset frames, separator rules, status dots, and duplicate state badges inside the card. The details dialog may use the existing badge component.

## State Treatments

| `data-tone` | Fill | State text | Radius | Typical use |
| --- | --- | --- | --- | --- |
| `neutral` or omitted | `--surface` | `--muted` | 6px | Todo, in progress, ordinary workflow state |
| `caution` | `--caution-soft` | `--caution` | 4px | Due soon or elevated priority |
| `danger` | `--danger-soft` | `--danger` | 0px | Overdue, blocked, or work requiring immediate attention |
| `good` | `--good-soft` | `--good` | 6px | Confirmed completion |

Choose the tone from the record's actual state and the product's deadline policy. A workflow state such as `In progress` is neutral, not a warning or forecast. Keep the title and metadata readable on every fill. Do not fade completed cards, hide other work, reorder records, or infer deadlines as part of the card component; those lifecycle decisions belong to the host application.

## Interaction and Reuse

- For a card that opens details, use one native `<button type="button" class="task-card" data-task-card aria-haspopup="dialog">` containing spans for its header, title, and footer. The whole card is the click/tap target and responds to native Enter/Space activation. Do not nest links, copy buttons, or other interactive controls inside it. Keep cards in ordinary DOM/tab order.
- On devices that support hover, mix the card's base fill with 8% `--ink` using `color-mix(in srgb, var(--task-card-fill) 92%, var(--ink) 8%)`. Keep state and geometry fixed. Render a 2px `--focus` outline inset 2px around the whole card so adjacent cards cannot cover it. Use the shared 0.98 pressed scale; omit transitions under reduced motion.
- A native disabled button retains its state, uses opacity 0.45 and `cursor: not-allowed`, and has no hover/pressed treatment. Completion alone does not disable a card.
- Open a labelled native dialog with the complete project, ID, title, state, and optional metadata, including the exact date. Put additional task-specific content in the source card's sibling `<template data-task-details>`. Escape, the close button, and backdrop activation dismiss the dialog and restore focus to the originating card; keep Tab/Shift+Tab inside it.
- For a card that navigates elsewhere, use an `<a class="task-card" href="…">` with a real destination instead of the dialog attributes. For a passive desktop readout, use `<article class="task-card">`, omit interactive attributes and hover effects, and let long titles grow to fit. Keep the same three-row anatomy, tones, and typography.

Copy `components/cards.css` and the `.task-cards` markup from `components/cards.html`, together with the shared tokens/base styles. The example's outer `.card-examples` wrapper only adds 24px between the existing metric grid and the task grid; it is not a required card dependency.

For dialog inspection, also copy `components/cards.js`, the existing dialog/button/badge styles, and the Lucide `X` symbol. Each list item contains its card button followed by its optional details template. The controller reads project, title, state, ID, and metadata from the card's `.task-card-project`, `.task-card-title`, `.task-card-state`, `.task-card-id`, and `.task-card-meta` elements so preview and details share one source. Keep the first four present; omit the metadata element when it has no useful value.

Load the script after the markup. It initializes each wrapper marked `data-task-cards` and needs no catalog IDs or sample data. For a wrapper added later, call `ComponentReference.initTaskCards(root)`. Initialization is idempotent; each root gets an independent dialog. Click handling is delegated, so replacing or adding card items within an initialized root needs no rebinding. Call the returned instance's `destroy()` before removing the wrapper to detach its handler and dialog. Templates contain trusted application markup; insert external task text with `textContent` rather than interpreting it as HTML.
