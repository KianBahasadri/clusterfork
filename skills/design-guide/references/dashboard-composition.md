# Dashboard Composition

The [dashboard reference](../assets/dashboard-reference/index.html) exercises the existing component kit as a cluster operations page. Use it to evaluate whether the kit supports a concrete workflow before adding component types. Component treatments and interaction rules remain owned by the four main specification references.

## Scope and Data

The page is a fixed sample snapshot for production, all regions, at 15:00 UTC on 4 September 2026. It has no live service connection. Its overview totals are calculated from the six service rows and shared service metadata; its cluster p95 and latency readouts come from the catalog's chart samples. Weight the aggregate error rate by requests per minute, rather than averaging service percentages. Regional rows sum requests and replicas and show the most severe service status in each region. The open incident illustrates investigating the order queue through the existing details dialog; its age is relative to the snapshot.

The composed workflow is: read aggregate metrics and the health callout, compare service health with region, replica count, release, and owner, then inspect latency, regional health, and the open incident together. Every service and region column supports the existing sort cycle and multiple priorities. Chart exploration includes the existing time range, series, plotting, exact-data, and CSV controls. Service CSV export follows the displayed row order and includes all nine service fields plus the snapshot timestamp, with numeric units in its column names.

## Layout

The dashboard shell spans the available viewport width, with no maximum width, outer side margins, or horizontal page padding at any breakpoint. Its density follows compact Conky readouts: align related values, place useful groups beside each other, and spend space on data and controls.

- Above 900px, use a compact horizontal navigation row with 44px minimum height, retaining clearance for the fixed page utilities. At narrower widths, use the existing mobile contents drawer. Keep section and shortcut order consistent with the document.
- Combine the page title, scope, snapshot, and summary into one overview section. Use a 20px page title, 15px section headings, 12px between main sections, and 20px between lower groups. Keep metrics at the catalog's readable label and value sizes; arrange the six summaries in six columns above 900px, three from 421px through 900px, and two at 420px and below, with 16px gaps.
- Give the service table the full width. Above 1200px, arrange latency, regional health, and incident details in three columns with proportions 1.15:1.05:0.8. Between 701px and 1200px, keep latency beside the stacked region and incident sections; at 700px and below, stack all three in document order. Use a 180px chart preview and keep detailed controls in the existing explorer.
- For these dense dashboard tables, retain 13.5px cell text and use 4px vertical and 10px horizontal cell/header padding. Keep all fields readable and permit horizontal scrolling inside a table when needed, with neutral scrollbars. At 600px and below or with a coarse pointer, increase body-cell vertical padding to 10px around a 24px minimum row trigger, making clickable service rows at least 44px tall. Give header sort buttons and standalone buttons a 44px minimum height, removing vertical header-cell padding.
- Use 8px vertical and 10px horizontal padding for dashboard callouts. Keep semantic colors, status shapes, focus indicators, and dialog dimensions from the catalog. Do not add decorative cards around these groups. Use landscape print layout and omit sort indicators in printed tables.

At 1366×768 and larger desktop viewports, the complete sample should fit in one view with every service column visible. Reflow smaller screens naturally; do not reduce text to force the desktop arrangement onto a phone. These density choices belong to the dashboard example, while the catalog continues to demonstrate its default component spacing.

## Reuse and Ownership

- `dashboard-reference/components/` owns the dashboard's content and section markup. `shared/dashboard.css` owns page composition, and `shared/dashboard.js` connects its derived totals, service context columns, regional table, service export, and incident action.
- Component styles and behavior load directly from `component-reference/`. Icons, page tools, toast history, service details, and the chart explorer use shared catalog HTML fragments at assembly time. Fix their component behavior in the catalog sources so both references receive the change.
- `component-reference/components/services-data.js` supplies region, owner, release, and replica data to both the service details dialog and dashboard. Load it before `tables.js` and the dashboard script. Reuse `makeTableSortable` for the regional table, keeping its sorting independent of the services table.
- The dashboard's `shared/page-options.js` configures `window.ComponentReference.pageOptions` after `icons.js` and before `page.js` and `spotlight.js`: `title` and `exportFilename` customize Markdown export; `destinationName` and `destinationPlural` customize section-search labels. Omitted options retain the catalog defaults. Navigation destinations and shortcut descriptions remain explicit page markup.

## What This Example Reveals

The existing metrics, callouts, chart, table, dialogs, buttons, badges, breadcrumbs, and page utilities cover this sample's workflow. It did not require another visual component type.

The first sparse composition spent substantial space on a vertical index, large section gaps, and a chart occupying a full row. Compact composition exposes more of the existing data at once: the page title and scope share a row, service context is visible in the table, and the supporting groups share the lower area. This establishes a dashboard density example without requiring new visual component types.

The example scripts also have a reuse limit: service details and chart exploration bind to fixed element IDs and sample data. Table sorting already supports multiple independent tables, as the service and region tables demonstrate. Multiple service-detail or chart-explorer instances would still need explicit initialization and data inputs. Treat that as an implementation boundary, rather than evidence that a new visual component is missing.
