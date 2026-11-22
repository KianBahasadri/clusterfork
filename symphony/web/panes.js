const STATUS_GROUPS = ["Todo", "In Progress", "Done"];

function pollDelay(state, hidden) {
  if (state.messages.length === 200) return 0;
  if (state.busy) return 600;
  return hidden ? 2000 : 1000;
}

function issueOrder(a, b) {
  return (a.priority || 5) - (b.priority || 5)
    || a.identifier.localeCompare(b.identifier, undefined, {numeric: true});
}

function completedOrder(a, b) {
  return (Date.parse(b.completedAt) || 0) - (Date.parse(a.completedAt) || 0)
    || a.identifier.localeCompare(b.identifier, undefined, {numeric: true});
}

function selectableProject(projects, selected) {
  return projects.some((project) => project.id === selected) ? selected : (projects[0]?.id || "");
}

function partitionIssues(rows) {
  const backlog = rows.filter((issue) => issue.state.name === "Backlog").toSorted(issueOrder);
  const groups = STATUS_GROUPS.map((name) => ({
    name,
    issues: rows.filter((issue) => issue.state.name === name)
      .toSorted(name === "Done" ? completedOrder : issueOrder),
  }));
  return {backlog, groups};
}

function formatDuration(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "Unavailable";
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return seconds + "s";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + "m";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h " + (minutes % 60) + "m";
  return Math.floor(hours / 24) + "d " + (hours % 24) + "h";
}

function formatSeconds(seconds) {
  return Number.isFinite(seconds) ? formatDuration(seconds * 1000) : "Unavailable";
}

function stageDuration(startedAt, now = Date.now()) {
  const started = Date.parse(startedAt);
  return Number.isFinite(started) ? formatDuration(Math.max(0, now - started)) : "Unavailable";
}

function updateStagePill(pill, now = Date.now()) {
  const duration = stageDuration(pill.dataset.stageStart, now);
  const coverage = pill.dataset.stageCoverage || "complete";
  pill.lastChild.textContent = duration;
  pill.title = (pill.dataset.stageName || "Current stage") + " duration: " + duration
    + (coverage === "complete" ? "" : " · " + (pill.dataset.stageReason || coverage + " history"));
}

function nextIssueTab(tabs, current, key) {
  const index = tabs.indexOf(current);
  if (index < 0) return "";
  if (key === "Home") return tabs[0];
  if (key === "End") return tabs[tabs.length - 1];
  if (key === "ArrowLeft") return tabs[(index - 1 + tabs.length) % tabs.length];
  if (key === "ArrowRight") return tabs[(index + 1) % tabs.length];
  return "";
}

function executionNeedsLoad(tab, issue) {
  return tab === "execution" && Boolean(issue);
}

function executionShouldRefresh(open, tab, issue) {
  return Boolean(open && tab === "execution" && issue);
}

function executionRequestCurrent(request, expected, tab, open, identifier, currentIdentifier) {
  return request === expected && tab === "execution" && Boolean(open) && identifier === currentIdentifier;
}

function childPath(root, node) {
  const path = [];
  while (node && node !== root) {
    if (!node.parentNode) return null;
    path.unshift(Array.prototype.indexOf.call(node.parentNode.childNodes, node));
    node = node.parentNode;
  }
  return node === root ? path : null;
}

function childAtPath(root, path) {
  let node = root;
  for (const index of path || []) {
    node = node?.childNodes?.[index];
    if (!node) return null;
  }
  return node;
}

function executionViewKey(node, index, prefix) {
  return node?.dataset?.viewKey || prefix + ":" + index;
}

function executionViewAnchor(root, node) {
  const elementNode = node?.nodeType === 3 ? node.parentElement : node;
  const keyed = elementNode?.closest?.("[data-view-key]");
  if (!keyed || keyed === root || !root.contains(keyed)) return null;
  return {key: keyed.dataset.viewKey, path: childPath(keyed, node)};
}

