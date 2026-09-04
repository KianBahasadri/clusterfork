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
}(window.ComponentReference.createLucideIcon, window.ComponentReference.spawnToast));
