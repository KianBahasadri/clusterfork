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
  // The rate popup sits above the thumb while dragging; double-click or Enter types a value.
  Array.prototype.forEach.call(document.querySelectorAll(".range-slider-precision"), function (slider) {
    var scrubStatus = document.getElementById(slider.getAttribute("data-range-scrub-status"));
    var popup = document.createElement("div");
    var rateLabel = document.createElement("span");
    var valueInput = document.createElement("input");
    var sliderLabel = slider.id ? document.querySelector("label[for=\"" + slider.id + "\"]") : null;
    var activePointer = null;
    var dragValue = Number(slider.value);
    var initialValue = slider.value;
    var lastX = 0;
    var originY = 0;
    var unitsPerPixel = 1;
    var editing = false;
    var dragMoved = false;
    var startedOnThumb = false;
    var pendingThumbClick = false;
    var pendingThumbAt = 0;
    var pendingThumbX = 0;
    var inputWidthCh = Math.max(4, Math.max(String(slider.min).length, String(slider.max).length) + 1);

    popup.className = "range-thumb-popup";
    popup.hidden = true;
    popup.setAttribute("aria-hidden", "true");
    rateLabel.className = "range-thumb-popup-rate";
    valueInput.className = "range-thumb-popup-input";
    valueInput.type = "text";
    valueInput.inputMode = "decimal";
    valueInput.autocomplete = "off";
    valueInput.spellcheck = false;
    valueInput.hidden = true;
    valueInput.style.width = inputWidthCh + "ch";
    valueInput.setAttribute("aria-label", (sliderLabel ? sliderLabel.textContent : "Value") + " value");
    popup.appendChild(rateLabel);
    popup.appendChild(valueInput);
    slider.parentNode.appendChild(popup);

    function trackGeometry() {
      var minimum = Number(slider.min);
      var maximum = Number(slider.max);
      var bounds = slider.getBoundingClientRect();
      var trackInset = 9;
      var trackStart = bounds.left + trackInset;
      var trackWidth = Math.max(1, bounds.width - trackInset * 2);
      var ratio = maximum === minimum ? 0 : (Number(slider.value) - minimum) / (maximum - minimum);
      return {
        minimum: minimum,
        maximum: maximum,
        bounds: bounds,
        trackStart: trackStart,
        trackWidth: trackWidth,
        thumbX: trackStart + ratio * trackWidth,
        thumbY: bounds.top + bounds.height / 2
      };
    }

    function isOnThumb(event) {
      var geometry = trackGeometry();
      return Math.abs(event.clientX - geometry.thumbX) <= 12
        && Math.abs(event.clientY - geometry.thumbY) <= geometry.bounds.height / 2;
    }

    function positionPopup() {
      if (popup.hidden) return;
      var geometry = trackGeometry();
      var viewport = window.visualViewport;
      var viewLeft = viewport ? viewport.offsetLeft : 0;
      var viewTop = viewport ? viewport.offsetTop : 0;
      var viewWidth = viewport ? viewport.width : document.documentElement.clientWidth;
      var viewHeight = viewport ? viewport.height : window.innerHeight;
      var width = popup.offsetWidth;
      var height = popup.offsetHeight;
      var x = geometry.thumbX - width / 2;
      var y = geometry.thumbY - 9 - 8 - height;
      if (y < viewTop + 12) y = geometry.thumbY + 9 + 8;
      popup.style.left = Math.max(viewLeft + 12, Math.min(viewLeft + viewWidth - width - 12, x)) + "px";
      popup.style.top = Math.max(viewTop + 12, Math.min(viewTop + viewHeight - height - 12, y)) + "px";
    }

    function scrubBand(distance) {
      if (distance <= 24) return { multiplier: 1, label: "Speed 1×", status: "Normal speed" };
      if (distance <= 72) return { multiplier: 0.25, label: "Speed 0.25×", status: "Quarter speed" };
      if (distance <= 144) return { multiplier: 0.1, label: "Speed 0.1×", status: "Tenth speed" };
      return { multiplier: 0.02, label: "Speed 0.02×", status: "Fine speed" };
    }

    function showRatePopup(band) {
      if (editing) return;
      if (rateLabel.textContent !== band.label) rateLabel.textContent = band.label;
      if (scrubStatus && scrubStatus.textContent !== band.status) scrubStatus.textContent = band.status;
      valueInput.hidden = true;
      rateLabel.hidden = false;
      popup.classList.remove("is-editing");
      popup.style.visibility = "hidden";
      popup.hidden = false;
      popup.setAttribute("aria-hidden", "true");
      positionPopup();
      popup.style.visibility = "";
    }

    function hideRatePopup() {
      if (editing) return;
      popup.hidden = true;
      popup.style.visibility = "";
      popup.classList.remove("is-editing");
      popup.setAttribute("aria-hidden", "true");
      var rest = scrubBand(0);
      if (rateLabel.textContent !== rest.label) rateLabel.textContent = rest.label;
      if (scrubStatus && scrubStatus.textContent !== rest.status) scrubStatus.textContent = rest.status;
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

    function parseEnteredValue(text) {
      var match = String(text).replace(/,/g, "").match(/[+-]?\d*\.?\d+/);
      if (!match) return null;
      var parsed = Number(match[0]);
      return isFinite(parsed) ? parsed : null;
    }

    function closeEditor(restoreFocus) {
      if (!editing) return;
      editing = false;
      valueInput.hidden = true;
      rateLabel.hidden = false;
      popup.classList.remove("is-editing");
      popup.hidden = true;
      popup.style.visibility = "";
      popup.setAttribute("aria-hidden", "true");
      document.removeEventListener("pointerdown", onDocumentPointerDown, true);
      if (restoreFocus) slider.focus({ preventScroll: true });
    }

    function commitEditor() {
      if (!editing) return;
      var parsed = parseEnteredValue(valueInput.value);
      var previous = slider.value;
      closeEditor(true);
      if (parsed == null) return;
      setSliderValue(parsed);
      if (slider.value !== previous) slider.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function cancelEditor() {
      if (!editing) return;
      closeEditor(true);
    }

    function onDocumentPointerDown(event) {
      if (!editing || popup.contains(event.target)) return;
      commitEditor();
    }

    function openEditor() {
      if (slider.disabled || editing) return;
      if (activePointer !== null) return;
      editing = true;
      rateLabel.hidden = true;
      valueInput.hidden = false;
      valueInput.value = slider.value;
      popup.classList.add("is-editing");
      popup.style.visibility = "hidden";
      popup.hidden = false;
      popup.removeAttribute("aria-hidden");
      document.addEventListener("pointerdown", onDocumentPointerDown, true);
      positionPopup();
      popup.style.visibility = "";
      valueInput.focus({ preventScroll: true });
      valueInput.select();
    }

    slider.addEventListener("pointerdown", function (event) {
      if (slider.disabled || event.button !== 0) return;
      if (editing) {
        event.preventDefault();
        return;
      }
      if (isOnThumb(event) && pendingThumbClick
          && event.timeStamp - pendingThumbAt <= 500
          && Math.abs(event.clientX - pendingThumbX) <= 8) {
        event.preventDefault();
        pendingThumbClick = false;
        openEditor();
        return;
      }
      pendingThumbClick = false;
      event.preventDefault();

      var geometry = trackGeometry();
      var pointerRatio = Math.max(0, Math.min(1, (event.clientX - geometry.trackStart) / geometry.trackWidth));

      activePointer = event.pointerId;
      initialValue = slider.value;
      startedOnThumb = isOnThumb(event);
      dragMoved = false;
      dragValue = startedOnThumb
        ? Number(slider.value)
        : geometry.minimum + pointerRatio * (geometry.maximum - geometry.minimum);
      lastX = event.clientX;
      originY = event.clientY;
      unitsPerPixel = (geometry.maximum - geometry.minimum) / geometry.trackWidth;

      slider.focus({ preventScroll: true });
      slider.setPointerCapture(event.pointerId);
      setSliderValue(dragValue);
      showRatePopup(scrubBand(0));
    });

    slider.addEventListener("pointermove", function (event) {
      if (event.pointerId !== activePointer) return;
      event.preventDefault();
      if (Math.abs(event.clientX - lastX) > 3 || Math.abs(event.clientY - originY) > 3) dragMoved = true;
      var band = scrubBand(Math.abs(event.clientY - originY));
      dragValue += (event.clientX - lastX) * unitsPerPixel * band.multiplier;
      lastX = event.clientX;
      setSliderValue(dragValue);
      showRatePopup(band);
    });

    function finishScrub(event) {
      if (event.pointerId !== activePointer) return;
      var wasClickOnThumb = startedOnThumb && !dragMoved && event.type !== "pointercancel";
      activePointer = null;
      if (slider.hasPointerCapture(event.pointerId)) slider.releasePointerCapture(event.pointerId);
      if (slider.value !== initialValue) slider.dispatchEvent(new Event("change", { bubbles: true }));
      hideRatePopup();
      pendingThumbClick = wasClickOnThumb;
      pendingThumbAt = event.timeStamp;
      pendingThumbX = event.clientX;
    }

    popup.addEventListener("pointerdown", function (event) {
      if (event.target !== valueInput) event.preventDefault();
    });
    slider.addEventListener("pointerup", finishScrub);
    slider.addEventListener("pointercancel", finishScrub);
    slider.addEventListener("lostpointercapture", finishScrub);
    slider.addEventListener("keydown", function (event) {
      if (slider.disabled || editing || event.key !== "Enter" || event.repeat) return;
      event.preventDefault();
      openEditor();
    });
    valueInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        commitEditor();
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancelEditor();
      }
    });
    valueInput.addEventListener("blur", function () {
      if (!editing) return;
      window.setTimeout(function () {
        if (editing && document.activeElement !== valueInput) commitEditor();
      }, 0);
    });
    window.addEventListener("resize", positionPopup);
    window.addEventListener("scroll", positionPopup, true);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", positionPopup);
      window.visualViewport.addEventListener("scroll", positionPopup);
    }
  });
}(window.ComponentReference.createLucideIcon, window.ComponentReference.spawnToast));
