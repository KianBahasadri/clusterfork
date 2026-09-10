(function (reference) {
  "use strict";

  const section = document.getElementById("weather");
  if (!section) return;
  const form = section.querySelector("form");
  const time = document.getElementById("weatherTime");
  const temperature = document.getElementById("weatherTemperature");
  const size = document.getElementById("weatherGlyphSize");
  // Reset restored Firefox controls to the same defaults as the preview.
  form.reset();
  let condition = "clear";
  let placement = "close-right";
  const weather = reference.createWeather(document.getElementById("weatherExample"), {
    // Fictional representative readings for seven complete days, excluding today.
    previousWeekCelsius: [18, 20, 22, 21, 23, 24, 19],
    temperatureCelsius: Number(temperature.value),
    minute: Number(time.value),
    sunrise: 390,
    sunset: 1140
  });

  function slider(input, output, text, accessibleText) {
    document.getElementById(output).textContent = text;
    input.setAttribute("aria-valuetext", accessibleText || text);
    input.style.setProperty("--range-progress", `${(Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100}%`);
  }

  function render() {
    const reading = weather.update({
      temperatureCelsius: Number(temperature.value),
      minute: Number(time.value),
      glyphSize: Number(size.value) / 100 * 32,
      condition,
      placement
    });
    const minute = Number(time.value);
    const clock = `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
    slider(time, "weatherTimeOutput", clock, `${clock}, ${reading.isDay ? "daytime" : "nighttime"}`);
    slider(temperature, "weatherTemperatureOutput", `${temperature.value}°C`, `${temperature.value} degrees Celsius, ${reading.mode.toLowerCase()}`);
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
