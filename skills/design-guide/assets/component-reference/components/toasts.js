(function (reference) {
  var createLucideIcon = reference.createLucideIcon;

  // Toast notification
  var spawnToastBtn = document.getElementById("btnSpawnToast");
  var toastContainer = document.getElementById("toastContainer");
  var toastHistoryDialog = document.getElementById("toastHistoryDialog");
  var toastHistoryList = document.getElementById("toastHistoryList");
  var closeToastHistoryButton = document.getElementById("btnCloseToastHistory");
  var toastHistory = [];
  var toastCountdowns = new WeakMap();
  var toastHistoryReturnFocus = null;

  function formatToastTimestamp(date) {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit"
    }).format(date);
  }

  function renderToastHistory() {
    toastHistoryList.replaceChildren();
    toastHistory.slice().reverse().forEach(function (record) {
      var item = document.createElement("li");
      item.className = "toast-history-item";
      item.dataset.tone = record.tone;
      item.appendChild(createLucideIcon(record.tone === "danger" ? "octagon-x" : "check", "icon toast-history-icon"));

      var copy = document.createElement("div");
      copy.className = "toast-history-copy";

      var message = document.createElement("span");
      message.className = "toast-history-message";
      message.textContent = record.message;
      copy.appendChild(message);

      var timestamp = document.createElement("time");
      timestamp.className = "toast-history-time";
      timestamp.dateTime = record.createdAt.toISOString();
      timestamp.textContent = formatToastTimestamp(record.createdAt);
      copy.appendChild(timestamp);

      item.appendChild(copy);
      toastHistoryList.appendChild(item);
    });
  }

  function openToastHistory(trigger) {
    toastHistoryReturnFocus = trigger || document.activeElement;
    renderToastHistory();
    if (!toastHistoryDialog.open) toastHistoryDialog.showModal();
    closeToastHistoryButton.focus();
  }

  function updateToastTrayState() {
    var hasToasts = Boolean(toastContainer.querySelector(".toast"));
    var hasLiveToast = Boolean(toastContainer.querySelector(".toast:not(.is-archived)"));
    toastContainer.classList.toggle("has-history", hasToasts);
    toastContainer.classList.toggle("is-resting", hasToasts && !hasLiveToast);
    window.requestAnimationFrame(function () {
      toastContainer.scrollTop = toastContainer.scrollHeight;
    });
  }

  function archiveToast(toast) {
    var countdown = toastCountdowns.get(toast);
    if (!countdown || countdown.archived) return;
    if (countdown.timer) window.clearTimeout(countdown.timer);
    countdown.timer = 0;
    countdown.archived = true;

    var undoButton = toast.querySelector(".toast-undo");
    if (undoButton) undoButton.remove();
    toast.classList.remove("has-undo");
    toast.classList.add("is-archived");
    updateToastTrayState();
  }

  function resumeToastCountdown(toast) {
    var countdown = toastCountdowns.get(toast);
    if (!countdown || countdown.archived || countdown.timer) return;
    if (countdown.remaining <= 0) {
      archiveToast(toast);
      return;
    }
    countdown.startedAt = performance.now();
    countdown.timer = window.setTimeout(function () {
      countdown.timer = 0;
      countdown.remaining = 0;
      archiveToast(toast);
    }, countdown.remaining);
  }

  function pauseToastCountdown(toast) {
    var countdown = toastCountdowns.get(toast);
    if (!countdown || countdown.archived || !countdown.timer) return;
    window.clearTimeout(countdown.timer);
    countdown.timer = 0;
    countdown.remaining = Math.max(0, countdown.remaining - (performance.now() - countdown.startedAt));
  }

  function spawnToast(msg, undoable, tone) {
    var resolvedMessage = msg || "Preferences updated successfully.";
    var resolvedTone = tone === "danger" ? "danger" : "success";
    var createdAt = new Date();
    toastHistory.push({
      message: resolvedMessage,
      tone: resolvedTone,
      createdAt: createdAt
    });

    var toast = document.createElement("div");
    toast.className = "toast";
    toast.dataset.tone = resolvedTone;

    var toastMain = document.createElement("button");
    toastMain.className = "toast-main";
    toastMain.type = "button";
    toastMain.setAttribute("aria-label", resolvedMessage + " Open toast history");
    toastMain.title = "Open toast history";

    var iconName = resolvedTone === "danger" ? "octagon-x" : "check";
    var iconClass = resolvedTone === "danger" ? "icon toast-icon toast-icon-danger" : "icon toast-icon";
    toastMain.appendChild(createLucideIcon(iconName, iconClass));
    var message = document.createElement("span");
    message.className = "toast-msg";
    message.textContent = resolvedMessage;
    toastMain.appendChild(message);
    toastMain.addEventListener("click", function () { openToastHistory(toastMain); });
    toast.appendChild(toastMain);

    if (undoable !== false) {
      toast.classList.add("has-undo");
      var undoBtn = document.createElement("button");
      undoBtn.className = "toast-undo";
      undoBtn.type = "button";
      undoBtn.textContent = "Undo";
      undoBtn.addEventListener("click", function () { archiveToast(toast); });
      toast.appendChild(undoBtn);
    }

    toastContainer.appendChild(toast);
    toastCountdowns.set(toast, {
      archived: false,
      remaining: 4500,
      startedAt: 0,
      timer: 0
    });
    toast.addEventListener("pointerenter", function () { pauseToastCountdown(toast); });
    toast.addEventListener("pointerleave", function () {
      if (!toast.contains(document.activeElement)) resumeToastCountdown(toast);
    });
    toast.addEventListener("focusin", function () { pauseToastCountdown(toast); });
    toast.addEventListener("focusout", function () {
      window.requestAnimationFrame(function () {
        if (!toast.contains(document.activeElement) && !toast.matches(":hover")) resumeToastCountdown(toast);
      });
    });

    updateToastTrayState();
    resumeToastCountdown(toast);
  }

  if (spawnToastBtn) {
    spawnToastBtn.addEventListener("click", function () {
      spawnToast("Cluster configuration snapshot saved.");
    });
  }

  closeToastHistoryButton.addEventListener("click", function () {
    toastHistoryDialog.close();
  });

  toastHistoryDialog.addEventListener("close", function () {
    if (toastHistoryReturnFocus && document.contains(toastHistoryReturnFocus)) {
      toastHistoryReturnFocus.focus({ preventScroll: true });
    }
    toastHistoryReturnFocus = null;
  });

  reference.spawnToast = spawnToast;
}(window.ComponentReference));
