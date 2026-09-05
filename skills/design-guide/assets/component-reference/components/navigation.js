(function () {
  document.querySelectorAll("[data-swipe-tabs]").forEach(function (group) {
    var buttons = Array.from(group.querySelectorAll(".tab-btn"));
    var panels = buttons.map(function (button) { return document.getElementById(button.getAttribute("aria-controls")); });
    var viewport = group.querySelector(".tab-viewport");
    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    var active = buttons.findIndex(function (button) { return button.getAttribute("aria-selected") === "true"; });
    var gesture = null;
    var suppressClick = false;
    var preview = -1;
    var offset = 0;
    var settlement = null;
    var settleTimer = 0;

    function resetMotion() {
      window.clearTimeout(settleTimer);
      settlement = null;
      preview = -1;
      offset = 0;
      viewport.classList.remove("is-sliding", "is-settling");
      viewport.style.height = "";
      panels.forEach(function (panel) {
        panel.style.transform = "";
        panel.classList.remove("is-swipe-preview");
        panel.inert = false;
        panel.removeAttribute("aria-hidden");
        panel.hidden = !panel.classList.contains("active");
      });
    }

    function select(index, focus) {
      gesture = null;
      resetMotion();
      active = index;
      buttons.forEach(function (button, position) {
        var selected = position === index;
        button.setAttribute("aria-selected", String(selected));
        button.tabIndex = selected ? 0 : -1;
        panels[position].classList.toggle("active", selected);
        panels[position].hidden = !selected;
      });
      if (focus) buttons[index].focus({ preventScroll: true });
    }

    function translate(panel, distance) {
      panel.style.transform = "translate3d(" + distance + "px, 0, 0)";
    }

    function drag(distance, travel) {
      if (!viewport.classList.contains("is-sliding")) {
        viewport.style.height = panels[active].offsetHeight + "px";
        viewport.classList.add("is-sliding");
      }
      var neighbor = active + (distance < 0 ? 1 : -1);
      if (neighbor < 0 || neighbor >= panels.length) neighbor = -1;
      if (preview !== neighbor) {
        if (preview !== -1) {
          panels[preview].hidden = true;
          panels[preview].classList.remove("is-swipe-preview");
          panels[preview].style.transform = "";
          panels[preview].inert = false;
          panels[preview].removeAttribute("aria-hidden");
        }
        preview = neighbor;
        if (preview !== -1) {
          // The adjacent panel is only a visual preview until selection commits.
          panels[preview].inert = true;
          panels[preview].setAttribute("aria-hidden", "true");
          panels[preview].hidden = false;
          panels[preview].classList.add("is-swipe-preview");
        }
      }
      offset = preview === -1
        ? Math.sign(distance) * Math.min(64, Math.abs(distance) * 0.25)
        : Math.max(-travel, Math.min(travel, distance));
      translate(panels[active], offset);
      if (preview !== -1) translate(panels[preview], offset + (preview - active) * travel);
    }

    function finishSettlement() {
      if (!settlement) return;
      var completed = settlement;
      select(completed.index, completed.focus);
    }

    function settle(index, focus, travel) {
      settlement = { index: index, focus: focus };
      var destination = (active - index) * travel;
      if (reducedMotion.matches || Math.abs(offset - destination) < 0.5) {
        finishSettlement();
        return;
      }
      // Flush the last finger position before transitioning to the resting position.
      viewport.getBoundingClientRect();
      viewport.classList.add("is-settling");
      viewport.style.height = panels[index].offsetHeight + "px";
      translate(panels[active], destination);
      if (preview !== -1) translate(panels[preview], destination + (preview - active) * travel);
      settleTimer = window.setTimeout(finishSettlement, 260);
    }

    function cancelGesture() {
      var canceled = gesture;
      gesture = null;
      if (canceled && canceled.horizontal) settle(active, false, canceled.travel);
    }

    viewport.addEventListener("transitionend", function (event) {
      if (event.target === panels[active] && event.propertyName === "transform") finishSettlement();
    });

    buttons.forEach(function (button, index) {
      button.addEventListener("click", function () { select(index, false); });
      button.addEventListener("keydown", function (event) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        var next;
        if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
        if (event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = buttons.length - 1;
        if (next !== undefined) { event.preventDefault(); select(next, true); }
      });
    });

    function isIndependentControl(target) {
      if (target.closest("a, input, textarea, select, [contenteditable]:not([contenteditable='false']), [data-swipe-ignore]")) return true;
      if (target.closest("button") && !target.closest(".tab-btn")) return true;
      for (var node = target; node && node !== group; node = node.parentElement) {
        if (node.scrollWidth > node.clientWidth && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
      }
      return false;
    }

    group.addEventListener("touchstart", function (event) {
      finishSettlement();
      cancelGesture();
      suppressClick = false;
      if (event.touches.length !== 1 || isIndependentControl(event.target) || !window.getSelection().isCollapsed) return;
      var touch = event.touches[0];
      gesture = { id: touch.identifier, x: touch.clientX, y: touch.clientY, travel: viewport.clientWidth + 16, horizontal: false };
    }, { passive: true });

    group.addEventListener("touchmove", function (event) {
      if (!gesture) return;
      if (event.touches.length !== 1 || !event.cancelable || !window.getSelection().isCollapsed) { cancelGesture(); return; }
      var touch = event.touches[0];
      var x = Math.abs(touch.clientX - gesture.x), y = Math.abs(touch.clientY - gesture.y);
      if (!gesture.horizontal) {
        if (Math.max(x, y) < 10) return;
        if (y >= x) { gesture = null; return; }
        if (x < y * 1.5) return;
        gesture.horizontal = true;
      }
      // Only a horizontal gesture belongs to the tabs; native scrolling and zoom stay available.
      event.preventDefault();
      suppressClick = true;
      drag(touch.clientX - gesture.x, gesture.travel);
    }, { passive: false });

    group.addEventListener("touchend", function (event) {
      var completed = gesture;
      gesture = null;
      if (!completed || !completed.horizontal) return;
      var touch = Array.from(event.changedTouches).find(function (item) { return item.identifier === completed.id; });
      var next = active;
      if (touch && !event.touches.length && window.getSelection().isCollapsed) {
        var x = touch.clientX - completed.x, y = touch.clientY - completed.y;
        drag(x, completed.travel);
        if (Math.abs(x) >= 48 && Math.abs(x) >= Math.abs(y) * 1.5) {
          next = Math.max(0, Math.min(buttons.length - 1, active + (x < 0 ? 1 : -1)));
        }
      }
      settle(next, next !== active && group.contains(document.activeElement), completed.travel);
    }, { passive: true });

    group.addEventListener("touchcancel", cancelGesture, { passive: true });
    window.addEventListener("touchstart", function (event) {
      if (event.touches.length > 1) cancelGesture();
    }, { passive: true });
    group.addEventListener("click", function (event) {
      if (suppressClick && event.detail) { event.preventDefault(); event.stopPropagation(); }
      suppressClick = false;
    }, true);
    group.addEventListener("pointerdown", function (event) {
      if (event.pointerType !== "touch") suppressClick = false;
    });
    window.addEventListener("resize", function () {
      if (settlement) finishSettlement();
      else select(active, false);
    });
    reducedMotion.addEventListener("change", finishSettlement);
    select(Math.max(0, active), false);
  });
}());
