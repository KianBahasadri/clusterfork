(function () {
  // Searchable accessible comboboxes
  var dropdowns = document.querySelectorAll("[data-custom-dropdown]");
  var dropdownStates = new WeakMap();

  function dropdownParts(dropdown) {
    return {
      input: dropdown.querySelector("[data-dropdown-input]"),
      menu: dropdown.querySelector(".dropdown-menu"),
      value: dropdown.querySelector("[data-dropdown-value]"),
      items: dropdown.querySelectorAll(".dropdown-item"),
      fixedItems: dropdown.querySelectorAll(".dropdown-item:not(.dropdown-custom-option)"),
      customItem: dropdown.querySelector(".dropdown-custom-option"),
      customLabel: dropdown.querySelector("[data-dropdown-custom-label]"),
      empty: dropdown.querySelector(".dropdown-empty"),
      status: dropdown.querySelector("[data-dropdown-status]")
    };
  }

  function normalizeDropdownText(value) {
    return value.trim().toLocaleLowerCase();
  }

  function dropdownItemLabel(item) {
    return item.getAttribute("data-label") || item.textContent.trim();
  }

  function fitDropdownWidth(dropdown) {
    var parts = dropdownParts(dropdown);
    var context = document.createElement("canvas").getContext("2d");
    if (!context) return;

    var inputStyles = window.getComputedStyle(parts.input);
    context.font = [inputStyles.fontStyle, inputStyles.fontWeight, inputStyles.fontSize, inputStyles.fontFamily].join(" ");
    var inputLabels = [parts.input.value, parts.input.placeholder];
    var widestInputLabel = inputLabels.reduce(function (widest, label) {
      return Math.max(widest, context.measureText(label || "").width);
    }, 0);

    var optionStyles = window.getComputedStyle(parts.fixedItems[0] || parts.input);
    context.font = [optionStyles.fontStyle, "600", optionStyles.fontSize, optionStyles.fontFamily].join(" ");
    var widestOptionLabel = Array.prototype.reduce.call(parts.fixedItems, function (widest, item) {
      return Math.max(widest, context.measureText(dropdownItemLabel(item)).width);
    }, 0);

    var fittedWidth = Math.ceil(Math.max(widestInputLabel + 52, widestOptionLabel + 28));
    fittedWidth = Math.min(232, Math.max(176, fittedWidth));
    dropdown.style.setProperty("--dropdown-width", fittedWidth + "px");
  }

  function visibleDropdownItems(parts) {
    return Array.prototype.filter.call(parts.items, function (item) {
      return !item.hidden;
    });
  }

  function setActiveDropdownItem(dropdown, item) {
    var parts = dropdownParts(dropdown);
    var state = dropdownStates.get(dropdown);
    parts.items.forEach(function (option) { option.classList.remove("active"); });
    state.activeItem = item || null;

    if (item) {
      item.classList.add("active");
      parts.input.setAttribute("aria-activedescendant", item.id);
      item.scrollIntoView({ block: "nearest" });
    } else {
      parts.input.removeAttribute("aria-activedescendant");
    }
  }

  function filterDropdown(dropdown, showAll) {
    var parts = dropdownParts(dropdown);
    var state = dropdownStates.get(dropdown);
    var query = parts.input.value.trim();
    var normalizedQuery = normalizeDropdownText(query);
    var filter = showAll ? "" : normalizedQuery;
    var exactItem = null;
    var matchCount = 0;

    parts.fixedItems.forEach(function (item) {
      var normalizedLabel = normalizeDropdownText(dropdownItemLabel(item));
      var matches = !filter || normalizedLabel.indexOf(filter) !== -1;
      item.hidden = !matches;
      if (matches) matchCount += 1;
      if (normalizedLabel === normalizedQuery) exactItem = item;
    });

    var customVisible = dropdown.dataset.allowCustom === "true" && !!query && !exactItem;
    if (parts.customItem) {
      parts.customItem.hidden = !customVisible;
      parts.customItem.setAttribute("data-label", query);
      parts.customItem.setAttribute("data-value", query);
      parts.customLabel.textContent = query;
      var customSelected = customVisible && state.committedCustom && state.committedLabel === query;
      parts.customItem.classList.toggle("selected", customSelected);
      parts.customItem.setAttribute("aria-selected", customSelected ? "true" : "false");
    }

    var availableCount = matchCount + (customVisible ? 1 : 0);
    parts.empty.hidden = availableCount !== 0;
    parts.status.textContent = availableCount === 0
      ? "No matching options."
      : availableCount + (availableCount === 1 ? " option available." : " options available.");

    var visible = visibleDropdownItems(parts);
    var active = state.activeItem;
    if (!active || active.hidden || visible.indexOf(active) === -1) {
      active = visible.filter(function (item) {
        return item.getAttribute("aria-selected") === "true";
      })[0] || visible[0] || null;
    }
    setActiveDropdownItem(dropdown, active);
  }

  function hideDropdown(dropdown) {
    var parts = dropdownParts(dropdown);
    parts.menu.classList.remove("open");
    parts.input.setAttribute("aria-expanded", "false");
    setActiveDropdownItem(dropdown, null);
  }

  function setCommittedDropdownValue(dropdown, label, value, selectedItem, isCustom) {
    var parts = dropdownParts(dropdown);
    var state = dropdownStates.get(dropdown);
    var changed = state.committedLabel !== label || state.committedValue !== value;

    state.committedLabel = label;
    state.committedValue = value;
    state.committedCustom = isCustom;
    parts.input.value = label;
    parts.value.value = value;

    parts.fixedItems.forEach(function (item) {
      var selected = item === selectedItem;
      item.classList.toggle("selected", selected);
      item.setAttribute("aria-selected", selected ? "true" : "false");
    });

    if (parts.customItem) {
      parts.customItem.classList.toggle("selected", isCustom);
      parts.customItem.setAttribute("aria-selected", isCustom ? "true" : "false");
    }

    if (changed) parts.value.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function findExactDropdownItem(parts, value) {
    var normalizedValue = normalizeDropdownText(value);
    return Array.prototype.filter.call(parts.fixedItems, function (item) {
      return normalizeDropdownText(dropdownItemLabel(item)) === normalizedValue;
    })[0] || null;
  }

  function settleDropdownInput(dropdown) {
    var parts = dropdownParts(dropdown);
    var state = dropdownStates.get(dropdown);
    var query = parts.input.value.trim();
    var exactItem = findExactDropdownItem(parts, query);

    if (exactItem) {
      setCommittedDropdownValue(dropdown, dropdownItemLabel(exactItem), exactItem.dataset.value, exactItem, false);
    } else if (dropdown.dataset.allowCustom === "true") {
      setCommittedDropdownValue(dropdown, query, query, null, !!query);
    } else {
      parts.input.value = state.committedLabel;
    }
  }

  function openDropdown(dropdown, showAll, focusLast) {
    dropdowns.forEach(function (other) {
      if (other !== dropdown && dropdownParts(other).menu.classList.contains("open")) {
        settleDropdownInput(other);
        hideDropdown(other);
      }
    });

    var parts = dropdownParts(dropdown);
    parts.menu.classList.add("open");
    parts.input.setAttribute("aria-expanded", "true");
    filterDropdown(dropdown, showAll);

    if (focusLast) {
      var visible = visibleDropdownItems(parts);
      setActiveDropdownItem(dropdown, visible[visible.length - 1] || null);
    }
  }

  function selectDropdownItem(dropdown, item) {
    var isCustom = item.classList.contains("dropdown-custom-option");
    setCommittedDropdownValue(
      dropdown,
      dropdownItemLabel(item),
      item.dataset.value,
      isCustom ? null : item,
      isCustom
    );
    hideDropdown(dropdown);
    dropdownParts(dropdown).input.focus();
  }

  dropdowns.forEach(function (dropdown) {
    var parts = dropdownParts(dropdown);
    fitDropdownWidth(dropdown);
    dropdownStates.set(dropdown, {
      activeItem: null,
      committedLabel: parts.input.value,
      committedValue: parts.value.value,
      committedCustom: false
    });

    parts.input.addEventListener("click", function () {
      if (!parts.menu.classList.contains("open")) {
        openDropdown(dropdown, true, false);
        parts.input.select();
      }
    });

    parts.input.addEventListener("input", function () {
      dropdownStates.get(dropdown).activeItem = null;
      openDropdown(dropdown, false, false);
    });

    parts.input.addEventListener("keydown", function (e) {
      var isOpen = parts.menu.classList.contains("open");

      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen) {
          openDropdown(dropdown, true, e.key === "ArrowUp");
          return;
        }

        var visible = visibleDropdownItems(parts);
        var active = dropdownStates.get(dropdown).activeItem;
        var index = visible.indexOf(active);
        var offset = e.key === "ArrowDown" ? 1 : -1;
        var nextIndex = index === -1
          ? (offset === 1 ? 0 : visible.length - 1)
          : (index + offset + visible.length) % visible.length;
        setActiveDropdownItem(dropdown, visible[nextIndex] || null);
      } else if (e.key === "Enter" && isOpen) {
        var activeItem = dropdownStates.get(dropdown).activeItem;
        if (activeItem) {
          e.preventDefault();
          selectDropdownItem(dropdown, activeItem);
        }
      } else if (e.key === "Escape" && (isOpen || parts.input.value !== dropdownStates.get(dropdown).committedLabel)) {
        e.preventDefault();
        parts.input.value = dropdownStates.get(dropdown).committedLabel;
        hideDropdown(dropdown);
      } else if (e.key === "Tab" && isOpen) {
        settleDropdownInput(dropdown);
        hideDropdown(dropdown);
      }
    });

    parts.items.forEach(function (item) {
      item.addEventListener("mousedown", function (e) { e.preventDefault(); });
      item.addEventListener("mousemove", function () { setActiveDropdownItem(dropdown, item); });
      item.addEventListener("click", function () { selectDropdownItem(dropdown, item); });
    });
  });

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () {
      dropdowns.forEach(fitDropdownWidth);
    });
  }

  document.addEventListener("click", function (e) {
    dropdowns.forEach(function (dropdown) {
      var parts = dropdownParts(dropdown);
      if (!dropdown.contains(e.target) && parts.menu.classList.contains("open")) {
        settleDropdownInput(dropdown);
        hideDropdown(dropdown);
      }
    });
  });

  window.addEventListener("blur", function () {
    dropdowns.forEach(function (dropdown) {
      if (dropdownParts(dropdown).menu.classList.contains("open")) {
        settleDropdownInput(dropdown);
        hideDropdown(dropdown);
      }
    });
  });
}());
