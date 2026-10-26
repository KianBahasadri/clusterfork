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
- Read [Controls and Forms](references/controls-and-forms.md) for buttons, action triggers, dropdowns, menus, inputs, search fields, textareas, checkboxes, radios, switches, and sliders with precision scrubbing and typed-value entry.
- Read [Data Display](references/data-display.md) for badges, metadata, tables, charts, metrics, cards, panels, and color semantics.
- Read [Task Cards](references/task-cards.md) for compact task or issue records, project/deadline metadata, workflow contrast, and details dialogs.
- Read [Budget Map](references/budget-map.md) when comparing several budgets, quotas, or capacity limits over a shared period, including current usage and end-of-period forecasts.
- Read [Realtime Monitoring](references/realtime-monitoring.md) for continuously updating resource readings, recent trends, service-status histories, and feed freshness.
- Read [Weather](references/weather.md) for a visual temperature comparison with the previous three days, weather glyph sizing and placement, and optional sunrise/sunset times, UV index, and rain chance.
- Read [Overlays and Feedback](references/overlays-and-feedback.md) for dialogs, modals, tooltips, popovers, navigation, tabs, breadcrumbs, alerts, callouts, and toasts.
- Read every component reference above when building or reviewing a complete component catalog; for other interfaces, read the references for the components they contain.
- For a dashboard composed from the existing kit, also read [Dashboard Composition](references/dashboard-composition.md) for the sample's scope, reuse, and observed limits.

Treat each linked reference as the canonical instruction source for its area. Where a dedicated component reference explicitly specifies a different treatment, use that treatment; otherwise inherit the shared rules. Do not duplicate detailed rules in this router.

## External Application Examples

- For multi-view React + TypeScript applications with collection tables, market charts, crafting trees, faceted exploration, drag-and-drop workbenches, or screenshot import/review, read [Warframe Toolkit / Grakata](references/warframe-toolkit.md) for upstream locations and patterns to examine.
- For a download site built around an interactive book, edition archive, and downloads available without JavaScript, read [GRADE Book](references/grade-book.md) for upstream locations and patterns to examine.

Inspect the current upstream source when using an external example. Each guide identifies the repository, an optional local checkout, and useful source locations; follow the upstream README for current setup and preview instructions. Filenames are starting points: use repository search if they move. These applications illustrate patterns; the local component specifications above own this skill's shared rules.

Keep external repositories outside this skill. Browse upstream or use a separate checkout; if a temporary clone is needed, place it outside the skill directory. Maintain links and inspection guidance here instead of vendoring external source, built output, data, fonts, or artwork.

## Runnable References

- For the complete interactive catalog, open [Component Reference](assets/component-reference/index.html).
- For those components composed into a working page, open [Dashboard Reference](assets/dashboard-reference/index.html).

In that catalog, edit a component's markup, styles, and behavior in its matching `components/<name>.html`, `.css`, and `.js` files. Use matching filename prefixes for related examples and dialogs, such as `forms-selection.html` and `tables-details.html`, or when a larger component needs separate data, rendering, and interaction modules. Shared icons, page chrome, tokens, base styles, and page utilities live in `shared/`; shared table sorting lives in `components/table-sorting.js` and sample service metadata in `components/services-data.js`.

Each reference's `index.template.html` owns its page shell, fragment order, and explicit stylesheet and script load order. Include a fragment on its own line with `<!-- include: components/tables.html -->`; paths are relative to that reference directory, and the assembler preserves the include line's indentation. The dashboard can include canonical catalog fragments with `<!-- include: ../component-reference/components/tables-details.html -->`. Keep fragments as plain HTML and put includes only in templates. When adding a section, update that reference's index in `shared/page-sidebar.html` and the destinations and shortcut descriptions in `components/spotlight.html` and `components/spotlight-shortcuts.html`.

`index.html` in the component catalog and the dashboard is checked-in generated output; edit the template or fragments instead. From the repository root, run `python3 skills/design-guide/scripts/build_reference.py` to build the catalog, or add `--reference dashboard-reference` to build the dashboard. After changing HTML shared by both references, rebuild both, and include generated output with source changes. Add `--check` to either command to verify freshness without writing; the repository tests enforce both. The script uses only Python's standard library and resolves its inputs relative to itself, so it can also be invoked by absolute path from another working directory. CSS and JavaScript edits need no rebuild.

Keep generated references runnable by opening `index.html` directly, without requiring a build step or local server for viewing. The dashboard loads component styles and behavior from its sibling `component-reference/` directory; keep both asset directories together when copying or serving it. Use classic scripts with private scopes, sharing only helpers needed by another module through `window.ComponentReference`. Load helper providers before their consumers.

Keep a runnable reference synchronized with the Markdown file that owns each demonstrated rule. When adding another runnable reference authored for this skill, give it a separate directory under `assets/` with its own entry point and supporting files. Document external applications in a reference guide linking to their upstream repositories.
