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
- For a dashboard composed from the existing kit, also read [Dashboard Composition](references/dashboard-composition.md) for the sample's scope, reuse, and observed limits.

Treat each linked reference as the canonical instruction source for its area. Do not duplicate its detailed rules in this router.

## Runnable References

- For the complete interactive catalog, open [Component Reference](assets/component-reference/index.html).
- For those components composed into a working page, open [Dashboard Reference](assets/dashboard-reference/index.html).

In that catalog, edit a component's markup, styles, and behavior in its matching `components/<name>.html`, `.css`, and `.js` files. Use matching filename prefixes for related examples and dialogs, such as `forms-selection.html` and `tables-details.html`, or when a larger component needs separate data, rendering, and interaction modules. Shared icons, page chrome, tokens, base styles, and page utilities live in `shared/`; shared table sorting lives in `components/table-sorting.js` and sample service metadata in `components/services-data.js`.

Each reference's `index.template.html` owns its page shell, fragment order, and explicit stylesheet and script load order. Include a fragment on its own line with `<!-- include: components/tables.html -->`; paths are relative to that reference directory, and the assembler preserves the include line's indentation. The dashboard can include canonical catalog fragments with `<!-- include: ../component-reference/components/tables-details.html -->`. Keep fragments as plain HTML and put includes only in templates. When adding a section, update that reference's index in `shared/page-sidebar.html` and the destinations and shortcut descriptions in `components/spotlight.html` and `components/spotlight-shortcuts.html`.

`index.html` is checked-in generated output; edit the template or fragments instead. From the repository root, run `python3 skills/design-guide/scripts/build_reference.py` to build the catalog, or add `--reference dashboard-reference` to build the dashboard. After changing HTML shared by both references, rebuild both, and include generated output with source changes. Add `--check` to either command to verify freshness without writing; the repository tests enforce both. The script uses only Python's standard library and resolves its inputs relative to itself, so it can also be invoked by absolute path from another working directory. CSS and JavaScript edits need no rebuild.

Keep generated references runnable by opening `index.html` directly, without requiring a build step or local server for viewing. The dashboard loads component styles and behavior from its sibling `component-reference/` directory; keep both asset directories together when copying or serving it. Use classic scripts with private scopes, sharing only helpers needed by another module through `window.ComponentReference`. Load helper providers before their consumers.

Keep a runnable reference synchronized with the Markdown file that owns each demonstrated rule. When adding another runnable reference, give it a separate directory under `assets/` with its own entry point and supporting files.
