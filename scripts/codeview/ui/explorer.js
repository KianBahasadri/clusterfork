/* Shared collection controls for the dashboard and opaque module frames. */
"use strict";

window.CodeviewUI = (() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const paths = {
    up: '<path d="m5 12 7-7 7 7M12 19V5"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.34-4.34"/>',
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${paths[name]}</svg>`;
  let serial = 0;
  let activePopup = null;

  function position(popup, trigger) {
    const bounds = trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0;
    const top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth;
    const height = viewport?.height || innerHeight;
    popup.style.width = `${Math.min(bounds.width, width - 24)}px`;
    popup.style.left = `${Math.max(left + 12, Math.min(bounds.left, left + width - popup.offsetWidth - 12))}px`;
    popup.style.top = `${bounds.bottom + 6 + popup.offsetHeight <= top + height - 12
      ? bounds.bottom + 6 : Math.max(top + 12, bounds.top - popup.offsetHeight - 6)}px`;
  }

  function picker(label, choices, value, onChange) {
    const options = choices.map(option => typeof option === "string"
      ? { value: option, label: option } : option);
    const id = `cv-picker-${++serial}`;
    const root = document.createElement("div");
    root.className = "collection-picker";
    root.innerHTML = `<label class="form-label" for="${id}">${escape(label)}</label>
      <div class="picker-field"><input id="${id}" class="input-field" role="combobox"
        aria-autocomplete="list" aria-haspopup="listbox" aria-expanded="false"
        aria-controls="${id}-options" autocomplete="off" spellcheck="false">${icon("down")}</div>
      <div class="picker-options" id="${id}-options" role="listbox" aria-label="${escape(label)}" popover="manual"></div>
      <span class="sr-only" aria-live="polite"></span>`;
    const input = root.querySelector("input");
    const popup = root.querySelector('[role="listbox"]');
    const status = root.querySelector('[aria-live]');
    let selected = options.find(option => option.value === value) || options[0];
    let visible = options;
    let cursor = 0;
    let opened = false;
    input.value = selected?.label || "";
    const measure = document.createElement("canvas").getContext("2d");
    measure.font = `14px ${getComputedStyle(document.body).fontFamily}`;
    root.style.width = `${Math.max(176, Math.min(232, Math.max(
      measure.measureText(input.value).width + 52,
      ...options.map(option => measure.measureText(option.label).width + 28))))}px`;

    function render() {
      popup.innerHTML = visible.map(option => `<div role="option" id="${id}-option-${options.indexOf(option)}"
        aria-selected="${option.value === selected?.value}" data-index="${options.indexOf(option)}"
        class="picker-option${visible.indexOf(option) === cursor ? " is-active" : ""}">${escape(option.label)}</div>`).join("")
        || '<div class="picker-empty">No matching options</div>';
      const current = visible[cursor];
      if (current) input.setAttribute("aria-activedescendant", `${id}-option-${options.indexOf(current)}`);
      else input.removeAttribute("aria-activedescendant");
      status.textContent = `${visible.length} options available`;
    }
    function close() {
      if (!opened) return;
      opened = false;
      popup.hidePopover();
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      input.value = selected?.label || "";
      if (activePopup?.root === root) activePopup = null;
    }
    function open(filter = false) {
      if (!opened) {
        activePopup?.close();
        activePopup = { root, close };
        opened = true;
        popup.showPopover();
        input.setAttribute("aria-expanded", "true");
      }
      visible = filter ? options.filter(option => option.label.toLocaleLowerCase()
        .includes(input.value.trim().toLocaleLowerCase())) : options;
      cursor = Math.max(0, visible.indexOf(selected));
      render();
      position(popup, root.querySelector(".picker-field"));
    }
    function commit(option) {
      if (!option) return;
      const changed = selected?.value !== option.value;
      selected = option;
      close();
      input.value = option.label;
      if (changed) onChange(option.value);
    }
    input.addEventListener("click", () => { open(); input.select(); });
    input.addEventListener("input", () => open(true));
    input.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!opened) open();
        else cursor = Math.max(0, Math.min(visible.length - 1, cursor + (event.key === "ArrowDown" ? 1 : -1)));
        render();
        popup.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (opened) commit(visible[cursor]);
        else open();
      } else if (event.key === "Escape" && opened) {
        event.preventDefault(); event.stopPropagation(); close();
      } else if (event.key === "Tab") close();
    });
    popup.addEventListener("pointerdown", event => event.preventDefault());
    popup.addEventListener("click", event => {
      const option = event.target.closest("[data-index]");
      if (option) commit(options[Number(option.dataset.index)]);
    });
    root.querySelector(".picker-field > svg").addEventListener("click", () => { input.focus(); opened ? close() : open(); });
    input.addEventListener("blur", close);
    root.setValue = next => {
      selected = options.find(option => option.value === next) || options[0];
      close();
      input.value = selected?.label || "";
    };
    return root;
  }

  document.addEventListener("pointerdown", event => {
    if (activePopup && !activePopup.root.contains(event.target)) activePopup.close();
  });
  window.addEventListener("blur", () => activePopup?.close());
  window.addEventListener("resize", () => activePopup?.close());
  document.addEventListener("scroll", event => {
    if (activePopup && !activePopup.root.contains(event.target)) activePopup.close();
  }, true);

  function download(name, contents, type = "text/plain") {
    const url = URL.createObjectURL(new Blob([contents], { type }));
    const link = document.createElement("a");
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function inspect(title, body, trigger = document.activeElement) {
    const dialog = document.createElement("dialog");
    const id = `cv-inspect-${++serial}`;
    dialog.className = "modal-dialog collection-dialog";
    dialog.setAttribute("aria-labelledby", id);
    dialog.innerHTML = `<div class="modal-header"><h2 class="modal-title" id="${id}">${escape(title)}</h2>
      <button type="button" class="btn-icon" aria-label="Close" title="Close">${icon("close")}</button></div>
      <div class="modal-body">${body}</div>`;
    document.body.appendChild(dialog);
    dialog.querySelector(".btn-icon").onclick = () => dialog.close();
    dialog.onclick = event => { if (event.target === dialog) dialog.close(); };
    dialog.addEventListener("close", () => { dialog.remove(); trigger?.focus?.({ preventScroll: true }); });
    dialog.showModal();
    dialog.querySelector("button").focus();
    return dialog;
  }

  function collection(table, { toolbar, search, label = "records", filters = [], presets = [], pageSize = 25, compact = false,
      onUpdate = () => {}, exportName = table.id || "records" } = {}) {
    const region = table.closest(".table-container");
    let page = 0;
    let matched = [];
    let predicate = () => true;
    const chosen = new Map();
    const pickers = [];
    if (!toolbar) {
      toolbar = document.createElement("div");
      toolbar.className = "collection-toolbar";
      region.before(toolbar);
    }
    const count = document.createElement("span");
    count.className = "filter-count";
    count.setAttribute("aria-live", "polite");
    toolbar.appendChild(count);
    const footer = document.createElement("div");
    footer.className = "collection-footer";
    footer.innerHTML = `<span class="collection-range"></span><div class="collection-pages">
      <button class="btn-icon" type="button" aria-label="Previous page" title="Previous page">${icon("left")}</button>
      <span class="collection-page"></span>
      <button class="btn-icon" type="button" aria-label="Next page" title="Next page">${icon("right")}</button></div>`;
    region.after(footer);
    const empty = document.createElement("div");
    empty.className = "collection-empty";
    empty.innerHTML = `<strong>No matches.</strong> <button type="button" class="btn btn-quiet">Reset filters</button>`;
    footer.after(empty);
    const previous = footer.querySelector('[aria-label="Previous page"]');
    const next = footer.querySelector('[aria-label="Next page"]');

    function update(reset = false) {
      if (reset) page = 0;
      const rows = [...table.tBodies[0].rows];
      const query = search?.value.trim().toLocaleLowerCase() || "";
      matched = rows.filter(row => (!query || row.textContent.toLocaleLowerCase().includes(query))
        && filters.every(filter => !chosen.get(filter.label) || filter.match(row, chosen.get(filter.label)))
        && predicate(row));
      page = Math.max(0, Math.min(page, Math.ceil(matched.length / pageSize) - 1));
      const shown = new Set(matched.slice(page * pageSize, (page + 1) * pageSize));
      rows.forEach(row => { row.hidden = !shown.has(row); });
      count.textContent = `${matched.length.toLocaleString()} of ${rows.length.toLocaleString()} ${label}`;
      footer.hidden = matched.length <= pageSize;
      empty.hidden = matched.length !== 0;
      previous.disabled = page === 0;
      next.disabled = (page + 1) * pageSize >= matched.length;
      footer.querySelector(".collection-range").textContent = `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, matched.length)} of ${matched.length}`;
      footer.querySelector(".collection-page").textContent = `${page + 1} / ${Math.max(1, Math.ceil(matched.length / pageSize))}`;
      onUpdate(matched, chosen);
      table.dispatchEvent(new Event("codeview:rows"));
    }
    const reset = document.createElement("button");
    reset.type = "button"; reset.className = "btn btn-quiet"; reset.textContent = "Reset";
    function clear() {
      chosen.clear(); predicate = () => true;
      if (search) { search.value = ""; search.dispatchEvent(new Event("input")); }
      pickers.forEach(control => control.setValue(""));
      update(true);
    }
    reset.onclick = clear;
    empty.querySelector("button").onclick = clear;
    filters.forEach(filter => {
      const control = picker(filter.label, [{ value: "", label: filter.all || "All" }, ...filter.options], "", value => {
        chosen.set(filter.label, value); update(true);
      });
      pickers.push(control); toolbar.insertBefore(control, count);
    });
    if (presets.length) {
      function showColumns(value) {
        const preset = presets.find(item => item.value === value) || presets[0];
        [...table.rows].forEach(row => [...row.cells].forEach((cell, index) => {
          cell.hidden = !preset.columns.includes(index);
        }));
        table.dispatchEvent(new Event("codeview:columns"));
      }
      toolbar.insertBefore(picker("Columns", presets, presets[0].value, showColumns), count);
      showColumns(presets[0].value);
    }
    if (!compact) toolbar.appendChild(reset);
    const exportButton = document.createElement("button");
    exportButton.className = "btn btn-quiet"; exportButton.type = "button"; exportButton.textContent = "Export CSV";
    exportButton.onclick = () => {
      const indices = [...table.tHead.rows[0].cells].flatMap((cell, index) => cell.hidden ? [] : [index]);
      const csvCell = value => `"${String(value).replace(/"/g, '""')}"`;
      const values = row => indices.map(index => csvCell(row.cells[index].dataset.exportValue
        ?? row.cells[index].dataset.sortValue ?? row.cells[index].querySelector(".sort-th-btn > span")?.textContent
        ?? row.cells[index].textContent.trim())).join(",");
      download(`${exportName}.csv`, [values(table.tHead.rows[0]), ...matched.map(values)].join("\r\n"), "text/csv;charset=utf-8");
    };
    toolbar.appendChild(exportButton);
    previous.onclick = () => { page--; update(); };
    next.onclick = () => { page++; update(); };
    search?.addEventListener("input", () => update(true));
    table.addEventListener("codeview:sorted", () => update(true));
    update();
    return { update, reset: clear,
      setFilter(label, value) {
        chosen.set(label, value);
        pickers[filters.findIndex(filter => filter.label === label)]?.setValue(value);
        update(true);
      },
      filter(fn) { predicate = fn; update(true); } };
  }

  function sortable(table) {
    const original = [...table.tBodies[0].rows];
    const priorities = [];
    function apply() {
      const rows = original.slice().sort((a, b) => {
        for (const item of priorities) {
          const one = a.cells[item.index].textContent.trim();
          const two = b.cells[item.index].textContent.trim();
          const result = one.localeCompare(two, undefined, { numeric: true, sensitivity: "base" });
          if (result) return result * item.direction;
        }
        return 0;
      });
      rows.forEach(row => table.tBodies[0].appendChild(row));
      [...table.tHead.rows[0].cells].forEach(cell => {
        const order = priorities.findIndex(item => item.header === cell);
        const item = priorities[order];
        if (order === 0) cell.setAttribute("aria-sort", item.direction === 1 ? "ascending" : "descending");
        else cell.removeAttribute("aria-sort");
        cell.querySelector(".module-sort").innerHTML = item
          ? `${icon(item.direction === 1 ? "up" : "down")} ${order + 1}` : "";
        cell.querySelector(".sr-only").textContent = item
          ? `Sort priority ${order + 1}, ${item.direction === 1 ? "ascending" : "descending"}` : "Unsorted";
      });
      table.dispatchEvent(new Event("codeview:sorted"));
    }
    [...table.tHead.rows[0].cells].forEach((header, index) => {
      const label = header.textContent.trim();
      const button = document.createElement("button");
      button.type = "button"; button.className = "sort-th-btn";
      button.innerHTML = `<span>${escape(label)}</span><span class="module-sort" aria-hidden="true"></span>`;
      const description = document.createElement("span");
      description.className = "sr-only"; description.id = `cv-sort-${++serial}`;
      button.setAttribute("aria-describedby", description.id);
      header.replaceChildren(button, description);
      button.onclick = () => {
        const sort = priorities.find(item => item.index === index);
        if (!sort) priorities.push({ index, direction: 1, header });
        else if (sort.direction === 1) sort.direction = -1;
        else priorities.splice(priorities.indexOf(sort), 1);
        apply();
      };
    });
    table.addEventListener("codeview:columns", () => {
      for (let index = priorities.length - 1; index >= 0; index--) {
        if (priorities[index].header.hidden) priorities.splice(index, 1);
      }
      apply();
    });
  }

  function enhanceModule() {
    if (!location.pathname.startsWith("/m/") || !document.body.hasAttribute("data-codeview-collections")) return;
    document.body.classList.add("module-page");
    const filter = document.querySelector('input[type="search"]');
    const sections = [...document.querySelectorAll("[data-scripts-section], [data-skills-section], [data-statusline-section]")];
    let sectionFilter = "";
    const toolbar = filter?.closest(".scripts-toolbar, .skills-toolbar, .statusline-toolbar");
    const empty = document.createElement("div");
    empty.className = "collection-empty";
    empty.innerHTML = '<strong>No matches.</strong> <button type="button" class="btn btn-quiet">Reset filters</button>';
    toolbar?.after(empty);
    const sectionPicker = picker("Section", [{ value: "", label: "All sections" }, ...sections.map((section, index) =>
      ({ value: String(index), label: section.querySelector("h2").textContent }))], "", value => {
      sectionFilter = value; updateSections();
    });
    toolbar?.appendChild(sectionPicker);
    const tables = [...document.querySelectorAll(".data-table")];
    function columns(mode) {
      document.body.classList.toggle("module-full-fields", mode === "All fields");
      tables.forEach(table => {
        const headers = [...table.tHead.rows[0].cells].map(cell =>
          cell.querySelector(".sort-th-btn > span")?.textContent || cell.textContent.trim());
        const summary = headers.length === 3 ? [0, 2]
          : headers.includes("Latest Quotas / Segments") || headers.includes("Status") ? [0, 3, 4] : [0, 1, headers.length - 1];
        [...table.rows].forEach(row => [...row.cells].forEach((cell, index) => {
          cell.hidden = mode !== "All fields" && !summary.includes(index);
        }));
        table.dispatchEvent(new Event("codeview:columns"));
      });
    }
    toolbar?.appendChild(picker("Columns", ["Summary", "All fields"], "Summary", columns));
    function updateSections() {
      let shown = 0;
      sections.forEach((section, index) => {
        const hit = [...section.querySelectorAll("tbody tr, [data-statusline-item]")]
          .some(item => !item.hidden && item.style.display !== "none");
        section.hidden = !hit || (sectionFilter !== "" && String(index) !== sectionFilter);
        if (!section.hidden) shown++;
      });
      empty.hidden = shown !== 0;
    }
    empty.querySelector("button").onclick = () => {
      sectionFilter = ""; sectionPicker.setValue("");
      if (filter) { filter.value = ""; filter.dispatchEvent(new Event("input")); filter.focus(); }
      updateSections();
    };
    if (filter && !filter.parentElement.querySelector(".input-clear-btn")) {
      const clear = document.createElement("button");
      clear.type = "button"; clear.className = "input-clear-btn"; clear.hidden = true;
      clear.setAttribute("aria-label", "Clear filter"); clear.innerHTML = icon("close");
      filter.parentElement.appendChild(clear);
      filter.addEventListener("input", () => { clear.hidden = !filter.value; });
      clear.onclick = () => { filter.value = ""; filter.dispatchEvent(new Event("input")); filter.focus(); };
    }
    document.querySelectorAll(".data-table").forEach(table => {
      if (!table.tHead || !table.tBodies[0]) return;
      sortable(table);
      [...table.tBodies[0].rows].forEach(row => {
        [...row.cells].forEach((cell, index) => {
          const heading = table.tHead.rows[0].cells[index].querySelector(".sort-th-btn > span")?.textContent;
          if (!["What it does", "Description"].includes(heading)) return;
          const preview = document.createElement("span");
          preview.className = "collection-cell-preview";
          while (cell.firstChild) preview.appendChild(cell.firstChild);
          cell.appendChild(preview);
        });
        const copy = row.cells[0].querySelector("[data-copy]")?.dataset.copy;
        const trigger = document.createElement("button");
        trigger.type = "button"; trigger.className = "table-row-trigger";
        trigger.textContent = row.cells[0].textContent.trim();
        trigger.setAttribute("aria-label", `Inspect ${row.cells[0].textContent.trim()}`);
        row.cells[0].replaceChildren(trigger);
        const open = () => {
          const headings = [...table.tHead.rows[0].cells];
          inspect(row.cells[0].textContent.trim(), `<dl class="inspection-list">${headings.map((heading, index) =>
            `<div><dt>${escape(heading.querySelector(".sort-th-btn > span")?.textContent || heading.textContent)}</dt>
              <dd>${escape(row.cells[index].textContent.trim())}</dd></div>`).join("")}</dl>
            ${row.dataset.tip ? `<p class="inspection-description">${escape(row.dataset.tip)}</p>` : ""}
            ${row.dataset.flags ? `<pre class="inspection-code">${escape(row.dataset.flags)}</pre>` : ""}
            ${copy ? `<button type="button" class="btn btn-secondary" data-copy="${escape(copy)}">Copy ${escape(copy)}</button>` : ""}`, trigger);
        };
        trigger.onclick = open;
        row.addEventListener("click", event => {
          if (!event.target.closest("button, a, input") && !getSelection()?.toString()) open();
        });
        row.classList.add("is-inspectable");
      });
      const controls = collection(table, { search: filter, pageSize: 15, compact: true });
      filter?.addEventListener("input", () => controls.update(true));
      const buttons = () => [...table.querySelectorAll("tbody tr:not([hidden]) .table-row-trigger")];
      function tabStops(active = buttons()[0]) {
        table.querySelectorAll(".table-row-trigger").forEach(button => { button.tabIndex = button === active ? 0 : -1; });
      }
      table.addEventListener("codeview:rows", () => tabStops());
      table.addEventListener("focusin", event => {
        const button = event.target.closest(".table-row-trigger");
        if (button) tabStops(button);
      });
      table.addEventListener("keydown", event => {
        if (!event.target.matches(".table-row-trigger") || event.altKey || event.ctrlKey || event.metaKey
            || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
        const items = buttons();
        const index = Math.max(0, Math.min(items.length - 1, items.indexOf(event.target) + (event.key === "ArrowDown" ? 1 : -1)));
        event.preventDefault(); items[index]?.focus();
      });
      tabStops();
    });
    filter?.addEventListener("input", updateSections);
    columns("Summary");
    updateSections();
    const history = [];
    const tray = document.createElement("div");
    tray.className = "toast-container"; tray.setAttribute("aria-live", "polite");
    document.body.appendChild(tray);
    function notify(message, danger) {
      const record = { message, danger, created: new Date() };
      history.unshift(record);
      const toast = document.createElement("div");
      toast.className = "toast"; toast.dataset.tone = danger ? "danger" : "good";
      toast.innerHTML = `<button type="button" class="toast-main"><span class="toast-msg">${escape(message)}</span></button>`;
      tray.appendChild(toast); tray.classList.add("has-history"); tray.classList.remove("is-resting");
      toast.querySelector("button").onclick = () => inspect("Notification history", `<ul class="toast-history-list">${history.map(item =>
        `<li class="toast-history-item" data-tone="${item.danger ? "danger" : "good"}"><div class="toast-history-copy">
          <span class="toast-history-message">${escape(item.message)}</span><time class="toast-history-time"
          datetime="${item.created.toISOString()}">${escape(item.created.toLocaleString())}</time></div></li>`).join("")}</ul>`);
      let remaining = 5000, started, timer;
      function resume() {
        if (timer || toast.classList.contains("is-archived")) return;
        started = performance.now();
        timer = setTimeout(() => {
          timer = null; toast.classList.add("is-archived");
          if (!tray.querySelector(".toast:not(.is-archived)")) tray.classList.add("is-resting");
        }, remaining);
      }
      function pause() { if (timer) { clearTimeout(timer); timer = null; remaining -= performance.now() - started; } }
      toast.onmouseenter = pause; toast.onmouseleave = resume;
      toast.onfocusin = pause; toast.onfocusout = resume; resume();
    }
    document.addEventListener("click", async event => {
      const button = event.target.closest("[data-copy]");
      if (!button) return;
      event.stopImmediatePropagation();
      let copied = false;
      try { await navigator.clipboard.writeText(button.dataset.copy); copied = true; } catch { /* opaque frame fallback */ }
      if (!copied) {
        const area = document.createElement("textarea");
        area.value = button.dataset.copy; area.style.cssText = "position:fixed;opacity:0";
        (button.closest("dialog") || document.body).appendChild(area);
        area.select();
        try { copied = document.execCommand("copy"); } catch { copied = false; }
        finally { area.remove(); button.focus({ preventScroll: true }); }
      }
      if (copied) { button.classList.add("is-copied"); setTimeout(() => button.classList.remove("is-copied"), 1600); }
      notify(copied ? "Copied to clipboard" : "Could not copy to clipboard", !copied);
      const dialog = button.closest("dialog");
      if (dialog) {
        let status = dialog.querySelector(".copy-status");
        if (!status) { status = document.createElement("p"); status.className = "stats-note copy-status"; status.setAttribute("role", "status"); dialog.appendChild(status); }
        status.textContent = copied ? "Copied to clipboard" : "Could not copy to clipboard";
      }
    }, true);
  }

  document.addEventListener("DOMContentLoaded", enhanceModule);
  document.addEventListener("DOMContentLoaded", () => {
    const tip = document.createElement("div");
    tip.id = `cv-tooltip-${++serial}`; tip.className = "tooltip-bubble"; tip.hidden = true; tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    let owner = null, timer;
    function hide() {
      clearTimeout(timer); tip.hidden = true;
      owner?.removeAttribute("aria-describedby"); owner = null;
    }
    function place() {
      if (!owner) return;
      const bounds = owner.getBoundingClientRect();
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
      const width = viewport?.width || innerWidth;
      tip.style.maxWidth = `${Math.min(360, width - 24)}px`;
      tip.style.left = `${Math.max(left + 12, Math.min(bounds.left, left + width - tip.offsetWidth - 12))}px`;
      tip.style.top = `${bounds.top - tip.offsetHeight - 8 >= top + 12 ? bounds.top - tip.offsetHeight - 8 : bounds.bottom + 8}px`;
    }
    function show(target) {
      const value = target.closest(".record-copy, .rank-label, .tree-name, .collection-cell-preview")
        || target.closest("button")?.querySelector(".record-copy, .rank-label")
        || target.closest(".table-row-trigger")?.closest("tr").querySelector(".collection-cell-preview");
      if (!value || value.scrollWidth <= value.clientWidth || value.closest("dialog")) return;
      if (value.closest("tr[data-tip], tr[data-flags]")) return;
      const trigger = value.closest("button, [role='treeitem']") || value.closest("tr")?.querySelector(".table-row-trigger") || value;
      if (owner === trigger || trigger.hasAttribute("aria-describedby")) return;
      hide(); owner = trigger; tip.textContent = value.textContent;
      value.removeAttribute("title");
      timer = setTimeout(() => { tip.hidden = false; owner.setAttribute("aria-describedby", tip.id); place(); }, 150);
    }
    document.addEventListener("pointerover", event => show(event.target));
    document.addEventListener("focusin", event => show(event.target));
    document.addEventListener("pointerout", event => { if (owner && !owner.contains(event.relatedTarget)) hide(); });
    document.addEventListener("focusout", hide);
    document.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
    document.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    window.visualViewport?.addEventListener("resize", place);
    window.visualViewport?.addEventListener("scroll", place);
  });
  return { escape, icon, picker, collection, sortable, inspect, download };
})();
