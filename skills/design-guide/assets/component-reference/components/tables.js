(function (makeTableSortable) {
  var table = document.getElementById("sampleTable");
  var originalTableRows = Array.from(table.querySelectorAll("tbody tr"));
  makeTableSortable(table);

  // Service row details
  var serviceDetails = {
    "auth-gateway": { region: "us-east-1", team: "Identity", version: "v2.14.0", replicas: "6" },
    "checkout-api": { region: "us-east-1", team: "Commerce", version: "v3.8.1", replicas: "8" },
    "order-queue": { region: "us-east-1", team: "Fulfillment", version: "v1.12.3", replicas: "4" },
    "prediction-engine": { region: "us-west-2", team: "Machine Learning", version: "v0.9.0", replicas: "2" },
    "session-cache": { region: "us-east-1", team: "Identity", version: "v1.6.2", replicas: "3" },
    "inventory-api": { region: "eu-central-1", team: "Commerce", version: "v2.5.0", replicas: "4" }
  };
  var serviceDetailsDialog = document.getElementById("serviceDetailsDialog");
  var closeServiceDetailsButton = document.getElementById("btnCloseServiceDetails");

  function selectServiceRow(row) {
    originalTableRows.forEach(function (candidate) {
      var selected = candidate === row;
      candidate.classList.toggle("is-selected", selected);
      candidate.querySelector(".table-row-trigger").tabIndex = selected ? 0 : -1;
    });
  }

  function clearServiceRowSelection() {
    originalTableRows.forEach(function (row) {
      row.classList.remove("is-selected");
    });
  }

  table.addEventListener("focusout", function (event) {
    if (!table.contains(event.relatedTarget)) clearServiceRowSelection();
  });
  document.addEventListener("pointerdown", function (event) {
    if (table.contains(event.target)) return;
    clearServiceRowSelection();
    // Blank page space may not take focus, so release the row's focus too.
    var focused = document.activeElement;
    if (table.contains(focused)) focused.blur();
  });

  function openServiceDetails(row) {
    var details = serviceDetails[row.dataset.serviceId];
    selectServiceRow(row);
    var trigger = row.querySelector(".table-row-trigger");
    document.getElementById("serviceDetailsTitle").textContent = row.cells[0].textContent.trim();
    serviceDetailsDialog.querySelector('[data-service-detail="status"]').replaceChildren(row.cells[1].querySelector(".badge").cloneNode(true));
    ["rpm", "latency", "errorRate"].forEach(function (key, index) {
      serviceDetailsDialog.querySelector('[data-service-detail="' + key + '"]').textContent = row.cells[index + 2].textContent.trim();
    });
    Object.keys(details).forEach(function (key) {
      serviceDetailsDialog.querySelector('[data-service-detail="' + key + '"]').textContent = details[key];
    });
    // Let the native dialog restore the row's focus as soon as it closes.
    trigger.focus({ preventScroll: true });
    serviceDetailsDialog.showModal();
    closeServiceDetailsButton.focus();
  }

  originalTableRows.forEach(function (row, index) {
    var trigger = row.querySelector(".table-row-trigger");
    trigger.tabIndex = index === 0 ? 0 : -1;
    trigger.addEventListener("focus", function () {
      selectServiceRow(row);
    });
    trigger.addEventListener("keydown", function (event) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      var rows = Array.from(table.querySelectorAll("tbody tr"));
      var offset = event.key === "ArrowDown" ? 1 : -1;
      var next = rows[Math.max(0, Math.min(rows.length - 1, rows.indexOf(row) + offset))];
      next.querySelector(".table-row-trigger").focus({ preventScroll: true });
      next.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
    });
    trigger.addEventListener("click", function () {
      openServiceDetails(row);
    });
    row.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.target.closest("button, a, input, select, textarea, summary, [contenteditable]:not([contenteditable='false'])")) return;
      var selection = window.getSelection();
      if (selection && !selection.isCollapsed) return;
      openServiceDetails(row);
    });
  });
  closeServiceDetailsButton.addEventListener("click", function () {
    serviceDetailsDialog.close();
  });
  serviceDetailsDialog.addEventListener("keydown", function (event) {
    if (event.key === "Tab") {
      event.preventDefault();
      closeServiceDetailsButton.focus();
    }
  });
  serviceDetailsDialog.addEventListener("click", function (event) {
    if (event.target !== serviceDetailsDialog) return;
    var bounds = serviceDetailsDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
      serviceDetailsDialog.close();
    }
  });
}(window.ComponentReference.makeTableSortable));
