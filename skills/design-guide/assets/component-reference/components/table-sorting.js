(function (reference) {
  reference.makeTableSortable = function (table) {
    var originalTableRows = Array.from(table.querySelectorAll("tbody tr"));
    var tableSorts = [];
    var tableStatusOrder = ["good", "caution", "danger"];
    var sortColumns = Array.from(table.querySelectorAll(".sort-th-btn")).map(function (btn) {
      var header = btn.closest("th");
      var description = document.createElement("span");
      description.id = table.id + "-sort-description-" + header.cellIndex;
      description.className = "sr-only";
      header.appendChild(description);
      btn.setAttribute("aria-describedby", description.id);
      return {
        button: btn,
        header: header,
        index: header.cellIndex,
        type: btn.dataset.sort,
        direction: null,
        description: description
      };
    });
    table.style.setProperty("--sort-priority-width", String(sortColumns.length).length + "ch");

    function applyTableSort() {
      var rows = originalTableRows.slice();
      rows.sort(function (a, b) {
        for (var i = 0; i < tableSorts.length; i++) {
          var column = tableSorts[i];
          var cellA = a.cells[column.index];
          var cellB = b.cells[column.index];
          var comparison;
          if (column.type === "severity") {
            var rankA = tableStatusOrder.indexOf(cellA.dataset.sortSeverity);
            var rankB = tableStatusOrder.indexOf(cellB.dataset.sortSeverity);
            // Statuses without a health severity follow ranked states in both directions.
            if (rankA < 0 && rankB >= 0) return 1;
            if (rankA >= 0 && rankB < 0) return -1;
            comparison = rankA - rankB;
          } else {
            var textA = cellA.dataset.sortValue !== undefined ? cellA.dataset.sortValue : cellA.textContent.trim();
            var textB = cellB.dataset.sortValue !== undefined ? cellB.dataset.sortValue : cellB.textContent.trim();
            // Missing measurements follow real values in either direction.
            if (column.type === "number") {
              var missingA = !Number.isFinite(parseFloat(textA.replace(/,/g, "")));
              var missingB = !Number.isFinite(parseFloat(textB.replace(/,/g, "")));
              if (missingA !== missingB) return missingA ? 1 : -1;
              if (missingA) continue;
            }
            comparison = column.type === "number"
              ? parseFloat(textA.replace(/,/g, "")) - parseFloat(textB.replace(/,/g, ""))
              : textA.localeCompare(textB, undefined, { sensitivity: "base" });
          }
          if (comparison) return column.direction === "ascending" ? comparison : -comparison;
        }
        return 0;
      });
      var tbody = table.querySelector("tbody");
      rows.forEach(function (row) { tbody.appendChild(row); });

      sortColumns.forEach(function (column) {
        var priority = tableSorts.indexOf(column);
        column.header.dataset.sortDirection = column.direction || "none";
        if (priority === 0) column.header.setAttribute("aria-sort", column.direction);
        else column.header.removeAttribute("aria-sort");
        column.button.querySelector(".sort-priority").textContent = priority >= 0 ? String(priority + 1) : "";
        column.button.querySelector("use").setAttribute("href", column.direction === "descending" ? "#lucide-arrow-down" : "#lucide-arrow-up");
        column.description.textContent = priority >= 0
          ? "Sort priority " + (priority + 1) + " of " + tableSorts.length + ", " + column.direction
          : "Unsorted";
      });
    }

    sortColumns.forEach(function (column) {
      column.button.addEventListener("click", function () {
        if (!column.direction) {
          column.direction = "ascending";
          tableSorts.push(column);
        } else if (column.direction === "ascending") {
          column.direction = "descending";
        } else {
          column.direction = null;
          tableSorts.splice(tableSorts.indexOf(column), 1);
        }
        applyTableSort();
      });
    });
    applyTableSort();

    return {
      refresh: function () {
        originalTableRows = Array.from(table.querySelectorAll("tbody tr"));
        applyTableSort();
      },
      reset: function () {
        tableSorts = [];
        sortColumns.forEach(function (column) { column.direction = null; });
        applyTableSort();
      }
    };
  };
}(window.ComponentReference));
