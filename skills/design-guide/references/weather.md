# Weather

Apply [Foundations](foundations.md) and the relevant [Controls and Forms](controls-and-forms.md) rules alongside this specification.

## Purpose and Anatomy

* Combine a thermometer, a weather glyph, and optional compact rows for sunrise/sunset times, UV index, and rain chance into one weather summary. Keep personal training and weight progress in separate components.
* Show temperature relative to the arithmetic mean of the previous seven complete days, excluding today. Use representative temperatures from the same location and the same daily sampling method.
* Keep the temperature comparison visual, with no persistent temperature text or numbers. Show enabled details as values to the right of their glyphs. The summary's accessible name and hover title report the temperature mode, weather, local time, and day/night state, plus the enabled details and next sun transition when sun times are shown.
* Render the summary as a labelled `role="img"` with decorative SVG descendants. It has no buttons or keyboard stop; controls for exploring the example live outside it.
* Size the transparent container to its composition, within the available width, with no border, corner rounding, or shadow. Keep 24px vertical and 20px horizontal padding. Align detail-row glyphs and values in shared columns and separate visible rows by 8px. Remove hidden rows and their space, including the entire detail group when all three visibility options are off.
* Keep the thermometer 184px high. Group the main weather glyph and details into one stack that moves as a unit with the selected layout; size the composition to fit its visible content.

## Relative Temperature

Classify the current reading's difference from the seven-day mean in Celsius:

| Mode | Difference from the mean | Visible fill |
| --- | --- | --- |
| Far below average | −6°C or lower | Bulb only |
| Below average | Greater than −6°C and less than −2°C | One-quarter of the tube |
| Around average | −2°C through +2°C, inclusive | Half the tube |
| Above average | Greater than +2°C and less than +6°C | Three-quarters of the tube |
| Far above average | +6°C or higher | Full tube |

* Calculate the comparison before any optional unit conversion. These are simple comparison thresholds, not a forecast, safety judgement, or statistical anomaly model.
* Use the quantitative thermometer geometry in `components/weather.js`: a 48×184px cropped viewport, rounded tube, and circular bulb. The outer path uses `--surface-raised`, a 1.5px `--line-strong` stroke, and `--accent` for the observed fill.
* The inner tube is 6px wide; its top positions for the five levels are 160, 114, 80, 46, and 12px in the SVG's coordinate system, ending at 160px. Keep the bulb filled at every level. Far below has no tube fill; far above reaches the tube's top.
* Do not add side notches, an average marker, an indicator dot, numeric ticks, or temperature labels to the thermometer. Encode the mode in fill height rather than changing semantic colors.
* Require seven finite previous-day readings and one finite current reading. If an application cannot supply them, present an unavailable state at the integration boundary instead of treating absent readings as zero or inventing a comparison.

## Weather Glyph and Layout

* Use Lucide `Sun` / `Moon` for clear weather, `CloudSun` / `CloudMoon` for cloudy weather, `CloudRain` for rain, and `Snowflake` for snow. Choose the day icon from sunrise inclusive until sunset exclusive; choose the night icon otherwise.
* Preserve the Lucide paths, 24×24 view box, 2px stroke, round joins and caps, and no fill. Use `--ink`; scale the whole glyph uniformly.
* Default to a 32px glyph. Support 16–64px, equivalent to 50–200%, independently of the thermometer and the detail-row glyphs.
* Offer exactly these six layouts through the `placement` option. Account for both glyph size and the visible detail group's width and height so the thermometer, glyph, and details never overlap.

| Layout label | `placement` value | Arrangement |
| --- | --- | --- |
| Right · icon above | `right-icon-above` | Stack right of the thermometer; glyph above details |
| Left · icon above | `left-icon-above` | Stack left of the thermometer; glyph above details |
| Right · icon below | `right-icon-below` | Stack right of the thermometer; glyph below details |
| Left · icon below | `left-icon-below` | Stack left of the thermometer; glyph below details |
| Stack above | `stack-above` | Glyph, details, then thermometer in one centered column |
| Stack below | `stack-below` | Thermometer, details, then glyph in one centered column |

For side layouts, vertically center the thermometer against the entire glyph-and-details stack, with a 16px gap between their element boxes. Center the main weather glyph horizontally over or under the full detail group, including both its icons and values, and leave 12px between them. Recenter the stack whenever its content or glyph size changes. For the vertical layouts, center all blocks on one axis with 12px gaps between visible blocks. Hiding details removes their adjacent spacing. Preserve Lucide's internal whitespace and the thermometer's path geometry.

## Sunrise and Sunset

