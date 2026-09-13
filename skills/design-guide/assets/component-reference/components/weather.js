(function (reference) {
  "use strict";

  const modes = ["Far below average", "Below average", "Around average", "Above average", "Far above average"];
  const conditions = { clear: "Clear", cloudy: "Cloudy", rain: "Rain", snow: "Snow" };
  const placements = ["close-right", "close-left", "beside-right", "beside-left", "above", "below"];
  const dayLength = 1440;

  function clockTime(minute) {
    return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
  }

  function validate(options) {
    if (!Number.isFinite(options.temperatureCelsius) || !Array.isArray(options.previousWeekCelsius)
      || options.previousWeekCelsius.length !== 7 || !options.previousWeekCelsius.every(Number.isFinite)) {
      throw new TypeError("Weather requires a current temperature and seven finite previous-day temperatures in Celsius.");
    }
    if (![options.minute, options.sunrise, options.sunset].every(value => Number.isInteger(value) && value >= 0 && value < dayLength)
      || options.sunrise >= options.sunset) {
      throw new RangeError("Weather times must be local minutes within one day, with sunrise before sunset.");
    }
    if (!Object.hasOwn(conditions, options.condition) || !placements.includes(options.placement)
      || !Number.isFinite(options.glyphSize) || options.glyphSize < 16 || options.glyphSize > 64) {
      throw new RangeError("Weather requires a supported condition, placement, and glyph size between 16 and 64px.");
    }
    return { ...options, previousWeekCelsius: [...options.previousWeekCelsius] };
  }

  function createWeather(container, options) {
    let state = validate({ condition: "clear", placement: "close-right", glyphSize: 32, ...options });
    const root = document.createElement("div");
    root.className = "weather-summary";
    root.setAttribute("role", "img");
    root.innerHTML = `
      <div class="weather-stage" aria-hidden="true">
        <div class="weather-composition">
          <svg class="weather-thermometer" viewBox="14 0 48 184" aria-hidden="true" focusable="false">
            <path class="weather-thermometer-outline" d="M30 142V16a8 8 0 0 1 16 0v126a20 20 0 1 1-16 0Z"></path>
            <rect class="weather-thermometer-fill" x="35" y="80" width="6" height="80" rx="3"></rect>
            <circle class="weather-thermometer-bulb" cx="38" cy="160" r="11"></circle>
          </svg>
        </div>
      </div>
      <div class="weather-sun-times" aria-hidden="true"></div>`;

    function icon(name, className) {
      const element = reference.createLucideIcon(name, `icon ${className || ""}`);
      element.setAttribute("viewBox", "0 0 24 24");
      element.setAttribute("focusable", "false");
      return element;
    }

    const glyph = icon("sun", "weather-glyph");
    root.querySelector(".weather-composition").appendChild(glyph);
    const sunTimes = root.querySelector(".weather-sun-times");
    const sunEvents = ["sunrise", "sunset"].map(name => {
      const row = document.createElement("div");
      row.className = "weather-sun-event";
      const time = document.createElement("time");
      row.append(icon(name), time);
      sunTimes.appendChild(row);
      return { name, row, time };
    });
    const fill = root.querySelector(".weather-thermometer-fill");

    function update(patch = {}) {
      state = validate({ ...state, ...patch });
      const average = state.previousWeekCelsius.reduce((sum, value) => sum + value, 0) / 7;
      const difference = state.temperatureCelsius - average;
      const level = difference <= -6 ? 0 : difference < -2 ? 1 : difference <= 2 ? 2 : difference < 6 ? 3 : 4;
      const mode = modes[level];
      // Far below leaves only the bulb; far above reaches the top of the tube.
      const top = level === 0 ? 160 : 148 - level / 4 * 136;
      fill.setAttribute("y", String(top));
      fill.setAttribute("height", String(160 - top));
      root.dataset.level = String(level);
      root.dataset.placement = state.placement;
      root.style.setProperty("--weather-glyph-size", `${state.glyphSize}px`);

      const isDay = state.minute >= state.sunrise && state.minute < state.sunset;
      const glyphName = state.condition === "clear" ? (isDay ? "sun" : "moon")
        : state.condition === "cloudy" ? (isDay ? "cloud-sun" : "cloud-moon")
          : state.condition === "rain" ? "cloud-rain" : "snowflake";
      glyph.querySelector("use").setAttribute("href", `#lucide-${glyphName}`);
      root.dataset.daylight = String(isDay);
      sunEvents.forEach(({ name, row, time }) => {
        const value = clockTime(state[name]);
        time.dateTime = value;
        time.textContent = value;
        row.title = `${name === "sunrise" ? "Sunrise" : "Sunset"} at ${value}`;
      });

      const nextEvent = isDay ? "Sunset" : "Sunrise";
      const until = ((isDay ? state.sunset : state.sunrise) - state.minute + dayLength) % dayLength;
      const description = `${mode} compared with the average temperature of the previous seven days. `
        + `${conditions[state.condition]}. ${isDay ? "Daytime" : "Nighttime"}, ${clockTime(state.minute)}. `
        + `Sunrise ${clockTime(state.sunrise)}; sunset ${clockTime(state.sunset)}. `
        + `${nextEvent} in ${Math.floor(until / 60)} hours and ${until % 60} minutes.`;
      root.setAttribute("aria-label", description);
      root.setAttribute("title", description);
      return { average, level, mode, isDay };
    }

    update();
    container.appendChild(root);
    return { update, destroy() { root.remove(); } };
  }

  reference.createWeather = createWeather;
}(window.ComponentReference = window.ComponentReference || {}));
