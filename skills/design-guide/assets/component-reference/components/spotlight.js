(function (toggleTheme, exportMarkdown) {
  // Spotlight search and action launcher
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
      toggleTheme();
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
}(window.ComponentReference.toggleTheme, window.ComponentReference.exportMarkdown));
