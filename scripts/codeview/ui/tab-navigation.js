/* Direct touch tracking for Codeview's dynamically loaded tab panels. */
"use strict";

window.CodeviewTabs = function (group, activate) {
  const viewport = group.querySelector(".tab-viewport");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let gesture = null;
  let settlement = null;
  let timer = 0;
  let suppressClick = false;

  function reset() {
    clearTimeout(timer);
    gesture = settlement = null;
    viewport.classList.remove("is-sliding", "is-settling");
    viewport.style.height = "";
    viewport.querySelectorAll(".tab-panel").forEach(panel => {
      panel.style.transform = "";
      panel.classList.remove("is-swipe-preview");
      panel.inert = false;
      panel.removeAttribute("aria-hidden");
      panel.hidden = !panel.classList.contains("active");
    });
  }

  function translate(panel, distance) {
    panel.style.transform = `translate3d(${distance}px, 0, 0)`;
  }

  function drag(g, distance) {
    if (!viewport.classList.contains("is-sliding")) {
      viewport.style.height = `${g.panels[g.active].offsetHeight}px`;
      viewport.classList.add("is-sliding");
    }
    let neighbor = g.active + (distance < 0 ? 1 : -1);
    if (neighbor < 0 || neighbor >= g.panels.length) neighbor = -1;
    if (g.preview !== neighbor) {
      if (g.preview >= 0) {
        const old = g.panels[g.preview];
        old.hidden = true;
        old.inert = false;
        old.removeAttribute("aria-hidden");
        old.classList.remove("is-swipe-preview");
        old.style.transform = "";
      }
      g.preview = neighbor;
      if (neighbor >= 0) {
        const panel = g.panels[neighbor];
        panel.inert = true;
        panel.setAttribute("aria-hidden", "true");
        panel.hidden = false;
        panel.classList.add("is-swipe-preview");
      }
    }
    g.offset = neighbor < 0
      ? Math.sign(distance) * Math.min(64, Math.abs(distance) * 0.25)
      : Math.max(-g.travel, Math.min(g.travel, distance));
    translate(g.panels[g.active], g.offset);
    if (neighbor >= 0) translate(g.panels[neighbor], g.offset + (neighbor - g.active) * g.travel);
  }

  function finish() {
    if (!settlement) return;
    const { g, index, focus } = settlement;
    reset();
    if (index !== g.active) activate(g.buttons[index].dataset.tab);
    if (focus) g.buttons[index].focus({ preventScroll: true });
  }

  function settle(g, index, focus = false) {
    gesture = null;
    settlement = { g, index, focus };
    const destination = (g.active - index) * g.travel;
    if (reducedMotion.matches || Math.abs(g.offset - destination) < 0.5) {
      finish();
      return;
    }
    viewport.getBoundingClientRect();
    viewport.classList.add("is-settling");
    viewport.style.height = `${g.panels[index].offsetHeight}px`;
    translate(g.panels[g.active], destination);
    if (g.preview >= 0) translate(g.panels[g.preview], destination + (g.preview - g.active) * g.travel);
    timer = setTimeout(finish, 260);
  }

  function cancel() {
    if (gesture?.horizontal) settle(gesture, gesture.active);
    else gesture = null;
  }

  function ownsGesture(target) {
    if (target.closest("a, input, textarea, select, iframe, [contenteditable]:not([contenteditable='false']), [data-swipe-ignore]")) return true;
    if (target.closest("button") && !target.closest(".tab-btn")) return true;
    for (let node = target; node && node !== group; node = node.parentElement) {
      if (node.scrollWidth > node.clientWidth && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
    }
    return false;
  }

  group.addEventListener("touchstart", event => {
    finish();
    reset();
    suppressClick = false;
    if (event.touches.length !== 1 || ownsGesture(event.target) || !getSelection().isCollapsed) return;
    const buttons = [...group.querySelectorAll("#tabs button")];
    const panels = buttons.map(button => document.getElementById(button.getAttribute("aria-controls")));
    const active = buttons.findIndex(button => button.getAttribute("aria-selected") === "true");
    if (active < 0) return;
    const touch = event.touches[0];
    gesture = { buttons, panels, active, id: touch.identifier, x: touch.clientX, y: touch.clientY,
      travel: viewport.clientWidth + 16, horizontal: false, preview: -1, offset: 0 };
  }, { passive: true });

  group.addEventListener("touchmove", event => {
    if (!gesture) return;
    if (event.touches.length !== 1 || !event.cancelable || !getSelection().isCollapsed) { cancel(); return; }
    const touch = event.touches[0];
    const x = Math.abs(touch.clientX - gesture.x), y = Math.abs(touch.clientY - gesture.y);
    if (!gesture.horizontal) {
      if (Math.max(x, y) < 10) return;
      if (y >= x) { gesture = null; return; }
      if (x < y * 1.5) return;
      gesture.horizontal = true;
    }
    event.preventDefault();
    suppressClick = true;
    drag(gesture, touch.clientX - gesture.x);
  }, { passive: false });

  group.addEventListener("touchend", event => {
    const g = gesture;
    gesture = null;
    if (!g?.horizontal) return;
    const touch = [...event.changedTouches].find(item => item.identifier === g.id);
    let next = g.active;
    if (touch && !event.touches.length && getSelection().isCollapsed) {
      const x = touch.clientX - g.x, y = touch.clientY - g.y;
      drag(g, x);
      if (Math.abs(x) >= 48 && Math.abs(x) >= Math.abs(y) * 1.5) {
        next = Math.max(0, Math.min(g.buttons.length - 1, g.active + (x < 0 ? 1 : -1)));
      }
    }
    settle(g, next, next !== g.active && group.contains(document.activeElement));
  }, { passive: true });

  group.addEventListener("touchcancel", cancel, { passive: true });
  window.addEventListener("touchstart", event => {
    if (event.touches.length > 1) cancel();
  }, { passive: true });
  viewport.addEventListener("transitionend", event => {
    if (settlement && event.target === settlement.g.panels[settlement.g.active]
        && event.propertyName === "transform") finish();
  });
  group.addEventListener("click", event => {
    if (suppressClick && event.detail) { event.preventDefault(); event.stopPropagation(); }
    suppressClick = false;
  }, true);
  group.addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch") suppressClick = false;
  });
  window.addEventListener("resize", () => { if (settlement) finish(); else reset(); });
  reducedMotion.addEventListener("change", finish);
  return { reset };
};
