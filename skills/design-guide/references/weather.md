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

| Mode | Difference from the mean | Visible fill | Fill color |
| --- | --- | --- | --- |
| Far below average | −6°C or lower | Bulb only | Muted yellow |
| Below average | Greater than −6°C and less than −2°C | One-quarter of the tube | Muted blue |
| Around average | −2°C through +2°C, inclusive | Half the tube | Muted blue |
| Above average | Greater than +2°C and less than +6°C | Three-quarters of the tube | Muted blue |
| Far above average | +6°C or higher | Full tube | Muted yellow |

* Calculate the comparison before any optional unit conversion. These are simple comparison thresholds, not a forecast, safety judgement, or statistical anomaly model.
* Use the quantitative thermometer geometry in `components/weather.js`: a 48×184px cropped viewport, rounded tube, and circular bulb. The outer path uses `--surface-raised` and a 1.5px `--line-strong` stroke. Color the observed fill and bulb together using the muted palette below; preserve their rounded geometry at every level.
* The inner tube is 6px wide; its top positions for the five levels are 160, 114, 80, 46, and 12px in the SVG's coordinate system, ending at 160px. Keep the bulb filled at every level. Far below has no tube fill; far above reaches the tube's top.
* Do not add side notches, an average marker, an indicator dot, numeric ticks, or temperature labels to the thermometer. Keep fill height as the primary mode indicator. In either direction from around average, use blue, blue, then yellow: the middle three modes share the same muted blue, and only the two extremes turn muted yellow. Never use gray or red for temperature. Retain the named mode in the summary's accessible name and hover title.
* Require seven finite previous-day readings and one finite current reading. If an application cannot supply them, present an unavailable state at the integration boundary instead of treating absent readings as zero or inventing a comparison.

## Weather Glyph and Layout

* Use Lucide `Sun` / `Moon` for clear weather, `CloudSun` / `CloudMoon` for cloudy weather, `CloudRain` for rain, and `Snowflake` for snow. Choose the day icon from sunrise inclusive until sunset exclusive; choose the night icon otherwise.
* Preserve the Lucide paths, 24×24 view box, 2px stroke, round joins and caps, and no fill. Use `--muted`; scale the whole glyph uniformly.
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
* Default `hour12` to `false` for zero-padded 24-hour `HH:mm` values, such as `06:30` and `19:00`. With `hour12: true`, use `h:mm AM/PM`, such as `6:30 AM` and `7:00 PM`, without a leading hour zero. Midnight is `12:00 AM`; noon is `12:00 PM`. Apply the selected format to both sun times, their row titles, and every clock time in the summary's accessible name and hover title.
* Use a `<time>` element with a 24-hour `HH:mm` `datetime` in either display format, 14px `--mono`, 1.5 line-height, tabular numbers, and `--muted` text. Keep the existing centered layout and spacing as the time text changes width.
* Default `showSunTimes` to `true`. When enabled, keep both times visible during the day and at night. Update their text, `datetime`, row titles, and the summary's accessible description whenever the supplied event times change. The sunrise/sunset display contains no progress track, marker, or night indicator.
* With `showSunTimes: false`, hide both time rows, including their glyphs, and collapse their space. Leave the UV and rain rows independently visible. Omit event times and the next transition from the summary's accessible name and hover title. Continue using the supplied event times to determine the weather glyph's day/night form, and keep time values current so showing them again restores the latest readings.
* Accept local minutes since midnight for the current time, sunrise, and sunset. The compact reference supports a same-day sunrise before sunset. The caller must resolve location, date, timezone, and real event times; do not assume equal day/night lengths.
* At sunrise, switch the weather glyph to its daytime form; at sunset, switch to its night form. Calculate the next sunrise across midnight with a 24-hour wrap for the accessible description. Do not animate independently of supplied time or fetch weather/location data in the component.

## UV Index and Rain Chance

* Place the UV index row immediately below the sun times, followed by the rain chance row. Use a fixed 16px Lucide `Radiation` for UV and `CloudRain` for rain, with the same 8px icon-to-value gap and 14px tabular mono values as the sun times. Color each row's glyph and value together using its own reading and the muted palette below. Keep these glyphs unchanged at night and when the main weather glyph is resized.
* Show UV as just `4` or `4.2` beside the radiation glyph, rounding to at most one decimal with no visible `UV` prefix. Keep the UV identification in its row title and the summary's accessible name. Show rain chance as a whole percentage, such as `35%`. The percentage represents probability, not rainfall amount; use caller-supplied values without deriving either reading from the weather condition or time slider.
* Default `showUvIndex` and `showRainChance` to `true`. Each boolean independently hides its entire row and collapses its space. Include only enabled readings in the summary's accessible name and hover title. Identify each reading in its row title and continue updating hidden values so showing a row restores the latest reading.
* Accept `uvIndex` as a nonnegative finite number and `rainChancePercent` as a finite number from 0–100. Both default to `null`, meaning unavailable: render `—` beside the corresponding glyph and describe the reading as unavailable. Do not substitute zero for missing data. Preserve zero as a valid reading and reject invalid non-null inputs before changing the rendered state.

## Muted Colors and Reading States

