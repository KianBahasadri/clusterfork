(function (createLucideIcon, spawnToast) {
  // Loading button demo
  var btnLoading = document.getElementById("btnLoadingDemo");
  btnLoading.addEventListener("click", function () {
    btnLoading.disabled = true;
    btnLoading.setAttribute("aria-busy", "true");
    btnLoading.replaceChildren(createLucideIcon("loader-circle", "icon spinner"), document.createTextNode("Saving..."));
    setTimeout(function () {
      btnLoading.disabled = false;
      btnLoading.setAttribute("aria-busy", "false");
      btnLoading.replaceChildren(createLucideIcon("check"), document.createTextNode("Saved"));
      setTimeout(function () {
        btnLoading.textContent = "Click for Loading";
      }, 1500);
    }, 1200);
  });

  // Dwell-to-arm destructive button demo
  var dwellDangerButton = document.getElementById("btnDwellDanger");
  var dwellDangerStatus = document.getElementById("dwellDangerStatus");
  var dwellDangerTimer = 0;
  var dwellDangerMode = "";
  var dwellDangerPointerId = null;
  var ignoreNextDwellDangerClick = false;

  function isDwellDangerArmed() {
    return dwellDangerButton.classList.contains("is-armed");
  }

  function beginDwellDanger(mode) {
    if (dwellDangerTimer || isDwellDangerArmed()) return;
    dwellDangerMode = mode;
    dwellDangerButton.classList.add("is-arming");
    dwellDangerStatus.textContent = "Delete Workspace is arming";
    dwellDangerTimer = window.setTimeout(function () {
      dwellDangerTimer = 0;
      dwellDangerButton.classList.add("is-armed");
      dwellDangerStatus.textContent = mode === "touch"
        ? "Delete Workspace is ready. Release to activate"
        : "Delete Workspace is ready. Activate the button to continue";
    }, 1000);
  }

  function resetDwellDanger(message) {
    if (dwellDangerTimer) window.clearTimeout(dwellDangerTimer);
    dwellDangerTimer = 0;
    dwellDangerMode = "";
    dwellDangerPointerId = null;
    dwellDangerButton.classList.remove("is-arming", "is-armed");
    dwellDangerStatus.textContent = message || "";
  }

  function activateDwellDanger() {
    resetDwellDanger("Delete Workspace activated");
    spawnToast("Workspace deletion authorized.", false, "danger");
  }

  dwellDangerButton.addEventListener("pointerenter", function (event) {
    if (event.pointerType !== "touch") beginDwellDanger("pointer");
  });

  dwellDangerButton.addEventListener("pointerleave", function () {
    if (dwellDangerMode === "pointer") resetDwellDanger("Delete Workspace disarmed");
  });

  dwellDangerButton.addEventListener("focus", function () {
    if (dwellDangerMode !== "pointer" && dwellDangerMode !== "touch") beginDwellDanger("keyboard");
  });

  dwellDangerButton.addEventListener("blur", function () {
    if (dwellDangerMode === "keyboard") resetDwellDanger("Delete Workspace disarmed");
  });

  dwellDangerButton.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "touch") return;
    event.preventDefault();
    dwellDangerPointerId = event.pointerId;
    dwellDangerButton.setPointerCapture(event.pointerId);
    beginDwellDanger("touch");
  });

  dwellDangerButton.addEventListener("pointermove", function (event) {
    if (dwellDangerMode !== "touch" || event.pointerId !== dwellDangerPointerId) return;
    var bounds = dwellDangerButton.getBoundingClientRect();
    var isOutside = event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom;
    if (isOutside) resetDwellDanger("Delete Workspace disarmed");
  });

  dwellDangerButton.addEventListener("pointerup", function (event) {
    if (dwellDangerMode !== "touch" || event.pointerId !== dwellDangerPointerId) return;
    event.preventDefault();
    if (dwellDangerButton.hasPointerCapture(event.pointerId)) {
      dwellDangerButton.releasePointerCapture(event.pointerId);
    }
    ignoreNextDwellDangerClick = true;
    if (isDwellDangerArmed()) activateDwellDanger();
    else resetDwellDanger("Keep holding for one second");
    window.setTimeout(function () { ignoreNextDwellDangerClick = false; }, 0);
  });

  dwellDangerButton.addEventListener("pointercancel", function (event) {
    if (event.pointerId === dwellDangerPointerId) resetDwellDanger("Delete Workspace disarmed");
  });

  dwellDangerButton.addEventListener("click", function (event) {
    if (ignoreNextDwellDangerClick) {
      event.preventDefault();
      return;
    }
    if (!isDwellDangerArmed()) {
      event.preventDefault();
      if (!dwellDangerTimer && document.activeElement === dwellDangerButton) beginDwellDanger("keyboard");
      dwellDangerStatus.textContent = "Keep the button engaged for one second";
      return;
    }
    activateDwellDanger();
  });
}(window.ComponentReference.createLucideIcon, window.ComponentReference.spawnToast));
