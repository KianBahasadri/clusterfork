(function (createLucideIcon, spawnToast) {
  // Clear search button
  var searchInput = document.getElementById("inputSearch");
  var clearBtn = document.getElementById("btnClearSearch");
  clearBtn.addEventListener("click", function () {
    searchInput.value = "";
    searchInput.focus();
  });

  // Submit-triggered email validation
  var emailValidationForm = document.getElementById("emailValidationForm");
  var validationEmail = document.getElementById("validationEmail");
  var validationEmailSubmit = document.getElementById("validationEmailSubmit");
  var validationEmailError = document.getElementById("validationEmailError");
  var emailValidationPending = false;

  function resetEmailValidation() {
    validationEmail.classList.remove("is-invalid");
    validationEmail.removeAttribute("aria-invalid");
    validationEmailError.hidden = true;
    validationEmailError.textContent = "";
  }

  emailValidationForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (emailValidationPending) return;
    if (!validationEmail.checkValidity()) {
      validationEmail.classList.add("is-invalid");
      validationEmail.setAttribute("aria-invalid", "true");
      validationEmailError.textContent = validationEmail.validity.valueMissing
        ? "Enter an email address"
        : "Enter a valid email address";
      validationEmailError.hidden = false;
      validationEmail.focus();
      return;
    }

    var returnFocus = document.activeElement;
    emailValidationPending = true;
    resetEmailValidation();
    validationEmail.readOnly = true;
    validationEmailSubmit.style.minWidth = validationEmailSubmit.getBoundingClientRect().width + "px";
    validationEmailSubmit.disabled = true;
    validationEmailSubmit.setAttribute("aria-busy", "true");
    validationEmailSubmit.replaceChildren(
      createLucideIcon("loader-circle", "icon spinner"),
      document.createTextNode("Submitting…")
    );

    window.setTimeout(function () {
      spawnToast("Notification email submitted successfully.", false);
      validationEmail.value = "";
      validationEmail.readOnly = false;
      resetEmailValidation();
      validationEmailSubmit.disabled = false;
      validationEmailSubmit.setAttribute("aria-busy", "false");
      validationEmailSubmit.textContent = "Validate Email";
      validationEmailSubmit.style.minWidth = "";
      emailValidationPending = false;
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus({ preventScroll: true });
    }, 1200);
  });

  validationEmail.addEventListener("input", resetEmailValidation);

  // Indeterminate checkbox demo
  var indeterminateBox = document.getElementById("indeterminateBox");
  if (indeterminateBox) indeterminateBox.indeterminate = true;

  // Slider values and selected-track fill
  Array.prototype.forEach.call(document.querySelectorAll(".range-slider[data-range-output]"), function (slider) {
    var output = document.getElementById(slider.getAttribute("data-range-output"));

    function syncSlider() {
      var minimum = Number(slider.min);
      var maximum = Number(slider.max);
      var current = Number(slider.value);
      var progress = maximum === minimum ? 0 : ((current - minimum) / (maximum - minimum)) * 100;
      var suffix = slider.getAttribute("data-range-suffix") || "";
      var ariaUnit = slider.getAttribute("data-range-aria-unit");
      var displayValue = slider.hasAttribute("data-range-grouped")
        ? current.toLocaleString("en-US")
        : slider.value;

      slider.style.setProperty("--range-progress", progress + "%");
      if (output) output.value = displayValue + suffix;
      if (ariaUnit) slider.setAttribute("aria-valuetext", slider.value + " " + ariaUnit);
    }

    slider.addEventListener("input", syncSlider);
    syncSlider();
  });

  // Large-range pointer scrubbing: vertical distance selects a finer horizontal rate.
  Array.prototype.forEach.call(document.querySelectorAll(".range-slider-precision"), function (slider) {
    var scrubRate = document.getElementById(slider.getAttribute("data-range-scrub-rate"));
    var scrubStatus = document.getElementById(slider.getAttribute("data-range-scrub-status"));
    var activePointer = null;
    var dragValue = Number(slider.value);
    var initialValue = slider.value;
    var lastX = 0;
    var originY = 0;
    var unitsPerPixel = 1;

    function scrubBand(distance) {
      if (distance <= 24) return { multiplier: 1, label: "Scrub 1×", status: "Normal scrub speed" };
      if (distance <= 72) return { multiplier: 0.25, label: "Scrub 0.25×", status: "Quarter scrub speed" };
      if (distance <= 144) return { multiplier: 0.1, label: "Scrub 0.1×", status: "Tenth scrub speed" };
      return { multiplier: 0.02, label: "Scrub 0.02×", status: "Fine scrub speed" };
    }

    function updateScrubBand(band) {
      if (scrubRate && scrubRate.textContent !== band.label) scrubRate.textContent = band.label;
      if (scrubStatus && scrubStatus.textContent !== band.status) scrubStatus.textContent = band.status;
    }

    function setSliderValue(value) {
      var minimum = Number(slider.min);
      var maximum = Number(slider.max);
      var step = Number(slider.step) || 1;
      var clamped = Math.max(minimum, Math.min(maximum, value));
      var snapped = minimum + Math.round((clamped - minimum) / step) * step;
      var nextValue = String(Math.max(minimum, Math.min(maximum, snapped)));

      dragValue = clamped;
      if (slider.value === nextValue) return;
      slider.value = nextValue;
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    }

    slider.addEventListener("pointerdown", function (event) {
      if (slider.disabled || event.button !== 0) return;
      event.preventDefault();

      var minimum = Number(slider.min);
      var maximum = Number(slider.max);
      var bounds = slider.getBoundingClientRect();
      var trackInset = 9;
      var trackStart = bounds.left + trackInset;
      var trackWidth = Math.max(1, bounds.width - trackInset * 2);
      var pointerRatio = Math.max(0, Math.min(1, (event.clientX - trackStart) / trackWidth));
      var currentRatio = (Number(slider.value) - minimum) / (maximum - minimum);
      var currentX = trackStart + currentRatio * trackWidth;

      activePointer = event.pointerId;
      initialValue = slider.value;
      dragValue = Math.abs(event.clientX - currentX) <= 12
        ? Number(slider.value)
        : minimum + pointerRatio * (maximum - minimum);
      lastX = event.clientX;
      originY = event.clientY;
      unitsPerPixel = (maximum - minimum) / trackWidth;

      slider.focus({ preventScroll: true });
      slider.setPointerCapture(event.pointerId);
      setSliderValue(dragValue);
      updateScrubBand(scrubBand(0));
    });

    slider.addEventListener("pointermove", function (event) {
      if (event.pointerId !== activePointer) return;
      event.preventDefault();
      var band = scrubBand(Math.abs(event.clientY - originY));
      dragValue += (event.clientX - lastX) * unitsPerPixel * band.multiplier;
      lastX = event.clientX;
      setSliderValue(dragValue);
      updateScrubBand(band);
    });

    function finishScrub(event) {
      if (event.pointerId !== activePointer) return;
      activePointer = null;
      if (slider.hasPointerCapture(event.pointerId)) slider.releasePointerCapture(event.pointerId);
      if (slider.value !== initialValue) slider.dispatchEvent(new Event("change", { bubbles: true }));
      updateScrubBand(scrubBand(0));
    }

    slider.addEventListener("pointerup", finishScrub);
    slider.addEventListener("pointercancel", finishScrub);
    slider.addEventListener("lostpointercapture", finishScrub);
  });
}(window.ComponentReference.createLucideIcon, window.ComponentReference.spawnToast));