* Use `--muted` for ordinary detail text, detail glyphs, and the main weather glyph. The thermometer's blue for below, around, and above average is `color-mix(in srgb, var(--accent) 40%, var(--muted))`.
* Scope warning colors to the weather component: yellow is `color-mix(in srgb, var(--caution) 65%, var(--muted))`; red uses the same mix with `--danger` and applies only to UV and rain. Use these subdued colors for both themes, without changing global tokens or reducing element opacity. Keep small text readable against the canvas and surface backgrounds.
* Evaluate each reading independently before display rounding. Yellow/red on one metric does not recolor the main weather glyph, sun times, or another metric. Returning to a lower or missing reading clears the previous warning color; missing readings always use `--muted`.

| Reading | Muted neutral | Muted yellow | Muted red |
| --- | --- | --- | --- |
| UV index | Below 3: low | 3 to below 8: moderate to high | 8 or above: very high to extreme |
| Rain chance | Below 50% | 50% to below 80%: elevated likelihood | 80% or above: high likelihood |

The UV groupings follow the [EPA UV Index scale](https://www.epa.gov/sunsafety/uv-index-scale-0). Rain cutoffs are component display choices for precipitation likelihood, not measurements of storm intensity or official warning thresholds; [the National Weather Service defines precipitation probability](https://www.weather.gov/ffc/pop) as the chance of measurable precipitation. Likewise, temperature colors reinforce the relative modes above, rather than asserting an absolute heat-safety threshold. Keep the numeric UV/rain values visible and include their category descriptions in row titles and in the summary's accessible name and hover title whenever the rows are enabled.

## Runnable Reference and Reuse

Open [16 Weather](../assets/component-reference/index.html#weather) for one configurable preview. Its controls select clear/cloudy/rain/snow weather, time in 15-minute steps, temperature from 0–40°C, UV index from 0–12 in 0.5 steps, rain chance from 0–100% in 5% steps, independent visibility for sun times, UV index, and rain chance, glyph size from 50–200% in 5% steps, and the six layouts above. The fictional seven-day readings are 18, 20, 22, 21, 23, 24, and 19°C (mean 21°C); sunrise is 06:30 and sunset is 19:00. UV and rain controls default to 4 and 35%, remain adjustable when their rows are hidden, and state their yellow/red thresholds in helper text. The UV slider's maximum only limits the demo, not the renderer's accepted readings.

* Edit `components/weather.html`, `weather.css`, `weather.js`, and `weather-example.js`. The example owns all controls and sample data; the renderer has no catalog-ID, server, or prototype-directory dependency.
* Put the six layout choices under the `Layout` legend. Keep condition and layout selections visible with neutral pressed-button states and `aria-pressed`. Give every slider a visible label, associated output, descriptive `aria-valuetext`, and standard keyboard operation. Reset restored controls to the same defaults as the preview on initialization.
* Group four native checkboxes with `role="switch"`: an unchecked `Use 12-hour time`, followed by checked `Sunrise and sunset times`, `UV index`, and `Rain chance`. Arrange them in two equal columns in reading order, with 20px column gaps and 4px row gaps. Collapse to one column at viewport widths of 600px or less. Apply the shared switch treatment, a 44px minimum label target, and a visible focus ring on each track. Clicking a label or pressing `Space` updates only that option immediately.
* The time-format switch uses 24-hour time when off and 12-hour time when on. Update the time slider's output and `aria-valuetext`, the sunrise/sunset helper text, and the summary together. Keep this switch usable while sun times are hidden; showing them again restores the current format. Reuse `ComponentReference.formatWeatherTime(minute, hour12 = false)` for catalog clock text, with valid local minutes from 0–1439.
* Reserve a 200px preview column beside the controls on wider screens so changing layout does not shift the controls horizontally. At 700px and below, stack the summary above the controls. Let weather choices wrap, keep layout choices in two columns, and stack the time/temperature and UV/rain sliders when their columns would be too narrow. Support a 320px viewport and 200% zoom without page overflow.
* Load shared tokens, base styles, the Lucide sprite and `shared/icons.js`, followed by `weather.css` and `weather.js`. The catalog's exploration controls additionally use the shared button, range, and switch styles. Rebuild the generated catalog after HTML edits.

```js
const weather = ComponentReference.createWeather(container, {
  temperatureCelsius: 21,
  previousWeekCelsius: [18, 20, 22, 21, 23, 24, 19],
  minute: 840,
  sunrise: 390,
  sunset: 1140,
  hour12: false,
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
weather.update({ hour12: true });
weather.update({ uvIndex: 2.5, rainChancePercent: 60, showUvIndex: false });
// Remove this instance when its containing view is retired.
weather.destroy();
```

`update(patch)` changes only supplied options and returns `{ average, level, mode, isDay }`. Each instance owns its data and DOM; it can coexist with other instances. Invalid options, including non-boolean visibility flags or `hour12`, throw before changing the rendered state. Omitted condition, glyph size, and placement default to clear, 32px, and `right-icon-above`. Supply all temperature and time inputs explicitly, including event times when their display is hidden. Display format does not change the supplied minutes or the day/night calculation.
