# Weather exploration

Open `index.html` directly in a browser to compare fourteen weather displays. The
first three are a compact strip, a daylight arc, and a vertical thermometer.
The next three combine a visual temperature comparison with a daylight track,
sunrise/sunset rings, or a turning sun-and-moon dial. The final eight explore
compact glyph placement. The adopted component now lives in
[16 Weather](../component-reference/index.html#weather), with its canonical
rules in [Weather](../../references/weather.md). This page retains the earlier
design explorations.

The weather buttons select fixed sample temperatures and conditions. The time
slider moves through a sample local day in 15-minute steps. Sunrise is 06:30 and
sunset is 19:00; after sunset, the next event is the following day's sunrise.
The arc illustrates elapsed daylight, not the sun's astronomical elevation.
Changing units converts the readings and the thermometer's scale together.

## Temperature relative to the previous week

The visual comparisons show no persistent text or numbers inside the
component. Their thermometer fills snap to five levels: bulb only, one-quarter
of the tube, half the tube, three-quarters of the tube, and the full tube. The
half-full level represents the previous week's average. The tube has no side
notches or indicator dot. Accessible names and hover titles identify the state
and weather condition.

`relative-weather.js` calculates the arithmetic mean of seven sample daily
temperatures, oldest first: 18, 20, 22, 21, 23, 24, and 19°C. Their mean is 21°C.
The current temperature is excluded. The illustrative classification uses the
difference from that mean, calculated in Celsius before any display conversion:

| Mode | Difference from the mean |
| --- | --- |
| Far below average | −6°C or lower |
| Below average | Above −6°C and below −2°C |
| Around average | −2°C through +2°C, inclusive |
| Above average | Above +2°C and below +6°C |
| Far above average | +6°C or higher |

The comparison buttons choose sample readings at −8, −4, 0, +4, and +8°C from
the calculated mean. The weather-glyph controls change the condition
independently. All time sliders control the same sample time. All displays
share the current reading and weather; changing °C/°F never changes a relative
mode.

The daylight track spans the full local day. Dotted segments represent night;
the solid segment lies between the sunrise and sunset markers. A moving sun or
moon locates the current time. Each event ring fills clockwise over the 24 hours
toward its next sunrise or sunset, then resets when the event occurs. The dial
places the sun and moon opposite one another and keeps their glyphs upright
while rotating across a fixed horizon. Its daytime half-turn lasts from
sunrise to sunset; the other half-turn lasts until the next sunrise. The moon
is a night indicator, not a model of lunar position. None of these displays
animates independently of the sample time.

## Compact glyph placement

Examples 07–14 place the glyph close to the right or left of the tube, at its
upper corner, beside the bulb, overlapping the bulb, inside the bulb, above
the tube, or below the bulb. The thermometer geometry and its five fill levels
remain the same. The overlapping version gives the glyph a surface-colored
backplate; the version inside the bulb uses a smaller inverted glyph. Other
placements start with a 32px glyph. The weather-glyph size slider scales the
eight placements from 50% to 200%, updating continuously while preserving each
attachment point. The glyph inside the bulb stays centered and is capped at
20px so it fits; the thermometer and sunrise/sunset glyphs retain their sizes.

These samples crop unused horizontal space from the thermometer's SVG viewport
and use a 160px component boundary at the default glyph size on desktop. Each includes the same compact
sunrise/sunset strip. During daylight its marker advances from sunrise to
sunset; at night the marker is replaced by a moon. Preview controls beside this
group update the same weather, relative temperature, and time as the rest of
the page. The grid uses four columns on desktop and two on smaller screens,
falling back to one column when enlarged glyphs need more width.

## Editing

Edit `index.html`, the CSS files, and the two JavaScript files directly. Keep
this directory beside `component-reference/`, which supplies the shared tokens, base styling,
and command palette behavior. Icons are vendored Lucide SVGs; their ISC license
is included here. No weather service or location access is used.
