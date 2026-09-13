(function (reference) {
  "use strict";

  const section = document.getElementById("weather");
  if (!section) return;
  const form = section.querySelector("form");
  const time = document.getElementById("weatherTime");
  const temperature = document.getElementById("weatherTemperature");
  const uvIndex = document.getElementById("weatherUvIndex");
  const rainChance = document.getElementById("weatherRainChance");
  const size = document.getElementById("weatherGlyphSize");
  const showSunTimes = document.getElementById("weatherShowSunTimes");
  const showUvIndex = document.getElementById("weatherShowUvIndex");
  const showRainChance = document.getElementById("weatherShowRainChance");
  const hour12 = document.getElementById("weatherHour12");
  const sunrise = 390;
  const sunset = 1140;
  // Reset restored Firefox controls to the same defaults as the preview.
  form.reset();
  let condition = "clear";
  let placement = "right-icon-above";
  const weather = reference.createWeather(document.getElementById("weatherExample"), {
    // Fictional representative readings for seven complete days, excluding today.
    previousWeekCelsius: [18, 20, 22, 21, 23, 24, 19],
    temperatureCelsius: Number(temperature.value),
    minute: Number(time.value),
    sunrise,
    sunset,
    hour12: hour12.checked,
    uvIndex: Number(uvIndex.value),
    rainChancePercent: Number(rainChance.value),
    showSunTimes: showSunTimes.checked,
    showUvIndex: showUvIndex.checked,
    showRainChance: showRainChance.checked
  });

  function slider(input, output, text, accessibleText) {
    document.getElementById(output).textContent = text;
    input.setAttribute("aria-valuetext", accessibleText || text);
    input.style.setProperty("--range-progress", `${(Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100}%`);
  }

  function render() {
    const reading = weather.update({
      temperatureCelsius: Number(temperature.value),
      uvIndex: Number(uvIndex.value),
      rainChancePercent: Number(rainChance.value),
      minute: Number(time.value),
      hour12: hour12.checked,
      glyphSize: Number(size.value) / 100 * 32,
      showSunTimes: showSunTimes.checked,
      showUvIndex: showUvIndex.checked,
      showRainChance: showRainChance.checked,
      condition,
      placement
    });
    const minute = Number(time.value);
    const clock = reference.formatWeatherTime(minute, hour12.checked);
    document.getElementById("weatherDaylightReference").textContent =
      `Sunrise ${reference.formatWeatherTime(sunrise, hour12.checked)} · Sunset ${reference.formatWeatherTime(sunset, hour12.checked)}`;
    slider(time, "weatherTimeOutput", clock, `${clock}, ${reading.isDay ? "daytime" : "nighttime"}`);
    slider(temperature, "weatherTemperatureOutput", `${temperature.value}°C`, `${temperature.value} degrees Celsius, ${reading.mode.toLowerCase()}`);
    slider(uvIndex, "weatherUvIndexOutput", uvIndex.value, `UV index ${uvIndex.value}`);
    slider(rainChance, "weatherRainChanceOutput", `${rainChance.value}%`, `${rainChance.value} percent chance of rain`);
    slider(size, "weatherGlyphSizeOutput", `${size.value}%`, `${size.value} percent`);
    section.querySelectorAll("[data-weather-condition]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.weatherCondition === condition));
    });
    section.querySelectorAll("[data-weather-placement]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.weatherPlacement === placement));
    });
  }

  form.addEventListener("submit", event => event.preventDefault());
  form.addEventListener("input", render);
  form.addEventListener("click", event => {
    const button = event.target.closest("[data-weather-condition], [data-weather-placement]");
    if (!button) return;
    if (button.dataset.weatherCondition) condition = button.dataset.weatherCondition;
    if (button.dataset.weatherPlacement) placement = button.dataset.weatherPlacement;
    render();
  });
  render();
}(window.ComponentReference));
