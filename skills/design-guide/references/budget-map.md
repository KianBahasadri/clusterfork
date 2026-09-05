# Budget Map

## 14. Shared Time and Limit Plane

Use this component to see which of several budgets, quotas, or capacities is approaching its own ceiling, and where each is forecast to finish. It works best with two to six items sharing the same period. Each item must have a positive limit and use the same unit for its history, current value, forecast, and limit. Different items may have different units: the shared coordinate is percentage of that item's limit, never a sum of unrelated amounts. Use a table when exact lookup is the main task; use the map when relative pressure and trajectory should be visible together.

The [runnable example](../assets/component-reference/index.html#budget-map) uses illustrative budgets for AWS, Azure, Blacksmith, GitHub Actions, and OpenRouter, identified as sample data in its accessible description. Amounts and limits are fictional. The renderer does not fetch data or calculate forecasts.

## Anatomy and Geometry

- Draw one transparent, borderless SVG containing an affine time/usage plane with the original billing-map camera: the left time-zero edge is near, and time recedes upward and right toward the far period-end edge. From the bottom origin, use time vector `(160, -94 × 0.82)` and usage vector `(-145, -94 × 0.82)`, uniformly scaled. Keep parallel gridlines and shared coordinates for every item. Do not replace the plane with separate per-item charts or place it inside a decorative card.
- Normalize each raw amount as `amount / item.limit * 100`. The period start is time zero; period end is time one. Put the current markers on the shared `asOf` line and forecasts on the shared period-end edge. Keep every marker centered on its actual coordinate; do not spread coincident values apart. Inspection brings the selected item's trail and logo to the foreground.
- Start the usage scale at zero with a 105% maximum, leaving a red band from 100% to 105% when all values fit. If any historical, current, or forecast percentage exceeds 105%, expand the maximum to that largest supplied percentage without rounding up or adding extra headroom. Recalculate on data updates and return to 105% when all supplied values fit again. Keep overruns visible past the 100% limit; do not clamp them to the boundary. Include the fitted maximum and exact period in the accessible description.
- Draw usage gridlines every 25 percentage points across the entire fitted scale, including 125%, 150%, and subsequent steps inside the red region when they fit. Draw the outer edge at the exact fitted maximum as well; do not duplicate it when the maximum is already a 25% step. Keep gridlines neutral except for the 100% danger boundary, with a translucent angular `--danger-soft` region beyond it. Give the near left edge a slightly stronger neutral stroke. Mark the current time with a neutral dashed line. A faint dotted line from period-start zero to period-end 100% shows even consumption pace.
- Draw observed history as solid `--accent` trails with small sample dots. Use each service's supplied logo for its current marker, preserving its recognizable vector silhouette and brand colors. Keep logos upright in screen space, with a 22px size on wide containers and 16px below 480px; wide marks may use 1.2 times that width while preserving aspect ratio. Brand colors identify the provider and do not change the chart's data/state colors. Use a filled neutral-series marker only when an item has no registered logo.
- Draw supplied forecasts as dashed `--derived` connectors and hollow end markers. Change only the part of a forecast beyond 100% to `--danger`. Do not fabricate observations, extrapolate a forecast, or connect across an explicit null history sample. A single observation remains a point. Use hollow circles for forecasts below the warning threshold, a 4px-radius square for near-limit forecasts, and a sharp square for overruns. Current overruns take precedence over a lower forecast.
- Keep the populated map free of persistent text: no axis labels, dates, values, item names, status captions, leader lines, or legend. Names, exact values, units, dates, and status text appear in hover/keyboard readouts, the details dialog, and the accessible description. Use `--mono` at 11px for readout dates and the shared text styles for inspection details.
- Place the plot directly beneath the component section heading. Omit the dataset-title, date-range, and sample-data header. Use the dataset title for its accessible name and details dialog. Center the plane, cap its width at 720px, leave 16px clearance around it, and derive height from the same projection ratio rather than stretching the camera on mobile. Retain vertical scrolling and zoom.

## Data and States

The constructor accepts one object with `title`, ISO `start`, `end`, and `asOf` timestamps, optional `sample`, optional `warningAt` (default 90 percent), and an `items` array. Use `start <= asOf < end`; a monthly period can end at midnight on the first of the following month. `warningAt` must be greater than zero and less than 100.

Each item has a stable string `id`, a `label` for inspection and accessibility, a `unit` string, a positive numeric `limit`, numeric or null `current` and `forecast` values, optional `logo` registry key, optional `stale`, and a `history` array of `{ at, value }`. Historical timestamps must be unique and fall inside `[start, asOf)`; input order is normalized chronologically. Use explicit null samples to mark gaps. Amounts must be finite and non-negative. An unavailable amount is null, including in exported data; zero is a real measurement.

In inspection and accessible descriptions, forecasts above 100% say `Forecast over limit`; forecasts from `warningAt` through 100% say `Near limit`; lower forecasts say `Within limit`. A current amount above 100% says `Over limit now`. Missing forecasts say `No forecast`, and missing current and forecast values say `Unavailable`. Retained stale data is dimmed and labelled `Stale` in inspection. An entirely empty or unavailable dataset has a visible message instead of appearing to be a healthy empty map; individual unavailable sources are named in the accessible description and details dialog.

## Inspection and Exact Data

- Make the interactive preview a native button. Hover inspects the nearest supplied observation, current marker, or forecast endpoint, emphasizes that item's entire trail, and shows the full item name, exact amount and unit, percentage, UTC date, and status. Keep the neutral readout within the plot. Clear it on pointer exit, blur, or Escape.
- Left/Right move through items in input order; Home/End reach the endpoints without wrapping. Announce keyboard inspection politely. Preserve page modifier shortcuts. Enter, Space, or tapping the map opens the selected item's details in a labelled native dialog.
- In the dialog, offer working item selection, limit, current amount, forecast, forecast percentage, and forecast headroom. Negative headroom means a projected overrun. Put the complete exact-data table behind `Show exact data` inside this dialog. Every column supports the shared sort cycle and multiple priorities; confine wide data to a scrolling region with an opaque sticky header.
- `Export all data CSV` always exports every item's supplied history, current amount, and forecast in input-item/chronological order, independent of the inspected item and table sort. Include UTC timestamps, raw amounts, units, limits, percentages, freshness, and headers; leave unavailable values empty and escape CSV fields. Include the period in the filename and announce export completion inside the dialog.
- Trap dialog focus, close on Escape or backdrop activation, and restore focus to the map. Use the shared dialog colors, typography, controls, and responsive touch targets. Map interaction shortcuts belong in the page's shortcut popup.
- For a passive desktop readout, set `interactive: false`. Render the same map and accessible data summary without clickable controls or a dialog. The visual structure does not depend on hover.

## Copying the Implementation

Copy `budget-map-model.js`, `budget-map-plot.js`, `budget-map.js`, and `budget-map.css` from `assets/component-reference/components/`, together with the shared tokens/base styles. Load the model first, then the plot and controller. Interactive use also needs the existing dialog/button/table styles and Lucide `X`/sort-arrow symbols; load `table-sorting.js` after the model and before the controller. The model creates `window.ComponentReference` if needed; no catalog element IDs or sample dataset are required.

For provider marks, also copy `budget-map-logos.js` and load it after the model and before initialization. It registers `aws`, `azure`, `blacksmith`, `github-actions`, and `openrouter`, using the vector paths from the original Conky billing map (`conky-linear-HUP`, revision `a8152dd`). The AWS mark retains that map's compact smile treatment. The brands retain ownership of their marks. To supply other logos, pass `options.logos` as a registry whose entries contain SVG `viewBox`, positive width/height `ratio`, and a `paths` array of SVG path attributes. Use verified vector assets for each service's identity. Unknown or omitted logo keys use the generic current marker.

```html
<div id="spend-map"></div>
<script>
  var map = ComponentReference.createBudgetMap(
    document.getElementById("spend-map"),
    {
      title: "Compute budget", start: "2026-09-01T00:00:00Z",
      end: "2026-10-01T00:00:00Z", asOf: "2026-09-16T00:00:00Z",
      items: [{
        id: "compute", label: "Compute", unit: "USD", limit: 10000,
        current: 7200, forecast: 13200,
        history: [{ at: "2026-09-08T00:00:00Z", value: 3020 }]
      }]
    }
  );
</script>
```

`createBudgetMap(root, data, options)` owns only the supplied root's contents. Each instance has independent selection, dialog IDs, sort state, and resize observation. Call `map.update(nextData)` with another complete dataset; invalid updates leave the existing model unchanged. Call `map.destroy()` before removing or remounting its root to disconnect the observer and dispose of its dialog. Pass `{ interactive: false }` as the third argument for a passive view. `budget-map-example.js` supplies only the catalog's example data and initialization; replace it in another product.
