(function () {
  function createLucideIcon(name, className) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    svg.setAttribute("class", className || "icon");
    svg.setAttribute("aria-hidden", "true");
    use.setAttribute("href", "#lucide-" + name);
    svg.appendChild(use);
    return svg;
  }

  // 1. Theme toggle
  var html = document.documentElement;
  var themeToggle = document.getElementById("themeToggleBtn");
  var themeIconUse = document.getElementById("themeIconUse");

  function updateTheme(theme) {
    html.dataset.theme = theme;
    if (theme === "light") {
      themeIconUse.setAttribute("href", "#lucide-moon");
      themeToggle.setAttribute("aria-label", "Switch to dark theme");
      themeToggle.setAttribute("title", "Switch to dark theme");
    } else {
      themeIconUse.setAttribute("href", "#lucide-sun");
      themeToggle.setAttribute("aria-label", "Switch to light theme");
      themeToggle.setAttribute("title", "Switch to light theme");
    }
  }

  themeToggle.addEventListener("click", function () {
    var next = html.dataset.theme === "dark" ? "light" : "dark";
    updateTheme(next);
  });

  // 2. Spotlight search and action launcher
  var spotlightTrigger = document.getElementById("spotlightTrigger");
  var spotlightDialog = document.getElementById("spotlightDialog");
  var spotlightInput = document.getElementById("spotlightInput");
  var spotlightInputLabel = document.getElementById("spotlightInputLabel");
  var spotlightResults = document.getElementById("spotlightResults");
  var spotlightOptions = Array.from(spotlightResults.querySelectorAll(".spotlight-option"));
  var componentSections = Array.from(document.querySelectorAll(".component-section"));
  var spotlightEmpty = document.getElementById("spotlightEmpty");
  var spotlightStatus = document.getElementById("spotlightStatus");
  var shortcutDialog = document.getElementById("shortcutDialog");
  var closeShortcutsBtn = document.getElementById("btnCloseShortcuts");
  var spotlightMode = "commands";
  var restoreSpotlightFocus = true;
  var shortcutDialogReturnFocus = spotlightTrigger;
  var shortcutPlatform = navigator.userAgentData && navigator.userAgentData.platform
    ? navigator.userAgentData.platform
    : navigator.platform || "";
  var isAppleShortcutPlatform = /mac|iphone|ipad|ipod/i.test(shortcutPlatform);
  var commandShortcutModifier = isAppleShortcutPlatform ? "⌘" : "Ctrl";
  var globalShortcutModifier = isAppleShortcutPlatform ? "⌥" : "Alt";

  function appendShortcutKey(container, label) {
    var key = document.createElement("kbd");
    key.textContent = label;
    container.appendChild(key);
  }

  function renderShortcutKeys(container, key, shifted, modifier) {
    container.replaceChildren();
    appendShortcutKey(container, modifier);
    if (shifted) appendShortcutKey(container, isAppleShortcutPlatform ? "⇧" : "Shift");
    appendShortcutKey(container, key);
  }

  function appendControlSpaceAlternative(container) {
    var separator = document.createElement("span");
    separator.className = "shortcut-alternative";
    separator.setAttribute("aria-hidden", "true");
    separator.textContent = "/";
    container.appendChild(separator);
    appendShortcutKey(container, "Ctrl");
    appendShortcutKey(container, "Space");
  }

  spotlightOptions.forEach(function (option) {
    var shifted = option.dataset.shortcutShift === "true";
    renderShortcutKeys(option.querySelector(".spotlight-shortcut"), option.dataset.shortcutKey, shifted, globalShortcutModifier);
    var ariaShortcut = (shifted ? "Alt+Shift+" : "Alt+") + option.dataset.shortcutKey;
    option.setAttribute("aria-keyshortcuts", ariaShortcut);
  });

  document.querySelectorAll(".shortcut-keys[data-shortcut-key]").forEach(function (container) {
    var modifier = container.dataset.shortcutModifier === "command"
      ? commandShortcutModifier
      : globalShortcutModifier;
    renderShortcutKeys(container, container.dataset.shortcutKey, container.dataset.shortcutShift === "true", modifier);
    if (container.dataset.controlSpaceAlternative === "true") appendControlSpaceAlternative(container);
  });

  function visibleSpotlightOptions() {
    return spotlightOptions.filter(function (option) { return !option.hidden; });
  }

  function setActiveSpotlightOption(option) {
    spotlightOptions.forEach(function (candidate) {
      var active = candidate === option;
      candidate.classList.toggle("active", active);
      candidate.setAttribute("aria-selected", active ? "true" : "false");
    });

    if (option) {
      spotlightInput.setAttribute("aria-activedescendant", option.id);
      option.scrollIntoView({ block: "nearest" });
    } else {
      spotlightInput.removeAttribute("aria-activedescendant");
    }
  }

  function filterSpotlight() {
    var query = spotlightInput.value.trim().toLocaleLowerCase();
    var matches = [];

    spotlightOptions.forEach(function (option) {
      var label = option.querySelector(".spotlight-option-label");
      var searchableText = ((label ? label.textContent : "") + " " + (option.dataset.search || "")).toLocaleLowerCase();
      var belongsToMode = option.dataset.spotlightMode === spotlightMode;
      option.hidden = !belongsToMode || (query !== "" && !searchableText.includes(query));
      if (!option.hidden) matches.push(option);
    });

    spotlightEmpty.hidden = matches.length !== 0;
    spotlightEmpty.textContent = spotlightMode === "jump" ? "No matching components" : "No matching commands";
    spotlightStatus.textContent = matches.length === 1 ? "1 result available" : matches.length + " results available";
    setActiveSpotlightOption(matches[0] || null);
  }

  function setSpotlightMode(mode) {
    spotlightMode = mode === "jump" ? "jump" : "commands";
    spotlightInput.value = "";
    spotlightInput.placeholder = spotlightMode === "jump" ? "Jump to a component" : "Search commands";
    spotlightInputLabel.textContent = spotlightInput.placeholder;
    spotlightDialog.setAttribute("aria-label", spotlightMode === "jump" ? "Jump to a component" : "Search and commands");
    spotlightResults.setAttribute("aria-label", spotlightMode === "jump" ? "Components" : "Commands");
    filterSpotlight();
  }

  function openSpotlight(mode) {
    if (shortcutDialog.open) shortcutDialog.close();
    restoreSpotlightFocus = true;
    if (!spotlightDialog.open) spotlightDialog.showModal();
    spotlightTrigger.setAttribute("aria-expanded", "true");
    spotlightInput.setAttribute("aria-expanded", "true");
    setSpotlightMode(mode);
    window.requestAnimationFrame(function () {
      spotlightInput.focus();
    });
  }

  function closeSpotlight(returnFocus) {
    restoreSpotlightFocus = returnFocus !== false;
    if (spotlightDialog.open) spotlightDialog.close();
  }

  function openShortcutDialog() {
    shortcutDialogReturnFocus = spotlightDialog.open
      ? spotlightTrigger
      : document.activeElement || spotlightTrigger;
    closeSpotlight(false);
    if (!shortcutDialog.open) shortcutDialog.showModal();
    window.requestAnimationFrame(function () {
      closeShortcutsBtn.focus();
    });
  }

  function scrollToComponent(target, behavior) {
    if (!target) return;
    target.scrollIntoView({ behavior: behavior || "auto", block: "start" });
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", "#" + target.id);
    }
  }

  function currentComponentIndex() {
    var viewportAnchor = window.scrollY + Math.min(120, window.innerHeight * 0.25);
    var currentIndex = 0;
    componentSections.forEach(function (section, index) {
      if (section.offsetTop <= viewportAnchor) currentIndex = index;
    });
    return currentIndex;
  }

  function moveBetweenComponents(direction) {
    if (!componentSections.length) return;
    var currentIndex = currentComponentIndex();
    var nextIndex = Math.max(0, Math.min(componentSections.length - 1, currentIndex + direction));
    if (nextIndex === currentIndex) return;
    scrollToComponent(componentSections[nextIndex], "instant");
  }

  function buildMarkdownExport() {
    var lines = ["# Component Reference", ""];
    document.querySelectorAll(".component-section").forEach(function (section) {
      var heading = section.querySelector("h2");
      if (!heading) return;

      var title = heading.textContent.trim();
      var bodyLines = section.innerText.split("\n").map(function (line) {
        return line.trim();
      }).filter(Boolean);
      if (bodyLines[0] === title) bodyLines.shift();

      lines.push("## " + title, "");
      if (bodyLines.length) lines.push(bodyLines.join("\n\n"), "");
    });
    return lines.join("\n").trim() + "\n";
  }

  function downloadText(filename, contents, mimeType) {
    var url = URL.createObjectURL(new Blob([contents], { type: mimeType }));
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  function exportMarkdown() {
    downloadText("component-reference.md", buildMarkdownExport(), "text/markdown;charset=utf-8");
    spawnToast("Markdown export downloaded", false);
  }

  function runSpotlightAction(action) {
    if (action === "jump-to") {
      if (spotlightDialog.open) {
        setSpotlightMode("jump");
        spotlightInput.focus();
      } else {
        openSpotlight("jump");
      }
    } else if (action === "shortcuts") {
      openShortcutDialog();
    } else if (action === "export-markdown") {
      closeSpotlight(true);
      exportMarkdown();
    } else if (action === "export-pdf") {
      closeSpotlight(true);
      window.setTimeout(function () { window.print(); }, 0);
    } else if (action === "toggle-theme") {
      closeSpotlight(true);
      updateTheme(html.dataset.theme === "dark" ? "light" : "dark");
    }
  }

  function activateSpotlightOption(option) {
    if (!option) return;

    if (option.dataset.spotlightAction) {
      runSpotlightAction(option.dataset.spotlightAction);
      return;
    }

    var selector = option.dataset.spotlightTarget;
    var target = selector ? document.querySelector(selector) : null;
    if (!target) return;

    closeSpotlight(true);
    scrollToComponent(target, "smooth");
  }

  spotlightTrigger.addEventListener("click", function () { openSpotlight("commands"); });
  spotlightInput.addEventListener("input", filterSpotlight);
  spotlightInput.addEventListener("keydown", function (event) {
    var options = visibleSpotlightOptions();
    var active = spotlightResults.querySelector(".spotlight-option.active");
    var activeIndex = options.indexOf(active);

    if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      setActiveSpotlightOption(options[(activeIndex + 1 + options.length) % options.length]);
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      setActiveSpotlightOption(options[(activeIndex - 1 + options.length) % options.length]);
    } else if (event.key === "Enter" && active) {
      event.preventDefault();
      activateSpotlightOption(active);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeSpotlight(true);
    }
  });

  spotlightOptions.forEach(function (option) {
    option.addEventListener("mousedown", function (event) { event.preventDefault(); });
    option.addEventListener("mousemove", function () { setActiveSpotlightOption(option); });
    option.addEventListener("click", function () { activateSpotlightOption(option); });
  });

  function isEditableShortcutTarget(target) {
    return target instanceof Element && !!target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])");
  }

  document.addEventListener("keydown", function (event) {
    var commandModifierPressed = event.ctrlKey || event.metaKey;
    var opensWithCommandK = commandModifierPressed
      && !event.altKey
      && !event.shiftKey
      && event.key.toLocaleLowerCase() === "k";
    var opensWithControlSpace = event.ctrlKey
      && !event.metaKey
      && !event.altKey
      && !event.shiftKey
      && event.code === "Space";
    if (opensWithCommandK || opensWithControlSpace) {
      event.preventDefault();
      openSpotlight("commands");
      return;
    }

    if (event.isComposing || !event.altKey || event.ctrlKey || event.metaKey) return;
    if (document.querySelector("dialog[open]:not(#spotlightDialog)")) return;
    if (isEditableShortcutTarget(event.target) && event.target !== spotlightInput) return;

    if (!event.shiftKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
      event.preventDefault();
      moveBetweenComponents(event.key === "ArrowDown" ? 1 : -1);
      return;
    }

    var shortcutOption = spotlightOptions.find(function (option) {
      return event.code === option.dataset.shortcutCode
        && event.shiftKey === (option.dataset.shortcutShift === "true");
    });

    if (shortcutOption) {
      event.preventDefault();
      activateSpotlightOption(shortcutOption);
    }
  });

  spotlightDialog.addEventListener("click", function (event) {
    if (event.target === spotlightDialog) closeSpotlight(true);
  });

  spotlightDialog.addEventListener("close", function () {
    if (spotlightDialog.open) return;
    spotlightTrigger.setAttribute("aria-expanded", "false");
    spotlightInput.setAttribute("aria-expanded", "false");
    setSpotlightMode("commands");
    spotlightEmpty.hidden = true;
    setActiveSpotlightOption(null);
    if (restoreSpotlightFocus) spotlightTrigger.focus({ preventScroll: true });
    restoreSpotlightFocus = true;
  });

  closeShortcutsBtn.addEventListener("click", function () { shortcutDialog.close(); });
  shortcutDialog.addEventListener("click", function (event) {
    if (event.target === shortcutDialog) shortcutDialog.close();
  });
  shortcutDialog.addEventListener("close", function () {
    if (shortcutDialogReturnFocus && document.contains(shortcutDialogReturnFocus)) {
      shortcutDialogReturnFocus.focus({ preventScroll: true });
    }
    shortcutDialogReturnFocus = spotlightTrigger;
  });

  // 3. Loading button demo
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

  // 4. Dwell-to-arm destructive button demo
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

  // 5. Searchable accessible comboboxes
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

  // 6. Clear search button
  var searchInput = document.getElementById("inputSearch");
  var clearBtn = document.getElementById("btnClearSearch");
  clearBtn.addEventListener("click", function () {
    searchInput.value = "";
    searchInput.focus();
  });

  // 7. Submit-triggered email validation
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

  // 8. Indeterminate checkbox demo
  var indeterminateBox = document.getElementById("indeterminateBox");
  if (indeterminateBox) indeterminateBox.indeterminate = true;

  // 9. Copyable metadata
  var metadataCopyButtons = document.querySelectorAll(".meta-tag-copy[data-copy-value]");
  var metadataCopyResetTimers = new Map();
  var commitHashWrapper = document.getElementById("commitHashWrapper");
  var copyCommitButton = document.getElementById("copyCommitButton");
  var commitHashTooltipTimer = 0;
  var commitHashPointerInside = false;
  var commitHashFocused = false;
  var commitHashTooltipSuppressed = false;

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

  function hideCommitHashTooltip() {
    if (commitHashTooltipTimer) window.clearTimeout(commitHashTooltipTimer);
    commitHashTooltipTimer = 0;
    commitHashWrapper.classList.remove("is-tooltip-visible");
    copyCommitButton.removeAttribute("aria-describedby");
  }

  function updateCommitHashTooltip() {
    hideCommitHashTooltip();
    if (commitHashTooltipSuppressed || (!commitHashPointerInside && !commitHashFocused)) return;
    commitHashTooltipTimer = window.setTimeout(function () {
      commitHashTooltipTimer = 0;
      commitHashWrapper.classList.add("is-tooltip-visible");
      copyCommitButton.setAttribute("aria-describedby", "commitHashTooltip");
    }, 150);
  }

  commitHashWrapper.addEventListener("pointerenter", function () {
    commitHashPointerInside = true;
    commitHashTooltipSuppressed = false;
    updateCommitHashTooltip();
  });
  commitHashWrapper.addEventListener("pointerleave", function () {
    commitHashPointerInside = false;
    if (!commitHashFocused) commitHashTooltipSuppressed = false;
    updateCommitHashTooltip();
  });
  copyCommitButton.addEventListener("focus", function () {
    commitHashFocused = true;
    commitHashTooltipSuppressed = false;
    updateCommitHashTooltip();
  });
  copyCommitButton.addEventListener("blur", function () {
    commitHashFocused = false;
    if (!commitHashPointerInside) commitHashTooltipSuppressed = false;
    updateCommitHashTooltip();
  });
  copyCommitButton.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    commitHashTooltipSuppressed = true;
    hideCommitHashTooltip();
  });

  // 10. Table sorting
  var table = document.getElementById("sampleTable");
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
          var textA = cellA.textContent.trim();
          var textB = cellB.textContent.trim();
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

  // 11. Modal dialog
  var dialog = document.getElementById("demoDialog");
  var openModalBtn = document.getElementById("btnOpenModal");
  var closeModalBtn = document.getElementById("btnCloseModal");
  var cancelModalBtn = document.getElementById("btnCancelModal");
  var confirmModalBtn = document.getElementById("btnConfirmModal");

  if (openModalBtn && dialog) {
    openModalBtn.addEventListener("click", function () {
      dialog.showModal();
    });
    closeModalBtn.addEventListener("click", function () { dialog.close(); });
    cancelModalBtn.addEventListener("click", function () { dialog.close(); });
    confirmModalBtn.addEventListener("click", function () {
      dialog.close();
      spawnToast("Traffic successfully redirected to stable cluster.");
    });
  }

  // 12. Tabs
  var tabBtns = document.querySelectorAll(".tab-btn");
  var tabPanels = document.querySelectorAll(".tab-panel");
  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabBtns.forEach(function (b) {
        b.setAttribute("aria-selected", "false");
        b.setAttribute("tabindex", "-1");
      });
      tabPanels.forEach(function (p) { p.classList.remove("active"); });

      btn.setAttribute("aria-selected", "true");
      btn.setAttribute("tabindex", "0");
      var targetId = btn.getAttribute("aria-controls");
      document.getElementById(targetId).classList.add("active");
    });
  });

  // 13. Toast notification
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

  // 14. Sidebar scroll spy
  var sections = document.querySelectorAll(".component-section");
  var tocLinks = document.querySelectorAll(".toc-link");

  window.addEventListener("scroll", function () {
    var scrollPos = window.scrollY + 120;
    sections.forEach(function (sec) {
      var top = sec.offsetTop;
      var height = sec.offsetHeight;
      if (scrollPos >= top && scrollPos < top + height) {
        tocLinks.forEach(function (l) {
          l.classList.toggle("active", l.getAttribute("href") === "#" + sec.id);
        });
      }
    });
  });

}());
