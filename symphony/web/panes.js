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

function captureExecutionView(panel, scrollContainer, selection = null, activeElement = null) {
  const state = {
    details: [...panel.querySelectorAll("details")].map((item) => item.open),
    logs: [...panel.querySelectorAll(".execution-log")].map((item) => item.scrollTop),
    scrollTop: scrollContainer?.scrollTop || 0,
    focus: childPath(panel, activeElement),
    selection: null,
  };
  if (selection?.rangeCount && panel.contains(selection.anchorNode) && panel.contains(selection.focusNode)) {
    const range = selection.getRangeAt(0);
    state.selection = {
      anchor: childPath(panel, range.startContainer),
      anchorOffset: range.startOffset,
      focus: childPath(panel, range.endContainer),
      focusOffset: range.endOffset,
    };
  }
  return state;
}

function restoreExecutionView(panel, state, scrollContainer, selection = null) {
  if (!state) return;
  [...panel.querySelectorAll("details")].forEach((item, index) => {
    if (index < state.details.length) item.open = state.details[index];
  });
  [...panel.querySelectorAll(".execution-log")].forEach((item, index) => {
    if (index < state.logs.length) item.scrollTop = state.logs[index];
  });
  if (scrollContainer) scrollContainer.scrollTop = state.scrollTop;
  const focus = childAtPath(panel, state.focus);
  if (focus?.focus) focus.focus({preventScroll: true});
  if (state.selection && selection) {
    try {
      const anchor = childAtPath(panel, state.selection.anchor);
      const end = childAtPath(panel, state.selection.focus);
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
