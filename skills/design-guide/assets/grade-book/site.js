"use strict";

(() => {
  const root = document.documentElement;
  const book = document.querySelector("#book");
  const stage = document.querySelector(".book-stage");
  const downloads = document.querySelector("#downloads");
  const cover = document.querySelector(".cover-front");
  const opener = document.querySelector(".cover-opener");
  const coverImage = opener.querySelector("img");
  const coverTexture = document.createElement("canvas");
  const coverContext = coverImage.classList.contains("cover-flat")
    ? coverTexture.getContext("2d", { alpha: false }) : null;
  const inside = book.querySelector(".cover-inside");
  const caption = document.querySelector(".open-label");
  const closeBook = document.querySelector(".close-book");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const narrowBook = matchMedia("(max-width: 860px)");
  const LEAF_MS = 1100;
  const support = document.querySelector(".support");
  const supportDropdowns = [...support.querySelectorAll(".support-dropdown")];
  const editions = document.querySelector("#editions");
  const defaultView = { x: 5, y: -22 };
  let rotation = { ...defaultView };
  let zoom = 1;
  let requestedZoom = 1;
  let gesture = null;
  let spinFrame = 0;
  let suppressClick = false;
  let focusTimer;
  let settleTimer;
  const renderer = window.createBookRenderer?.({ stage, book, image: coverImage });

  function drawCover() {
    if (!coverContext || !coverImage.complete || !coverImage.naturalWidth) return;
    // Prefilter the large original before the 3D projection. Directly shrinking
    // it on the GPU aliases fine type and the barcode at oblique angles.
    const width = opener.clientWidth * zoom;
    const height = opener.clientHeight * zoom;
    if (!width || !height) return;
    const density = Math.min((devicePixelRatio || 1) * 1.5,
      coverImage.naturalWidth / width, coverImage.naturalHeight / height);
    const pixelsWide = Math.min(coverImage.naturalWidth, Math.ceil(width * density));
    const pixelsHigh = Math.min(coverImage.naturalHeight, Math.ceil(height * density));
    if (coverTexture.width === pixelsWide && coverTexture.height === pixelsHigh) return;
    coverTexture.width = pixelsWide;
    coverTexture.height = pixelsHigh;
    coverContext.imageSmoothingEnabled = true;
    coverContext.imageSmoothingQuality = "high";
    coverContext.drawImage(coverImage, 0, 0, pixelsWide, pixelsHigh);
    if (!coverTexture.isConnected) {
      coverTexture.className = "cover-texture";
      coverTexture.setAttribute("aria-hidden", "true");
      opener.append(coverTexture);
      coverImage.hidden = true;
    }
  }
  coverImage.addEventListener("load", drawCover);

  function drawRotation() {
    rotation.y = ((rotation.y + 180) % 360 + 360) % 360 - 180;
    rotation.x = Math.max(-75, Math.min(75, rotation.x));
    book.style.setProperty("--rotation-x", rotation.x + "deg");
    book.style.setProperty("--rotation-y", rotation.y + "deg");
    fitZoom();
    renderer?.invalidate(book.classList.contains("is-rotating") ? 0 : 1050);
  }
  function stopSpin() {
    cancelAnimationFrame(spinFrame);
    spinFrame = 0;
    book.classList.remove("is-rotating", "is-dragging");
  }
  function resetRotation() {
    stopSpin();
    rotation = { ...defaultView };
    drawRotation();
  }
  const zoomMin = .55;
  // At the highest zoom the cover extends this far past the screen edges.
  const screenPast = 40;
  let zoomedByUser = false;
  let restingZoom = 1;
  function coverSpan(scale) {
    const corners = frontCorners(scale);
    const top = Math.min(...corners.map(corner => corner.y));
    const bottom = Math.max(...corners.map(corner => corner.y));
    return { top, bottom, height: bottom - top, center: (top + bottom) / 2 };
  }
  // Closed-book ceiling: the highest and lowest points of the cover are just
  // off the screen. The tilt and the header make one side travel further.
  function closedCeiling() {
    const clears = scale => {
      const span = coverSpan(scale);
      return span.top <= -screenPast && span.bottom >= innerHeight + screenPast;
    };
    let low = zoomMin;
    let high = 8;
    if (!clears(high)) return high;
    for (let step = 0; step < 16; step++) {
      const mid = (low + high) / 2;
      if (clears(mid)) high = mid;
      else low = mid;
    }
    return high;
  }
  // Open pages are flat, so their on-screen height is the layout height times
  // the zoom. The ceiling is the screen plus the same small strip.
  function openCeiling() {
    const height = book.offsetHeight;
    if (!height) return 8;
    return (innerHeight + screenPast * 2) / height;
  }
  // Shift an overflowing open page so the excess height is split evenly across
  // the top and bottom of the screen.
  function liftMargin() {
    if (!zoomedByUser || !book.classList.contains("is-open") || book.classList.contains("is-closing")) return 0;
    const scale = zoom || 1;
    const visualHeight = book.offsetHeight * scale;
    if (visualHeight <= innerHeight) return 0;
    const stageRect = stage.getBoundingClientRect();
    const center = stageRect.top + stageRect.height / 2;
    return (innerHeight / 2 - center) / scale;
  }
  function zoomCeiling() {
    return book.classList.contains("is-open") && !book.classList.contains("is-closing")
      ? openCeiling() : closedCeiling();
  }
  function setZoom(value) {
    zoomedByUser = true;
    requestedZoom = Math.max(zoomMin, Math.min(zoomCeiling(), value));
    fitZoom();
  }
  // Corners of the tilted book, in stage pixels. Perspective makes the
  // near edge larger than the layout box, which is what clips the cover.
  function frontCorners(scale) {
    const width = book.offsetWidth;
    const height = book.offsetHeight;
    const tilt = narrowBook.matches ? -2 : -3;
    const matrix = new DOMMatrix().rotateAxisAngle(1, 0, 0, rotation.x)
      .multiply(new DOMMatrix().rotateAxisAngle(0, 1, 0, rotation.y))
      .multiply(new DOMMatrix().rotateAxisAngle(0, 0, 1, tilt));
    const perspective = parseFloat(getComputedStyle(stage).perspective) || 1800;
    const centerX = stage.clientWidth / 2;
    const centerY = stage.clientHeight / 2;
    const origin = stage.getBoundingClientRect().top;
    const corners = [];
    for (const x of [-width / 2 - 2, width / 2]) {
      for (const y of [-height / 2, height / 2]) {
        const point = matrix.transformPoint(new DOMPoint(x * scale, y * scale, 0));
        const w = 1 - point.z / perspective;
        corners.push({
          x: centerX + point.x / w,
          y: origin + centerY + point.y / w,
          edge: y < 0 ? "top" : "bottom"
        });
      }
    }
    return corners;
  }
  function projectedBounds(scale, depths = [-22, 2]) {
    const width = book.offsetWidth;
    const height = book.offsetHeight;
    const tilt = narrowBook.matches ? -2 : -3;
    const matrix = new DOMMatrix().rotateAxisAngle(1, 0, 0, rotation.x)
      .multiply(new DOMMatrix().rotateAxisAngle(0, 1, 0, rotation.y))
      .multiply(new DOMMatrix().rotateAxisAngle(0, 0, 1, tilt));
    const stageStyle = getComputedStyle(stage);
    const perspective = parseFloat(stageStyle.perspective) || 1800;
    const centerX = stage.clientWidth / 2;
    const centerY = stage.clientHeight / 2;
    const bounds = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
    for (const x of [-width / 2 - 2, width / 2]) {
      for (const y of [-height / 2, height / 2]) {
        for (const z of depths) {
          const point = matrix.transformPoint(new DOMPoint(x * scale, y * scale, z * scale));
          const w = 1 - point.z / perspective;
          const sx = centerX + point.x / w;
          const sy = centerY + point.y / w;
          bounds.left = Math.min(bounds.left, sx);
          bounds.top = Math.min(bounds.top, sy);
          bounds.right = Math.max(bounds.right, sx);
          bounds.bottom = Math.max(bounds.bottom, sy);
        }
      }
    }
    return bounds;
  }
  function closedFit(requested) {
    const margin = 12;
    const maxX = stage.clientWidth - margin;
    const maxY = stage.clientHeight - margin;
    const fits = scale => {
      const box = projectedBounds(scale);
      return box.left >= margin && box.top >= margin && box.right <= maxX && box.bottom <= maxY;
    };
    if (fits(requested)) return requested;
    let low = .05;
    let high = requested;
    for (let step = 0; step < 14; step++) {
      const mid = (low + high) / 2;
      if (fits(mid)) low = mid;
      else high = mid;
    }
    return low;
  }
  function fitZoom() {
    const ceiling = zoomCeiling();
    if (!book.classList.contains("is-open") || book.classList.contains("is-closing")) {
      // Resting zoom keeps the whole cover on screen. A deliberate zoom may
      // continue until the top and bottom are just past the screen.
      restingZoom = closedFit(Math.min(1, ceiling));
      zoom = zoomedByUser ? Math.min(requestedZoom, ceiling) : restingZoom;
    } else {
      const spread = innerWidth > 860 ? 2 : 1;
      const widthFit = stage.clientWidth * .94 / (book.offsetWidth * spread);
      const pad = parseFloat(getComputedStyle(stage).paddingTop) + parseFloat(getComputedStyle(stage).paddingBottom);
      const available = stage.clientHeight - pad;
      const heightFit = available > 40 && book.offsetHeight ? available / book.offsetHeight : widthFit;
      const fitted = Math.max(zoomMin, Math.min(widthFit, heightFit));
      restingZoom = Math.min(fitted, 1);
      zoom = zoomedByUser ? Math.min(requestedZoom, ceiling) : restingZoom;
    }
    // On a phone the closed cover and the open page want different sizes.
    // Change scale across the whole turn. A short zoom ease swells the book
    // and then lets it sink while the page is still moving.
    const turning = narrowBook.matches && book.classList.contains("is-open")
      && !book.classList.contains("is-open-flat");
    stage.style.transition = zoomedByUser || !turning
      ? ""
      : "--book-scale 1100ms cubic-bezier(.42,0,.58,1)";
    stage.style.setProperty("--book-scale", zoom);
    stage.style.setProperty("--book-height", book.offsetHeight + "px");
    stage.style.setProperty("--book-lift", liftMargin() + "px");
    // Past the resting size the cover has to leave the page and reach the
    // screen edges. The page otherwise clips it under the header.
    root.classList.toggle("is-book-zoomed", zoomedByUser && zoom > restingZoom + 0.02);
    drawCover();
    renderer?.invalidate();
  }
  function leafAnimations() {
    return [cover, inside].flatMap(element => element.getAnimations().filter(animation =>
      String(animation.animationName || "").startsWith("mobile-leaf")));
  }
  // Opening and closing use different leaf animations. Closing starts at the
  // open pose, so a close that interrupts the turn seeks to the matching point.
  function steerLeaf(opening, resumeAt) {
    const bookTurn = book.getAnimations().filter(animation => animation.animationName === "mobile-book-close");
    const animations = leafAnimations();
    for (const animation of animations.concat(bookTurn)) {
      animation.pause();
      animation.playbackRate = 1;
      if (!opening) animation.currentTime = resumeAt;
      if (animation.playState !== "running") animation.play();
    }
    return animations[0];
  }
  function setOpen(open, focus = true) {
    clearTimeout(focusTimer);
    clearTimeout(settleTimer);
    renderer?.suspend();
    const wasFlat = book.classList.contains("is-open-flat");
    const motion = Boolean(focus) && !reducedMotion.matches;
    const narrow = narrowBook.matches && motion;
    const shutNarrow = narrow && !open && book.classList.contains("is-open");
    let resumeClose = 0;
    if (shutNarrow && !wasFlat) {
      const running = leafAnimations();
      const time = running.length ? Number(running[0].currentTime) || 0 : 0;
      resumeClose = Math.max(0, LEAF_MS - time);
    }
    // Restore the flat spread to the 3D pose before a close can move.
    // Opening an already open narrow book leaves the settled page alone.
    if (wasFlat && (shutNarrow || !narrow)) {
      book.classList.add("is-restoring");
      book.classList.remove("is-open-flat");
      void book.offsetWidth;
      book.classList.remove("is-restoring");
    }
    stopSpin();
    gesture = null;
    if (!shutNarrow) book.classList.toggle("is-open", open);
    book.classList.toggle("is-closing", shutNarrow);
    downloads.inert = !open;
    downloads.setAttribute("aria-hidden", String(!open));
    cover.inert = open;
    opener.setAttribute("aria-expanded", String(open));
    caption.firstChild.textContent = open ? "Close the book " : "Open the book ";
    caption.classList.toggle("is-open", open);
    resetRotation();
    fitZoom();
    // Fit the spread after the class change, then aim the leaf. Seeking
    // earlier is undone when the layout pass restarts the animation.
    const leaf = narrow && (shutNarrow || open) ? steerLeaf(open, resumeClose) : null;
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      if (open) book.classList.add("is-open-flat");
      else {
        book.classList.remove("is-open", "is-closing");
        fitZoom();
        renderer?.resume();
      }
    };
    if (!motion) settle();
    else if (leaf) {
      leaf.onfinish = settle;
      const time = Number(leaf.currentTime) || 0;
      const rate = leaf.playbackRate || 1;
      settleTimer = setTimeout(settle, (rate < 0 ? time : LEAF_MS - time) / Math.abs(rate) + 50);
    } else settleTimer = setTimeout(settle, 1120);
    if (focus) {
      if (!open) opener.focus({ preventScroll: true });
      else focusTimer = setTimeout(() => {
        if (!supportDropdowns.some(dropdown => dropdown.open)) downloads.querySelector("a").focus({ preventScroll: true });
      }, reducedMotion.matches ? 0 : 1100);
    }
  }
  opener.addEventListener("click", () => setOpen(true));
  closeBook.addEventListener("click", () => setOpen(false));
  inside.addEventListener("click", () => {
    if (book.classList.contains("is-open") && !book.classList.contains("is-closing")) setOpen(false);
  });
  caption.addEventListener("click", () => setOpen(!book.classList.contains("is-open")));
  root.classList.add("is-enhanced");
  for (const element of [caption, closeBook, document.querySelector(".page-utilities"),
                        document.querySelector(".edition-filter-controls")]) element.hidden = false;
  setOpen(location.hash === "#downloads", false);

  function inScrollable(node) {
    for (let element = node instanceof Element ? node : node?.parentElement; element && element !== document.body; element = element.parentElement) {
      const overflow = getComputedStyle(element).overflowY;
      if ((overflow === "auto" || overflow === "scroll") && element.scrollHeight > element.clientHeight + 1) return true;
    }
    return false;
  }
  addEventListener("wheel", event => {
    if (event.ctrlKey || event.metaKey || inScrollable(event.target)) return;
    event.preventDefault();
  }, { passive: false });
  addEventListener("touchstart", event => {
    if (event.touches.length < 2) return;
    finishDrag(null, true);
    stopSpin();
  }, { passive: true });
  addEventListener("touchmove", event => {
    if (event.touches.length > 1 || inScrollable(event.target)) return;
    event.preventDefault();
  }, { passive: false });
  stage.addEventListener("wheel", event => {
    if (event.ctrlKey || event.metaKey || event.deltaY === 0) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1;
    const delta = Math.max(-300, Math.min(300, event.deltaY * unit));
    setZoom(zoom * Math.exp(-delta * .0015));
  }, { passive: false });
  stage.addEventListener("keydown", event => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (!["+", "=", "-", "0"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "0") {
      zoomedByUser = false;
      requestedZoom = 1;
      fitZoom();
    } else setZoom(zoom * (event.key === "-" ? 1 / 1.15 : 1.15));
  });
  addEventListener("resize", fitZoom);
  new ResizeObserver(fitZoom).observe(stage);

  // Keep native click/keyboard activation. Capture only once a real drag starts.
  // A finger uses the same gesture. Touch needs a little more travel so a tap still opens.
  // Touch already captures to its target; retargeting that capture cancels the drag.
  stage.addEventListener("pointerdown", event => {
    if (book.classList.contains("is-open")) return;
    if (event.pointerType === "touch" && !event.isPrimary) {
      finishDrag(null, true);
      stopSpin();
      return;
    }
    if (event.button !== 0 || !event.isPrimary) return;
    stopSpin();
    gesture = { id: event.pointerId, startX: event.clientX, startY: event.clientY,
      lastX: event.clientX, lastY: event.clientY, time: event.timeStamp,
      initial: { ...rotation }, dragging: false, vx: 0, vy: 0,
      touch: event.pointerType === "touch",
      slop: event.pointerType === "touch" ? 10 : 6 };
  });
  window.addEventListener("pointermove", event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.startX;
    const dy = event.clientY - gesture.startY;
    if (!gesture.dragging && Math.hypot(dx, dy) < gesture.slop) return;
    if (!gesture.dragging) {
      gesture.dragging = true;
      if (!gesture.touch) stage.setPointerCapture(event.pointerId);
      book.classList.add("is-rotating", "is-dragging");
    }
    event.preventDefault();
    const elapsed = Math.max(8, event.timeStamp - gesture.time);
    gesture.vy = Math.max(-1.2, Math.min(1.2, (event.clientX - gesture.lastX) * .55 / elapsed));
    gesture.vx = Math.max(-.5, Math.min(.5, -(event.clientY - gesture.lastY) * .35 / elapsed));
    rotation = { x: gesture.initial.x - dy * .35, y: gesture.initial.y + dx * .55 };
    gesture.lastX = event.clientX;
    gesture.lastY = event.clientY;
    gesture.time = event.timeStamp;
    drawRotation();
  }, { passive: false });
  function finishDrag(event, cancelled = false) {
    if (!gesture || (event && gesture.id !== event.pointerId)) return;
    const ended = gesture;
    gesture = null;
    if (stage.hasPointerCapture(ended.id)) stage.releasePointerCapture(ended.id);
    book.classList.remove("is-dragging");
    if (!ended.dragging) return;
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 400);
    if (cancelled || reducedMotion.matches || !event || event.timeStamp - ended.time > 100) {
      stopSpin();
      return;
    }
    let previous = performance.now();
    const started = previous;
    function coast(now) {
      const elapsed = Math.min(32, now - previous);
      previous = now;
      ended.vx *= Math.pow(.88, elapsed / 16);
      ended.vy *= Math.pow(.88, elapsed / 16);
      rotation.x += ended.vx * elapsed;
      rotation.y += ended.vy * elapsed;
      drawRotation();
      if (now - started < 700 && Math.abs(ended.vx) + Math.abs(ended.vy) > .015) {
        spinFrame = requestAnimationFrame(coast);
      } else stopSpin();
    }
    spinFrame = requestAnimationFrame(coast);
  }
  window.addEventListener("pointerup", event => finishDrag(event));
  window.addEventListener("pointercancel", event => finishDrag(event, true));
  stage.addEventListener("lostpointercapture", event => {
    if (event.target === stage) finishDrag(event, true);
  });
  window.addEventListener("blur", () => { finishDrag(null, true); stopSpin(); });
  stage.addEventListener("dragstart", event => event.preventDefault());
  stage.addEventListener("click", event => {
    if (suppressClick) {
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressClick = false;
    } else if (!book.classList.contains("is-open") && book.contains(event.target)
        && !event.target.closest("button")) setOpen(true);
  }, true);
  opener.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const directions = { ArrowLeft: [0, -15], ArrowRight: [0, 15], ArrowUp: [-10, 0], ArrowDown: [10, 0] };
    if (event.key === "Home") { event.preventDefault(); resetRotation(); }
    if (directions[event.key]) {
      event.preventDefault();
      stopSpin();
      book.classList.add("is-rotating");
      rotation.x += directions[event.key][0];
      rotation.y += directions[event.key][1];
      drawRotation();
    }
  });
  function showDownloads() {
    setOpen(true);
  }
  document.querySelectorAll('a[href="#downloads"]').forEach(link =>
    link.addEventListener("click", event => {
      event.preventDefault();
      history.replaceState(null, "", "#downloads");
      showDownloads();
    }));
  addEventListener("hashchange", () => { if (location.hash === "#downloads") showDownloads(); });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const expanded = supportDropdowns.find(dropdown => dropdown.classList.contains("is-expanded"));
    if (expanded) {
      // Handle Escape before the search field consumes it to clear its value.
      event.preventDefault();
      setDropdownOpen(expanded, false);
      expanded.querySelector("summary").focus({ preventScroll: true });
    } else if (book.classList.contains("is-open")) setOpen(false);
  }, true);

  const query = document.querySelector("#edition-query");
  const from = document.querySelector("#edition-from");
  const through = document.querySelector("#edition-to");
  const clearSearch = document.querySelector(".clear-search");
  const clearFilters = document.querySelector(".clear-filters");
  const sortButton = document.querySelector(".sort-editions");
  const count = document.querySelector("#edition-count");
  const list = document.querySelector(".edition-list");
  const normalize = text => text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/g, " ").trim();
  const entries = [...list.children].map(element => ({ element, search: normalize(element.dataset.search),
    date: element.dataset.date, published: element.dataset.published }));
  let newestFirst = true;

  function filterEditions() {
    // Quoted phrases remain together; other words may occur anywhere.
    const terms = [...normalize(query.value).matchAll(/"([^"]+)"|([^\s"]+)/g)]
      .map(match => match[1] || match[2]);
    const invalidDates = Boolean(from.value && through.value && from.value > through.value);
    document.querySelector("#date-error").hidden = !invalidDates;
    through.setAttribute("aria-invalid", String(invalidDates));
    clearSearch.hidden = !query.value;
    clearFilters.hidden = !(query.value || from.value || through.value);
    entries.sort((a, b) => (newestFirst ? -1 : 1) * a.published.localeCompare(b.published));
    let visible = 0;
    for (const entry of entries) {
      const matches = !invalidDates && (!from.value || entry.date >= from.value)
        && (!through.value || entry.date <= through.value)
        && terms.every(term => /^\d+$/.test(term)
          ? new RegExp(`\\b${term}\\b`).test(entry.search) : entry.search.includes(term));
      entry.element.hidden = !matches;
      visible += Number(matches);
      list.append(entry.element);
    }
    count.textContent = `${visible} of ${entries.length} edition${entries.length === 1 ? "" : "s"}`;
    document.querySelector(".no-editions").hidden = visible > 0 || invalidDates;
  }
  for (const input of [query, from, through]) input.addEventListener("input", filterEditions);
  clearSearch.addEventListener("click", () => { query.value = ""; filterEditions(); query.focus(); });
  clearFilters.addEventListener("click", () => {
    query.value = from.value = through.value = "";
    filterEditions();
    query.focus();
  });
  sortButton.addEventListener("click", () => {
    newestFirst = !newestFirst;
    sortButton.textContent = newestFirst ? "Newest first" : "Oldest first";
    filterEditions();
  });
  const dropdownAnimations = new WeakMap();
  function setDropdownOpen(dropdown, expanded, focusSearch = false) {
    if (dropdown.classList.contains("is-expanded") === expanded) return;
    const panel = dropdown.querySelector(".support-panel");
    const startHeight = dropdown.open ? panel.getBoundingClientRect().height : 0;
    const currentStyle = getComputedStyle(panel);
    const startOpacity = dropdown.open ? currentStyle.opacity : 0;
    const startPaddingTop = dropdown.open ? currentStyle.paddingTop : "0px";
    const startPaddingBottom = dropdown.open ? currentStyle.paddingBottom : "0px";
    dropdownAnimations.get(dropdown)?.cancel();
    dropdown.classList.remove("is-animating");
    if (expanded) {
      clearTimeout(focusTimer);
      stopSpin();
      for (const other of supportDropdowns) if (other !== dropdown) setDropdownOpen(other, false);
      dropdown.open = true;
      if (dropdown === editions) filterEditions();
    }
    dropdown.classList.toggle("is-expanded", expanded);
    dropdown.querySelector("summary").setAttribute("aria-expanded", String(expanded));
    panel.inert = !expanded;
    document.body.classList.toggle("support-expanded",
      supportDropdowns.some(item => item.classList.contains("is-expanded")));
    const endHeight = expanded ? panel.getBoundingClientRect().height : 0;
    const finish = () => {
      dropdown.classList.remove("is-animating");
      dropdown.open = expanded;
      dropdownAnimations.delete(dropdown);
      if (expanded && focusSearch && dropdown === editions) query.focus({ preventScroll: true });
    };
    if (reducedMotion.matches) { finish(); return; }
    dropdown.classList.add("is-animating");
    const animation = panel.animate([
      { height: startHeight + "px", opacity: startOpacity, paddingTop: startPaddingTop,
        paddingBottom: startPaddingBottom, transform: expanded ? "translateY(-4px)" : "none" },
      { height: endHeight + "px", opacity: expanded ? 1 : 0, paddingTop: expanded ? "8px" : "0px",
        paddingBottom: expanded ? "16px" : "0px", transform: expanded ? "none" : "translateY(-4px)" },
    ], { duration: 280, easing: "cubic-bezier(.22,.8,.2,1)" });
    dropdownAnimations.set(dropdown, animation);
    animation.onfinish = finish;
  }
  for (const dropdown of supportDropdowns) {
    // Keep native details as the no-JavaScript fallback; animate both directions.
    dropdown.removeAttribute("name");
    dropdown.querySelector(".support-panel").inert = true;
    dropdown.querySelector("summary").setAttribute("aria-expanded", "false");
    dropdown.querySelector("summary").addEventListener("click", event => {
      event.preventDefault();
      setDropdownOpen(dropdown, !dropdown.classList.contains("is-expanded"), event.detail === 0);
    });
  }
  function dismissSupport(event) {
    if (!support.contains(event.target)) {
      for (const dropdown of supportDropdowns) setDropdownOpen(dropdown, false);
    }
  }
  document.addEventListener("pointerdown", dismissSupport);
  document.addEventListener("focusin", dismissSupport);

  const themeButton = document.querySelector(".theme-toggle");
  const systemDark = matchMedia("(prefers-color-scheme: dark)");
  function setTheme(theme, persist = false) {
    root.dataset.theme = theme;
    const label = `Switch to ${theme === "dark" ? "light" : "dark"} theme`;
    themeButton.setAttribute("aria-label", label);
    themeButton.dataset.tooltip = label;
    themeButton.querySelector("use").setAttribute("href", theme === "dark" ? "#icon-sun" : "#icon-moon");
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#080b0d" : "#ecefec";
    renderer?.invalidate();
    if (persist) {
      try { localStorage.setItem("grade-book-theme", theme); } catch { /* Storage may be disabled. */ }
    }
  }
  function savedTheme() {
    try {
      const value = localStorage.getItem("grade-book-theme");
      return value === "dark" || value === "light" ? value : null;
    } catch {
      return null;
    }
  }
  setTheme(savedTheme() ?? (systemDark.matches ? "dark" : "light"));
  systemDark.addEventListener("change", () => {
    if (!savedTheme()) setTheme(systemDark.matches ? "dark" : "light");
  });
  const toggleTheme = () => setTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
  themeButton.addEventListener("click", toggleTheme);


  const tooltip = document.querySelector("#tooltip");
  let tooltipTimer, tooltipTarget;
  function hideTooltip() {
    clearTimeout(tooltipTimer);
    tooltip.hidden = true;
    tooltipTarget?.removeAttribute("aria-describedby");
    tooltipTarget = null;
  }
  function positionTooltip() {
    if (!tooltipTarget) return;
    const rect = tooltipTarget.getBoundingClientRect();
    const bounds = tooltip.getBoundingClientRect();
    tooltip.style.left = Math.max(12, Math.min(innerWidth - bounds.width - 12,
      rect.left + rect.width / 2 - bounds.width / 2)) + "px";
    tooltip.style.top = (rect.top >= bounds.height + 20
      ? rect.top - bounds.height - 8 : rect.bottom + 8) + "px";
  }
  document.querySelectorAll("[data-tooltip]").forEach(button => {
    const show = () => {
      hideTooltip();
      tooltipTimer = setTimeout(() => {
        tooltipTarget = button;
        tooltip.textContent = button.dataset.tooltip;
        tooltip.hidden = false;
        button.setAttribute("aria-describedby", "tooltip");
        positionTooltip();
      }, 150);
    };
    button.addEventListener("pointerenter", show);
    button.addEventListener("focus", show);
    button.addEventListener("pointerleave", hideTooltip);
    button.addEventListener("blur", hideTooltip);
    button.addEventListener("click", hideTooltip);
  });
  addEventListener("resize", positionTooltip);
  addEventListener("scroll", positionTooltip, { passive: true });
  visualViewport?.addEventListener("resize", positionTooltip);
  document.addEventListener("keydown", event => { if (event.key === "Escape") hideTooltip(); });
})();