function executionKeyedNode(root, key) {
  if (!key) return null;
  return [...root.querySelectorAll("[data-view-key]")]
    .find((item) => item.dataset.viewKey === key) || null;
}

function executionNodeAt(root, path, anchor) {
  if (anchor?.key) {
    if (!Array.isArray(anchor.path)) return null;
    return childAtPath(executionKeyedNode(root, anchor.key), anchor.path);
  }
  return childAtPath(root, path);
}

function captureExecutionView(panel, scrollContainer, selection = null, activeElement = null) {
  const state = {
    details: {},
    logs: {},
    scrollTop: scrollContainer?.scrollTop || 0,
    focus: childPath(panel, activeElement),
    focusAnchor: executionViewAnchor(panel, activeElement),
    selection: null,
  };
  [...panel.querySelectorAll("details")].forEach((item, index) => {
    state.details[executionViewKey(item, index, "details")] = item.open;
  });
  [...panel.querySelectorAll(".execution-log")].forEach((item, index) => {
    state.logs[executionViewKey(item, index, "log")] = item.scrollTop;
  });
  if (selection?.rangeCount && panel.contains(selection.anchorNode) && panel.contains(selection.focusNode)) {
    const range = selection.getRangeAt(0);
    state.selection = {
      anchor: childPath(panel, range.startContainer),
      anchorOffset: range.startOffset,
      anchorAnchor: executionViewAnchor(panel, range.startContainer),
      focus: childPath(panel, range.endContainer),
      focusOffset: range.endOffset,
      focusAnchor: executionViewAnchor(panel, range.endContainer),
    };
  }
  return state;
}

function restoreExecutionView(panel, state, scrollContainer, selection = null) {
  if (!state) return;
  [...panel.querySelectorAll("details")].forEach((item, index) => {
    const key = executionViewKey(item, index, "details");
    if (state.details && Object.hasOwn(state.details, key)) item.open = state.details[key];
  });
  [...panel.querySelectorAll(".execution-log")].forEach((item, index) => {
    const key = executionViewKey(item, index, "log");
    if (state.logs && Object.hasOwn(state.logs, key)) item.scrollTop = state.logs[key];
  });
  if (scrollContainer) scrollContainer.scrollTop = state.scrollTop;
  const focus = executionNodeAt(panel, state.focus, state.focusAnchor);
  if (focus?.focus) focus.focus({preventScroll: true});
  if (state.selection && selection) {
    try {
      const anchor = executionNodeAt(panel, state.selection.anchor, state.selection.anchorAnchor);
      const end = executionNodeAt(panel, state.selection.focus, state.selection.focusAnchor);
      if (anchor && end) {
        const range = document.createRange();
        range.setStart(anchor, state.selection.anchorOffset);
        range.setEnd(end, state.selection.focusOffset);
        selection.removeAllRanges();
        selection.addRange(range);
      }
    } catch {
      // The refreshed content may no longer contain the old selection offsets.
    }
  }
}

function executionAttemptTotal(worker, unavailable) {
  if (unavailable) {
    return {value: "Unavailable", detail: "Execution records are unavailable."};
  }
  if (worker?.coverage === "recorded") {
    return {value: String(worker.attemptCount || 0), detail: "Retained attempt records"};
  }
  if (worker?.coverage === "not_started") {
    return {value: "0", detail: "No worker attempts have started."};
  }
  return {value: "Not recorded", detail: "No worker attempt records were captured."};
}

function executionFindingTotal(findings) {
  if (!findings?.available) {
    return {value: "Unavailable", detail: "No attempt-linked reviewer findings were recorded."};
  }
  const count = Number.isInteger(findings.count) ? findings.count : 0;
  const reviews = Number.isInteger(findings.reviewCount) ? findings.reviewCount : 0;
  const detail = "Across " + reviews + " recorded review pass" + (reviews === 1 ? "" : "es");
  if (findings.complete) return {value: String(count), detail};
  return {
    value: count > 0 ? "At least " + count : "Partial",
    detail: "Partial; some attempts or finding counts are unavailable.",
  };
}
