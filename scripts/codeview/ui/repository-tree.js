/* Repository file tree. The host supplies the existing indexed-file dataset. */
"use strict";

window.CodeviewTree = function (panel, helpers) {
  const { esc, fmt, iconUse, searchField, bindSearch, openFile } = helpers;
  let nodes = new Map();
  let roots = [];
  let expanded = new Set();
  let filteredExpanded = new Set();
  let included = null;
  let query = "";
  let currentPath = null;
  let signature = null;
  let fileCount = 0;
  let matchingFiles = 0;
  let typeahead = "";
  let lastTyped = 0;

  panel.innerHTML = `<div class="panel-stack">
    <div class="tree-toolbar">
      ${searchField("tree-filter", "Filter paths")}
      <div class="tree-actions">
        <button type="button" class="btn btn-secondary" data-tree-action="expand">Expand all</button>
        <button type="button" class="btn btn-secondary" data-tree-action="collapse">Collapse all</button>
      </div>
    </div>
    <div class="section-heading">
      <h2>Repository tree</h2>
      <span class="section-note" id="tree-summary" aria-live="polite"></span>
    </div>
    <ul class="repository-tree" role="tree" aria-label="Repository files" aria-describedby="tree-summary"></ul>
    <div class="callout callout-info" id="tree-empty" hidden></div>
  </div>`;
  const tree = panel.querySelector('[role="tree"]');
  const input = panel.querySelector("#tree-filter");
  const empty = panel.querySelector("#tree-empty");
  const activeExpansion = () => query ? filteredExpanded : expanded;
  const items = () => [...tree.querySelectorAll('[role="treeitem"]')];

  function filter() {
    included = query ? new Set() : null;
    filteredExpanded = new Set();
    matchingFiles = 0;
    for (const node of nodes.values()) {
      if (node.children || (query && !node.path.toLowerCase().includes(query))) continue;
      matchingFiles++;
      if (!included) continue;
      included.add(node.path);
      let parent = node.parent;
      while (parent) {
        included.add(parent.path);
        filteredExpanded.add(parent.path);
        parent = parent.parent;
      }
    }
  }

  function render(focus = tree.contains(document.activeElement)) {
    const visiblePaths = [];
    const open = activeExpansion();
    function branch(children, depth) {
      const visible = included ? children.filter(node => included.has(node.path)) : children;
      return visible.map((node, i) => {
        visiblePaths.push(node.path);
        const folder = Boolean(node.children);
        const isOpen = folder && open.has(node.path);
        const meta = folder ? `${fmt(node.count)} files`
          : node.file.lines != null ? `${fmt(node.file.lines)} lines`
          : node.file.binary ? "Binary" : "Not counted";
        return `<li role="treeitem" tabindex="-1" data-path="${esc(node.path)}"
            aria-label="${esc(node.path)}" aria-level="${depth + 1}"
            aria-posinset="${i + 1}" aria-setsize="${visible.length}"
            ${folder ? `aria-expanded="${isOpen}"` : 'aria-haspopup="dialog"'}>
          <div class="tree-row" style="--tree-depth:${Math.min(depth, 8)}">
            ${iconUse("chevron-right", `icon tree-chevron${folder ? "" : " is-placeholder"}`)}
            ${iconUse(folder ? "folder" : "file", "icon tree-kind")}
            <span class="tree-name" title="${esc(node.path)}">${esc(node.name)}</span>
            <span class="tree-language" aria-hidden="true">${folder ? "" : esc(node.file.lang || "")}</span>
            <span class="tree-meta" aria-hidden="true">${meta}</span>
          </div>
          ${isOpen ? `<ul role="group">${branch(node.children, depth + 1)}</ul>` : ""}
        </li>`;
      }).join("");
    }
    tree.innerHTML = branch(roots, 0);
    const visibleSet = new Set(visiblePaths);
    while (currentPath && !visibleSet.has(currentPath)) {
      currentPath = nodes.get(currentPath)?.parent?.path || null;
    }
    if (!currentPath) currentPath = visiblePaths[0] || null;
    const current = items().find(item => item.dataset.path === currentPath);
    if (current) {
      current.tabIndex = 0;
      if (focus) current.focus({ preventScroll: true });
    }
    tree.hidden = !visiblePaths.length;
    empty.hidden = Boolean(visiblePaths.length);
    if (!visiblePaths.length) {
      empty.innerHTML = query
        ? '<strong>No matches:</strong> no indexed files match this path. <button type="button" class="btn btn-secondary" data-tree-action="clear">Clear filter</button>'
        : "<strong>No indexed files:</strong> this repository has no files to browse.";
    }
    panel.querySelector("#tree-summary").textContent = query
      ? `${fmt(matchingFiles)} of ${fmt(fileCount)} files`
      : `${fmt(fileCount)} files · ${fmt(nodes.size - fileCount)} folders`;
    const folders = [...nodes.values()].filter(node => node.children && (!included || included.has(node.path)));
    panel.querySelector('[data-tree-action="expand"]').disabled = !folders.some(node => !open.has(node.path));
    panel.querySelector('[data-tree-action="collapse"]').disabled = !folders.some(node => open.has(node.path));
  }

  function focusItem(item) {
    if (!item) return;
    items().forEach(row => { row.tabIndex = row === item ? 0 : -1; });
    currentPath = item.dataset.path;
    item.focus({ preventScroll: true });
    item.querySelector(".tree-row").scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function activate(item) {
    const node = nodes.get(item.dataset.path);
    currentPath = node.path;
    if (node.children) {
      const open = activeExpansion();
      if (open.has(node.path)) open.delete(node.path);
      else open.add(node.path);
      render(true);
    } else {
      focusItem(item);
      openFile(node.path, item).catch(console.error);
    }
  }

  tree.addEventListener("click", event => {
    const row = event.target.closest(".tree-row");
    if (row) activate(row.parentElement);
  });
  tree.addEventListener("focusin", event => {
    const item = event.target.closest('[role="treeitem"]');
    if (!item) return;
    currentPath = item.dataset.path;
    items().forEach(row => { row.tabIndex = row === item ? 0 : -1; });
  });
  tree.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const item = event.target.closest('[role="treeitem"]');
    if (!item) return;
    const node = nodes.get(item.dataset.path);
    const visible = items();
    const index = visible.indexOf(item);
    let next;
    if (event.key === "ArrowDown") next = visible[Math.min(index + 1, visible.length - 1)];
    else if (event.key === "ArrowUp") next = visible[Math.max(index - 1, 0)];
    else if (event.key === "Home") next = visible[0];
    else if (event.key === "End") next = visible.at(-1);
    else if (event.key === "ArrowRight") {
      if (node.children && !activeExpansion().has(node.path)) activate(item);
      else if (node.children) next = visible[index + 1];
    } else if (event.key === "ArrowLeft") {
      if (node.children && activeExpansion().has(node.path)) activate(item);
      else next = visible.find(row => row.dataset.path === node.parent?.path);
    } else if (event.key === "Enter" || event.key === " ") activate(item);
    else if (event.key.length === 1) {
      const now = performance.now();
      typeahead = now - lastTyped > 700 ? event.key.toLowerCase() : typeahead + event.key.toLowerCase();
      lastTyped = now;
      const prefix = [...typeahead].every(char => char === typeahead[0]) ? typeahead[0] : typeahead;
      next = [...visible.slice(index + 1), ...visible.slice(0, index + 1)]
        .find(row => nodes.get(row.dataset.path).name.toLowerCase().startsWith(prefix));
    } else return;
    event.preventDefault();
    focusItem(next);
  });

  bindSearch(input, value => {
    query = value.trim().toLowerCase();
    filter();
    render();
  });
  panel.addEventListener("click", event => {
    const action = event.target.closest("[data-tree-action]")?.dataset.treeAction;
    if (!action) return;
    if (action === "clear") {
      input.value = "";
      input.dispatchEvent(new Event("input"));
      input.focus();
      return;
    }
    const open = activeExpansion();
    for (const node of nodes.values()) {
      if (!node.children || (included && !included.has(node.path))) continue;
      if (action === "expand") open.add(node.path);
      else open.delete(node.path);
    }
    render(false);
  });

  return {
    update(files) {
      const nextSignature = JSON.stringify(files.map(file =>
        [file.path, file.lines, file.lang, file.binary]));
      if (signature === nextSignature) return;
      signature = nextSignature;
      nodes = new Map();
      roots = [];
      fileCount = files.length;
      for (const file of files) {
        const parts = file.path.split("/");
        let children = roots;
        let parent = null;
        let path = "";
        parts.forEach((name, index) => {
          path = path ? `${path}/${name}` : name;
          let node = nodes.get(path);
          if (!node) {
            node = { name, path, parent, count: 0 };
            if (index < parts.length - 1) node.children = [];
            else node.file = file;
            nodes.set(path, node);
            children.push(node);
          }
          node.count++;
          parent = node;
          children = node.children;
        });
      }
      const compare = (a, b) => Boolean(b.children) - Boolean(a.children)
        || a.name.localeCompare(b.name, "en", { numeric: true, sensitivity: "base" });
      roots.sort(compare);
      for (const node of nodes.values()) if (node.children) node.children.sort(compare);
      expanded = new Set([...expanded].filter(path => nodes.get(path)?.children));
      filter();
      render();
    },
  };
};