* Place sunrise first and sunset second at the top of the detail group.
* Each row contains a 16px Lucide `Sunrise` or `Sunset` glyph, followed by its clock time on the right with an 8px gap. Align the glyph and time vertically at their centers. Use `--muted` for the glyph and retain its 16px size when the weather glyph is resized.
* Format times as zero-padded 24-hour `HH:mm` values. Use a `<time>` element with a matching `datetime`, 14px `--mono`, 1.5 line-height, tabular numbers, and `--ink` text. Each row's hover title identifies the event and its time.
* Default `showSunTimes` to `true`. When enabled, keep both times visible during the day and at night. Update their text, `datetime`, row titles, and the summary's accessible description whenever the supplied event times change. The sunrise/sunset display contains no progress track, marker, or night indicator.
* With `showSunTimes: false`, hide both time rows, including their glyphs, and collapse their space. Leave the UV and rain rows independently visible. Omit event times and the next transition from the summary's accessible name and hover title. Continue using the supplied event times to determine the weather glyph's day/night form, and keep time values current so showing them again restores the latest readings.
* Accept local minutes since midnight for the current time, sunrise, and sunset. The compact reference supports a same-day sunrise before sunset. The caller must resolve location, date, timezone, and real event times; do not assume equal day/night lengths.
* At sunrise, switch the weather glyph to its daytime form; at sunset, switch to its night form. Calculate the next sunrise across midnight with a 24-hour wrap for the accessible description. Do not animate independently of supplied time or fetch weather/location data in the component.

## UV Index and Rain Chance

* Place the UV index row immediately below the sun times, followed by the rain chance row. Use a fixed 16px Lucide `Radiation` for UV and `CloudRain` for rain, with the same muted glyphs, 8px icon-to-value gap, and 14px tabular mono values as the sun times. Keep these glyphs unchanged at night and when the main weather glyph is resized.
* Show UV as `UV 4` or `UV 4.2`, rounding to at most one decimal. Show rain chance as a whole percentage, such as `35%`. The percentage represents probability, not rainfall amount; use caller-supplied values without deriving either reading from the weather condition or time slider.
* Default `showUvIndex` and `showRainChance` to `true`. Each boolean independently hides its entire row and collapses its space. Include only enabled readings in the summary's accessible name and hover title. Identify each reading in its row title and continue updating hidden values so showing a row restores the latest reading.
* Accept `uvIndex` as a nonnegative finite number and `rainChancePercent` as a finite number from 0–100. Both default to `null`, meaning unavailable: render `UV —` or `—` and describe the reading as unavailable. Do not substitute zero for missing data. Preserve zero as a valid reading and reject invalid non-null inputs before changing the rendered state.

## Runnable Reference and Reuse

Open [16 Weather](../assets/component-reference/index.html#weather) for one configurable preview. Its controls select clear/cloudy/rain/snow weather, time in 15-minute steps, temperature from 0–40°C, independent visibility for sun times, UV index, and rain chance, glyph size from 50–200% in 5% steps, and the six layouts above. The fictional seven-day readings are 18, 20, 22, 21, 23, 24, and 19°C (mean 21°C); sunrise is 06:30, sunset is 19:00, UV index is 4, and rain chance is 35%.

* Edit `components/weather.html`, `weather.css`, `weather.js`, and `weather-example.js`. The example owns all controls and sample data; the renderer has no catalog-ID, server, or prototype-directory dependency.
* Put the six layout choices under the `Layout` legend. Keep condition and layout selections visible with neutral pressed-button states and `aria-pressed`. Give every slider a visible label, associated output, descriptive `aria-valuetext`, and standard keyboard operation. Reset restored controls to the same defaults as the preview on initialization.
* Group three checked native checkboxes with `role="switch"`, labelled `Show sunrise and sunset times`, `Show UV index`, and `Show rain chance`, with 4px between them. Apply the shared switch treatment, a 44px minimum label target, and a visible focus ring on each track. Clicking a label or pressing `Space` updates only that visibility option immediately.
* Reserve a 200px preview column beside the controls on wider screens so changing layout does not shift the controls horizontally. At 700px and below, stack the summary above the controls. Let weather choices wrap, keep layout choices in two columns, and stack the time/temperature sliders when their columns would be too narrow. Support a 320px viewport and 200% zoom without page overflow.
* Load shared tokens, base styles, the Lucide sprite and `shared/icons.js`, followed by `weather.css` and `weather.js`. The catalog's exploration controls additionally use the shared button, range, and switch styles. Rebuild the generated catalog after HTML edits.

```js
const weather = ComponentReference.createWeather(container, {
  temperatureCelsius: 21,
  previousWeekCelsius: [18, 20, 22, 21, 23, 24, 19],
  minute: 840,
  sunrise: 390,
  sunset: 1140,
  uvIndex: 4,
  rainChancePercent: 35,
  condition: "clear",
  glyphSize: 32,
  placement: "right-icon-above",
  showSunTimes: true,
  showUvIndex: true,
  showRainChance: true
});
weather.update({ minute: 1200, glyphSize: 48, placement: "stack-above" });
weather.update({ showSunTimes: false });
weather.update({ uvIndex: 2.5, rainChancePercent: 60, showUvIndex: false });
// Remove this instance when its containing view is retired.
weather.destroy();
```

`update(patch)` changes only supplied options and returns `{ average, level, mode, isDay }`. Each instance owns its data and DOM; it can coexist with other instances. Invalid options, including non-boolean visibility flags, throw before changing the rendered state. Omitted condition, glyph size, and placement default to clear, 32px, and `right-icon-above`. Supply all temperature and time inputs explicitly, including event times when their display is hidden.
