(function (spawnToast) {
  // Copyable metadata
  var metadataCopyButtons = document.querySelectorAll(".meta-tag-copy[data-copy-value]");
  var metadataCopyResetTimers = new Map();

  function fallbackCopyText(value) {
    var previousFocus = document.activeElement;
    var helper = document.createElement("textarea");
    helper.value = value;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    helper.style.pointerEvents = "none";
    document.body.appendChild(helper);
    helper.select();
    helper.setSelectionRange(0, value.length);
    var didCopy = false;
    try {
      didCopy = document.execCommand("copy");
    } finally {
      helper.remove();
      if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
    }
    return didCopy ? Promise.resolve() : Promise.reject(new Error("Copy command failed"));
  }

  function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value).catch(function () {
        return fallbackCopyText(value);
      });
    }
    return fallbackCopyText(value);
  }

  function resetMetadataCopyButton(button) {
    button.classList.remove("is-copied");
    var timer = metadataCopyResetTimers.get(button);
    if (timer) window.clearTimeout(timer);
    metadataCopyResetTimers.delete(button);
  }

  metadataCopyButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.dataset.copyValue;
      copyText(value).then(function () {
        resetMetadataCopyButton(button);
        button.classList.add("is-copied");
        spawnToast("Copied to clipboard", false);
        metadataCopyResetTimers.set(button, window.setTimeout(function () {
          resetMetadataCopyButton(button);
        }, 1600));
      }).catch(function () {
        resetMetadataCopyButton(button);
        spawnToast("Could not copy to clipboard", false, "danger");
      });
    });
  });

}(window.ComponentReference.spawnToast));
