# Weather

Apply [Foundations](foundations.md) and the relevant [Controls and Forms](controls-and-forms.md) rules alongside this specification.

## Purpose and Anatomy

* Combine a thermometer, a weather glyph, and two compact sunrise/sunset time rows into one weather summary. Keep personal training and weight progress in separate components.
* Show temperature relative to the arithmetic mean of the previous seven complete days, excluding today. Use representative temperatures from the same location and the same daily sampling method.
* Keep the temperature comparison visual, with no persistent temperature text or numbers. Show sunrise and sunset as clock times to the right of their glyphs. The summary's accessible name and hover title report the temperature mode, weather, local time, day/night state, sunrise, sunset, and time until the next transition.
* Render the summary as a labelled `role="img"` with decorative SVG descendants. It has no buttons or keyboard stop; controls for exploring the example live outside it.
* Use a transparent 160px-wide container with no border, corner rounding, or shadow. Keep 24px vertical and 20px horizontal padding, and a 16px gap between the thermometer stage and the sunrise/sunset rows.
* Keep the thermometer 184px high and reserve `192px + glyph size` for its stage so changing placement does not move the time rows. The glyph size may change the overall height.

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

## Weather Glyph and Placement

* Use Lucide `Sun` / `Moon` for clear weather, `CloudSun` / `CloudMoon` for cloudy weather, `CloudRain` for rain, and `Snowflake` for snow. Choose the day icon from sunrise inclusive until sunset exclusive; choose the night icon otherwise.
* Preserve the Lucide paths, 24×24 view box, 2px stroke, round joins and caps, and no fill. Use `--ink`; scale the whole glyph uniformly.
* Default to a 32px glyph. Support 16–64px, equivalent to 50–200%, independently of the thermometer and the sunrise/sunset glyphs.
* Offer exactly these six placements. Recalculate positions from the glyph size so enlarging it preserves its attachment point and prevents clipping.

| Option | Position |
| --- | --- |
| Close right | To the right of the tube, centered at SVG y=80, with a 4px gap from its outline geometry |
| Close left | Mirror of close right |
| Beside right | To the right of the bulb, centered at SVG y=160, with a 4px gap from its outline geometry |
| Beside left | Mirror of beside right |
| Above | Centered over the tube, with 12px between the glyph box and the top of the outline geometry |
| Below | Centered under the bulb, with 12px between the bottom of the outline geometry and the glyph box |

The gaps refer to glyph boxes and path geometry, before stroke expansion; Lucide's internal whitespace remains unchanged. Above and below deliberately have more space than the side placements.

## Sunrise and Sunset

* Center two stacked rows beneath the thermometer stage: sunrise first, sunset second. Size the group to its contents and separate the rows by 8px.
* Each row contains a 16px Lucide `Sunrise` or `Sunset` glyph, followed by its clock time on the right with an 8px gap. Align the glyph and time vertically at their centers. Use `--muted` for the glyph and retain its 16px size when the weather glyph is resized.
* Format times as zero-padded 24-hour `HH:mm` values. Use a `<time>` element with a matching `datetime`, 14px `--mono`, 1.5 line-height, tabular numbers, and `--ink` text. Each row's hover title identifies the event and its time.
* Keep both times visible during the day and at night. Update their visible text, `datetime`, row titles, and the summary's accessible description whenever the supplied event times change. The sunrise/sunset display contains no progress track, marker, or night indicator.
* Accept local minutes since midnight for the current time, sunrise, and sunset. The compact reference supports a same-day sunrise before sunset. The caller must resolve location, date, timezone, and real event times; do not assume equal day/night lengths.
* At sunrise, switch the weather glyph to its daytime form; at sunset, switch to its night form. Calculate the next sunrise across midnight with a 24-hour wrap for the accessible description. Do not animate independently of supplied time or fetch weather/location data in the component.

## Runnable Reference and Reuse

Open [16 Weather](../assets/component-reference/index.html#weather) for one configurable preview. Its controls select clear/cloudy/rain/snow weather, time in 15-minute steps, temperature from 0–40°C, glyph size from 50–200% in 5% steps, and the six placements above. The fictional seven-day readings are 18, 20, 22, 21, 23, 24, and 19°C (mean 21°C); sunrise is 06:30 and sunset 19:00.

* Edit `components/weather.html`, `weather.css`, `weather.js`, and `weather-example.js`. The example owns all controls and sample data; the renderer has no catalog-ID, server, or prototype-directory dependency.
* Keep condition and placement selections visible with neutral pressed-button states and `aria-pressed`. Give every slider a visible label, associated output, descriptive `aria-valuetext`, and standard keyboard operation. Reset restored controls to the same defaults as the preview on initialization.
* Keep the summary beside the controls on wider screens. At 700px and below, stack the summary above the controls. Let weather choices wrap, keep placement choices in two columns, and stack the time/temperature sliders when their columns would be too narrow. Support a 320px viewport and 200% zoom without page overflow.
* Load shared tokens, base styles, the Lucide sprite and `shared/icons.js`, followed by `weather.css` and `weather.js`. The catalog's exploration controls additionally use the shared button and range styles. Rebuild the generated catalog after HTML edits.

```js
const weather = ComponentReference.createWeather(container, {
  temperatureCelsius: 21,
  previousWeekCelsius: [18, 20, 22, 21, 23, 24, 19],
  minute: 840,
  sunrise: 390,
  sunset: 1140,
  condition: "clear",
  glyphSize: 32,
  placement: "close-right"
});
weather.update({ minute: 1200, glyphSize: 48, placement: "above" });
// Remove this instance when its containing view is retired.
weather.destroy();
```

`update(patch)` changes only supplied options and returns `{ average, level, mode, isDay }`. Each instance owns its data and DOM; it can coexist with other instances. Invalid readings, event times, condition, placement, or glyph size throw before changing the rendered state. Omitted condition, glyph size, and placement default to clear, 32px, and close right. Supply all temperature and time inputs explicitly.
