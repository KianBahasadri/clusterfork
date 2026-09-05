---
name: design-guide
description: Apply exact UI component implementation specifications covering tokens, anatomy, dimensions, states, interaction, accessibility, and responsive behavior.
metadata:
  short-description: Exact UI implementation contract
---

# Design Guide

Apply the exact specifications in the references relevant to the interface being built or reviewed.

## Read the Relevant Specifications

- Always read [Foundations](references/foundations.md) for shared tokens, typography, geometry, icons, accessibility, page utilities, and layout.
- Read [Controls and Forms](references/controls-and-forms.md) for buttons, action triggers, dropdowns, menus, inputs, search fields, textareas, checkboxes, radios, and switches.
- Read [Data Display](references/data-display.md) for badges, metadata, tables, charts, metrics, cards, panels, and color semantics.
- Read [Overlays and Feedback](references/overlays-and-feedback.md) for dialogs, modals, tooltips, popovers, navigation, tabs, breadcrumbs, alerts, callouts, and toasts.
- Read all four references when building or reviewing a complete interface or component catalog.

Treat each linked reference as the canonical instruction source for its area. Do not duplicate its detailed rules in this router.

## Runnable References

- For the complete interactive catalog, open [Component Reference](assets/component-reference/index.html).

In that catalog, `index.html` owns the semantic markup and explicitly lists stylesheet and script load order. Edit a component's styles and behavior in its matching `components/<name>.css` and `.js` files. Use matching filename prefixes when a larger component needs separate data, rendering, and interaction modules. Shared tokens, base styles, and page utilities live in `shared/`; shared table sorting lives in `components/table-sorting.js`.

Keep the catalog runnable by opening `index.html` directly, without a build step or local server. Use classic scripts with private scopes, sharing only helpers needed by another module through `window.ComponentReference`. Load helper providers before their consumers.

Keep a runnable reference synchronized with the Markdown file that owns each demonstrated rule. When adding another runnable reference, give it a separate directory under `assets/` with its own entry point and supporting files.
