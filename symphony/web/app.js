const byId = (id) => document.getElementById(id);
const repos = byId("repos");
const backlog = byId("backlog");
const statusIssues = byId("status-issues");
const backlogPane = backlog.closest("aside");
const statusPane = statusIssues.closest("aside");
const main = document.querySelector("main");
const backlogToggle = byId("backlog-toggle");
const queueToggle = byId("queue-toggle");
const backlogScroll = new Map();
const statusScroll = new Map();
let backlogCollapsed = false;
let queueCollapsed = false;
const messages = byId("messages");
const chatLog = byId("chat-log");
const composer = byId("composer");
const messageField = byId("message");
const attachmentList = byId("attachments");
const commandList = byId("command-list");
const interrupt = byId("interrupt");
const pendingImages = [];
const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
const imageCount = 4;
const imageBytes = 5 * 1024 * 1024;
const imagesBytes = 10 * 1024 * 1024;
const expandedPlans = new Set();
const collapsedGroups = new Set();
const pendingPlans = new Set();
const priorityIcons = ["ellipsis", "octagon-alert", "signal", "signal-medium", "signal-low"];
let issueCache = new Map();
let issueRevision = "";
let selected = "";
let lastMessage = 0;
let tasks = [];
let projectsKey = "";
let issueDay = new Date().toDateString();
let stream = "";
let renderedStream = "";
let pendingStream = null;
let pendingMessages = [];
let pendingMessageIds = new Set();
let shownMessageIds = new Set();
let chatFrame = null;
let pollTimer = null;
let polling = false;
let pollAgain = false;
let pollStarted = false;
let following = true;
let codex = {};
let canInterrupt = false;
let updateState = {updateAvailable: false, draining: false};
let updatePending = false;
let updateError = "";

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function icon(name, namespace = "lucide") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  svg.setAttribute("class", "icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  use.setAttribute("href", "#" + namespace + "-" + name);
  svg.append(use);
  return svg;
}

function stateIcon(state) {
  const svg = icon(state.type, "workflow");
  svg.classList.add("status-icon");
  svg.style.setProperty("--status-color", state.color);
  return svg;
}

function issuePill(text, description, iconName) {
  const pill = element("span", "", "issue-pill");
  pill.title = description;
  if (iconName) pill.append(icon(iconName));
  pill.append(element("span", text || description, text ? "" : "sr-only"));
  return pill;
}

function projectLabel(project) {
  const label = element("span", "", "project-label");
  const glyph = project.icon ? element("span", project.icon, "project-emoji") : icon("folder");
  glyph.setAttribute("aria-hidden", "true");
  label.append(glyph, element("span", project.name, "project-name"));
  return label;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

function issueDeadline(value, today) {
  if (!value) return null;
  const date = new Date(value + "T00:00:00");
  if (!Number.isFinite(date.getTime())) return null;
  const days = Math.round((date - today) / 86400000);
  const formatted = date.toLocaleDateString([], {month: "short", day: "numeric",
    year: date.getFullYear() === today.getFullYear() ? undefined : "numeric"});
  const label = days < 0 ? "Overdue · " + formatted : days === 0 ? "Today" : days === 1 ? "Tomorrow" : formatted;
  const time = element("time", "", "issue-pill issue-deadline");
  time.append(icon("calendar-days"), element("span", label));
  time.dateTime = value;
  time.title = "Due " + formatted;
  time.dataset.tone = days < 0 ? "danger" : days <= 1 ? "caution" : "";
  return time;
}

function issueStagePill(issue) {
  const stage = issue.stage || {};
  const pill = issuePill("Unavailable", stage.reason || "Stage duration is unavailable.", "clock");
  if (!stage.startedAt) {
    pill.dataset.tone = "caution";
    return pill;
  }
  pill.dataset.stageStart = stage.startedAt;
  pill.dataset.stageName = stage.name || issue.state.name;
  pill.dataset.stageCoverage = stage.coverage || "complete";
  pill.dataset.stageReason = stage.reason || "";
  updateStagePill(pill);
  return pill;
}

function renderIssue(issue, today) {
  const row = element("article", "", "issue");
  const preview = element("button", "", "issue-preview");
  preview.type = "button";
  preview.id = "issue-open-" + issue.identifier;
  preview.setAttribute("aria-haspopup", "dialog");
  preview.setAttribute("aria-controls", "issue-dialog");
  preview.addEventListener("click", () => openIssue(issue.identifier));
  const link = element("span", issue.identifier, "issue-link");
  link.id = "issue-link-" + issue.identifier;
  const deadline = issueDeadline(issue.dueDate, today);

  const task = tasks.find((item) => item.issue === issue.identifier);
  const stateName = (task?.phase || issue.state.name).replaceAll("_", " ");
  const blocked = stateName.toLowerCase() === "blocked" || task?.phase === "finish_failed";
  const state = element("span", "", "issue-state");
  state.title = stateName;
  state.setAttribute("role", "img");
  state.setAttribute("aria-label", stateName);
  state.append(blocked ? icon("octagon-alert") : stateIcon(issue.state));
  if (blocked) state.dataset.tone = "danger";
  const heading = element("span", "", "issue-heading");
  heading.append(state, element("span", issue.title, "issue-title"));

  const meta = element("span", "", "issue-meta");
  const priority = issuePill("", issue.priorityLabel, priorityIcons[issue.priority]);
  if (issue.priority === 1) priority.dataset.tone = "danger";
  meta.append(priority, issueStagePill(issue));
  if (deadline) meta.append(deadline);
  if (!task && issue.state.name === "Todo") {
    const awaiting = issuePill("Awaiting approval",
      "Approval must be recorded in coordinator chat before this issue can start.", "octagon-alert");
    awaiting.dataset.tone = "caution";
    meta.append(awaiting);
  }
  if (issue.estimate != null) meta.append(issuePill(String(issue.estimate), "Estimate " + issue.estimate, "triangle"));
  for (const label of issue.labels) {
    const pill = issuePill(label.name, label.name);
    const dot = element("span", "", "label-dot");
    dot.style.backgroundColor = label.color;
    pill.prepend(dot);
    meta.append(pill);
  }
  if (task) {
    const phase = issuePill(stateName, "Worker " + stateName);
    if (blocked) phase.dataset.tone = "danger";
    meta.append(phase);
  }
  preview.append(link, heading, meta);
  const cardRow = element("div", "", "issue-card-row");
  cardRow.append(preview);
  if (issue.state.name === "Backlog" && !task) {
    const propose = element("button", "", "issue-plan-action");
    propose.type = "button";
    propose.id = "issue-propose-" + issue.identifier;
    const label = "Propose a plan for " + issue.identifier;
    propose.title = label;
    propose.setAttribute("aria-label", label);
    propose.disabled = pendingPlans.has(issue.identifier);
    propose.append(icon("arrow-right"));
    propose.addEventListener("click", () => proposePlan(issue, propose));
    cardRow.append(propose);
  }
  row.append(cardRow);
  if (task?.detail) row.append(element("p", task.detail, "detail"));
  if (issue.plan) {
    const plan = element("details");
    const summary = element("summary", "Proposed plan");
    summary.id = "plan-summary-" + issue.identifier;
    plan.dataset.issue = issue.identifier;
    plan.open = expandedPlans.has(issue.identifier);
    const body = element("div", "", "plan issue-markdown");
    body.innerHTML = issue.planHtml;
    plan.append(summary, body);
    if (!task) {
      plan.append(element("p", issue.state.name === "Todo"
        ? "This issue is awaiting plan approval in chat and will not run."
        : "Tell the coordinator in chat when you approve this plan.", "muted"));
    }
    row.append(plan);
  }
  return row;
}

async function proposePlan(issue, button) {
  if (pendingPlans.has(issue.identifier)) return;
  pendingPlans.add(issue.identifier);
  button.disabled = true;
  const project = issue.projectId || selected;
  try {
    await submit("Propose a plan for " + issue.identifier + ".", [], project);
  } finally {
    pendingPlans.delete(issue.identifier);
    const current = byId("issue-propose-" + issue.identifier);
    if (current) current.disabled = false;
  }
}

function toggleIssueGroup(event) {
  event.preventDefault();
  const summary = event.currentTarget;
  const group = summary.parentElement;
  const start = group.getBoundingClientRect().height;
  for (const animation of group.getAnimations()) animation.cancel();
  const opening = collapsedGroups.has(group.dataset.group);
  if (opening) collapsedGroups.delete(group.dataset.group);
  else collapsedGroups.add(group.dataset.group);
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    group.open = opening;
    group.style.overflow = "";
    return;
  }
  group.open = true;
  const end = opening ? group.getBoundingClientRect().height : summary.getBoundingClientRect().height;
  group.style.overflow = "clip";
  const animation = group.animate([
    {height: start + "px", transform: "translateY(0)"},
    {height: (opening ? end + 16 : end) + "px", transform: "translateY(4px)", offset: .55},
    {height: (opening ? end - 6 : end + 12) + "px", transform: "translateY(-2px)", offset: .78},
    {height: end + "px", transform: "translateY(0)"},
  ], {duration: 480, easing: "ease-out"});
  animation.onfinish = () => {
    group.open = opening;
    group.style.overflow = "";
  };
}

function renderIssueGroup(name, rows, today) {
  const group = element("details", "", "issue-group");
  group.dataset.group = JSON.stringify([selected, name]);
  group.open = !collapsedGroups.has(group.dataset.group);
  const summary = element("summary");
  summary.id = "status-summary-" + encodeURIComponent(name);
  summary.addEventListener("click", toggleIssueGroup);
  const heading = element("span", "", "issue-group-heading");
  if (rows.length) heading.append(stateIcon(rows[0].state));
  heading.append(element("span", name, "issue-group-name"),
                 element("span", String(rows.length), "issue-count"));
  summary.append(heading);
  const list = element("div", "", "issue-group-list");
  if (rows.length) list.append(...rows.map((issue) => renderIssue(issue, today)));
  else list.append(element("p", "No " + name + " issues.", "muted"));
  group.append(summary, list);
  return group;
}

function capturePaneScroll(project = selected) {
  if (!backlogCollapsed) backlogScroll.set(project, backlog.scrollTop);
  if (!queueCollapsed) statusScroll.set(project, statusIssues.scrollTop);
}

function restorePaneScroll(project = selected) {
  if (!backlogCollapsed) backlog.scrollTop = backlogScroll.get(project) || 0;
  if (!queueCollapsed) statusIssues.scrollTop = statusScroll.get(project) || 0;
}

function applyPaneState() {
  main.toggleAttribute("data-backlog-collapsed", backlogCollapsed);
  main.toggleAttribute("data-queue-collapsed", queueCollapsed);
  backlog.hidden = backlogCollapsed;
  statusIssues.hidden = queueCollapsed;
  for (const [button, collapsed, label] of [
    [backlogToggle, backlogCollapsed, "Backlog"],
    [queueToggle, queueCollapsed, "Queue"],
  ]) {
    button.setAttribute("aria-expanded", String(!collapsed));
    button.title = (collapsed ? "Expand " : "Collapse ") + label + " panel";
    button.setAttribute("aria-label", button.title);
  }
}

function setPaneCollapsed(kind, collapsed) {
  if (kind === "backlog") {
    if (collapsed && !backlogCollapsed) backlogScroll.set(selected, backlog.scrollTop);
    backlogCollapsed = collapsed;
    applyPaneState();
    if (!collapsed) backlog.scrollTop = backlogScroll.get(selected) || 0;
    return;
  }
  if (collapsed && !queueCollapsed) statusScroll.set(selected, statusIssues.scrollTop);
  queueCollapsed = collapsed;
  applyPaneState();
  if (!collapsed) statusIssues.scrollTop = statusScroll.get(selected) || 0;
}

function renderPanes() {
  for (const plan of document.querySelectorAll("details[data-issue]")) {
    if (plan.open) expandedPlans.add(plan.dataset.issue);
    else expandedPlans.delete(plan.dataset.issue);
  }
  const focused = [backlogPane, statusPane].some((pane) => pane.contains(document.activeElement))
    ? document.activeElement.id : "";
  const rows = issueCache.get(selected) || [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const {backlog: backlogRows, groups} = partitionIssues(rows);
  backlog.replaceChildren(...(backlogRows.length
    ? backlogRows.map((issue) => renderIssue(issue, today))
    : [element("p", selected ? "No Backlog issues for this project." : "No Linear project selected.", "muted")]));
  statusIssues.replaceChildren(...groups.map((group) => renderIssueGroup(group.name, group.issues, today)));
  if (focused) byId(focused)?.focus({preventScroll: true});
}

function updateProjects(projects, redraw) {
  const previous = issueCache.get(selected);
  issueCache = new Map(projects.map((project) => [project.id, project.issues]));
  issueCache.set("", projects.flatMap((project) => project.issues));
  preloadIssueDetails(issueCache.get(""));
  const key = JSON.stringify(projects.map((project) => [project.id, project.repo, project.name, project.icon]));
  if (key === projectsKey) {
    if (redraw || JSON.stringify(previous) !== JSON.stringify(issueCache.get(selected))) renderPanes();
    return;
  }
  projectsKey = key;
  const focused = repos.contains(document.activeElement);
  const choices = projects.map((project, index) => {
    const tab = element("button", "", "project-chip");
    tab.append(projectLabel(project));
    tab.title = project.name;
    tab.setAttribute("aria-label", project.name);
    tab.id = "project-tab-" + index;
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", "backlog status-issues");
    tab.dataset.project = project.id;
    tab.addEventListener("click", () => selectProject(project.id));
    return tab;
  });
  repos.replaceChildren(...choices);
  selectProject(selectableProject(projects, selected));
  if (focused) repos.querySelector('[aria-selected="true"]')?.focus({preventScroll: true});
}

function selectProject(project) {
  capturePaneScroll();
  selected = project;
  for (const tab of repos.children) {
    const active = tab.dataset.project === project;
    tab.setAttribute("aria-selected", active);
    tab.tabIndex = active ? 0 : -1;
    if (active) {
      backlog.setAttribute("aria-labelledby", tab.id);
      statusIssues.setAttribute("aria-labelledby", tab.id);
    }
  }
  renderPanes();
  restorePaneScroll(project);
}

function imageUrl(name) {
  return "/api/chat-image?name=" + encodeURIComponent(name);
}

function imageGallery(images, className) {
  const gallery = element("div", "", className);
  images.forEach((image, index) => {
    const link = element("a", "", "message-image");
    link.href = imageUrl(image.name);
    link.target = "_blank";
    link.rel = "noreferrer";
    const preview = element("img");
    preview.src = imageUrl(image.name);
    preview.alt = "Attached image " + (index + 1);
    preview.loading = "lazy";
    preview.addEventListener("load", keepBottom);
    preview.addEventListener("error", keepBottom);
    link.append(preview);
    gallery.append(link);
  });
  return gallery;
}

function keepBottom() {
  if (following) chatLog.scrollTop = chatLog.scrollHeight;
}

function createMessage(item) {
  const role = item.role;
  const label = {user: "You", assistant: "Coordinator", system: "Symphony", worker: "Worker"};
  const entry = element("article", "", "message " + role);
  if (Number.isFinite(Number(item.id))) entry.dataset.messageId = String(item.id);
  const content = element("div", role === "assistant" ? "" : item.text || "", "message-text");
  if (role === "assistant") {
    content.classList.add("issue-markdown");
    content.innerHTML = item.html || "";
  }
  entry.append(element("h2", label[role] || "Symphony"), content);
  if (item.images?.length) entry.append(imageGallery(item.images, "message-images"));
  return entry;
}

function insertMessage(item) {
  const id = Number(item.id);
  if (shownMessageIds.has(id)) return;
  const entry = createMessage(item);
  const next = [...(messages.children || [])].find((node) => {
    const other = Number(node.dataset?.messageId);
    return Number.isFinite(other) && other > id;
  });
  if (next && typeof messages.insertBefore === "function") messages.insertBefore(entry, next);
  else messages.append(entry);
  shownMessageIds.add(id);
}

function patchAttributes(current, next) {
  for (const attribute of [...next.attributes]) {
    if (current.getAttribute(attribute.name) !== attribute.value) {
      current.setAttribute(attribute.name, attribute.value);
    }
  }
  for (const attribute of [...current.attributes]) {
    if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  }
}

function patchChildren(current, next) {
  const existing = [...(current.childNodes || [])];
  const incoming = [...(next.childNodes || [])];
  const length = Math.max(existing.length, incoming.length);
  for (let index = 0; index < length; index += 1) {
    const oldChild = existing[index];
    const newChild = incoming[index];
    if (!oldChild) {
      current.append(newChild);
    } else if (!newChild) {
      current.removeChild(oldChild);
    } else {
      const kept = patchNode(oldChild, newChild);
      if (kept !== oldChild) current.replaceChild(kept, oldChild);
    }
  }
}

function patchNode(current, next) {
  if (!current || !next) return next || current;
  if (current.nodeType !== next.nodeType) return next;
  if (current.nodeType === 3) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return current;
  }
  if (current.nodeType !== 1) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return current;
  }
  if (current.tagName !== next.tagName) return next;
  patchAttributes(current, next);
  patchChildren(current, next);
  return current;
}

function captureSelection(target) {
  if (typeof target.contains !== "function") return null;
  const selection = globalThis.getSelection?.();
  if (!selection || !selection.rangeCount
      || !target.contains(selection.anchorNode) || !target.contains(selection.focusNode)) return null;
  return {
    anchor: selection.anchorNode, anchorOffset: selection.anchorOffset,
    focus: selection.focusNode, focusOffset: selection.focusOffset,
  };
}

function restoreSelection(saved) {
  if (!saved || saved.anchor.isConnected === false || saved.focus.isConnected === false) return;
  const selection = globalThis.getSelection?.();
  if (!selection) return;
  const limit = (node) => node.nodeType === 3 ? node.nodeValue.length : node.childNodes.length;
  const anchor = Math.min(saved.anchorOffset, limit(saved.anchor));
  const focus = Math.min(saved.focusOffset, limit(saved.focus));
  try {
    selection.setBaseAndExtent(saved.anchor, anchor, saved.focus, focus);
  } catch {
    // A replaced node no longer supports the old selection offsets.
  }
}

function updateStream(text, html) {
  if (text === renderedStream) return;
  const target = byId("stream");
  if (!text) {
    target.replaceChildren();
    renderedStream = "";
    return;
  }
  const saved = captureSelection(target);
  const source = document.createElement("div");
  source.innerHTML = html || "";
  patchChildren(target, source);
  restoreSelection(saved);
  renderedStream = text;
}

function queueMessage(item) {
  const id = Number(item?.id);
  if (item?.id == null || !Number.isFinite(id)
      || shownMessageIds.has(id) || pendingMessageIds.has(id)) return;
  pendingMessageIds.add(id);
  pendingMessages.push(item);
  scheduleChatFrame();
}

function queueStream(text, html) {
  if (pendingStream?.text === text && pendingStream?.html === html) return;
  pendingStream = {text, html};
  scheduleChatFrame();
}

function flushChat() {
  chatFrame = null;
  if (pendingMessages.length) {
    const batch = pendingMessages.toSorted((a, b) => Number(a.id) - Number(b.id));
    pendingMessages = [];
    for (const item of batch) {
      pendingMessageIds.delete(Number(item.id));
      insertMessage(item);
    }
  }
  if (pendingStream) {
    const next = pendingStream;
    pendingStream = null;
    updateStream(next.text, next.html);
  }
  byId("welcome").hidden = shownMessageIds.size > 0;
  keepBottom();
}

function scheduleChatFrame() {
  if (chatFrame !== null) return;
  chatFrame = typeof requestAnimationFrame === "function"
    ? requestAnimationFrame(flushChat) : setTimeout(flushChat, 0);
}

chatLog.addEventListener("scroll", () => {
  following = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80;
});

function setComposerError(message) {
  byId("request-error").textContent = message;
}

function setSendStatus(message) {
  byId("send-status").textContent = message;
}

function renderAttachments() {
  attachmentList.hidden = !pendingImages.length;
  attachmentList.replaceChildren(...pendingImages.map((image, index) => {
    const item = element("div", "", "composer-attachment");
    item.setAttribute("role", "listitem");
    const preview = element("img");
    preview.src = image.url;
    preview.alt = "Pasted image " + (index + 1);
    const remove = element("button", "", "attachment-remove");
    remove.type = "button";
    remove.title = "Remove attached image";
    remove.setAttribute("aria-label", "Remove attached image " + (index + 1));
    remove.append(icon("x"));
    remove.addEventListener("click", () => removeAttachment(image));
    item.append(preview, remove);
    return item;
  }));
}

function attachImage(file) {
  if (!file || !imageTypes.has(file.type)) {
    setComposerError("Attach PNG, JPEG, or WebP images.");
    return;
  }
  if (pendingImages.length >= imageCount) {
    setComposerError("Attach at most four images.");
    return;
  }
  if (file.size > imageBytes) {
    setComposerError("Each image must be at most 5 MiB.");
    return;
  }
  const total = pendingImages.reduce((sum, image) => sum + image.file.size, 0);
  if (total + file.size > imagesBytes) {
    setComposerError("Attached images must total at most 10 MiB.");
    return;
  }
  pendingImages.push({file, url: URL.createObjectURL(file)});
  setComposerError("");
  renderAttachments();
}

function removeAttachment(image) {
  const index = pendingImages.indexOf(image);
  if (index < 0) return;
  pendingImages.splice(index, 1);
  URL.revokeObjectURL(image.url);
  renderAttachments();
}

function clearAttachments(images) {
  for (const image of images) {
    const index = pendingImages.indexOf(image);
    if (index >= 0) pendingImages.splice(index, 1);
    URL.revokeObjectURL(image.url);
  }
  renderAttachments();
}

function imagePayload(image) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read an attached image."));
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      if (comma < 0) {
        reject(new Error("Could not read an attached image."));
        return;
      }
      resolve({type: image.file.type, data: result.slice(comma + 1)});
    };
    reader.readAsDataURL(image.file);
  });
}

function showLogin(state) {
  const login = byId("login");
  login.hidden = state.signedIn;
  if (state.signedIn) return;
  if (state.login) {
    const link = element("a", "Open device sign-in");
    link.href = state.login.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    login.replaceChildren(link, document.createTextNode(" and enter " + state.login.code + "."));
  } else if (!login.querySelector("button")) {
    const button = element("button", "Sign in to Codex");
    button.type = "button";
    button.addEventListener("click", () => submit("/login"));
    login.replaceChildren(button);
  }
}

function renderUpdateNotice() {
  const notice = byId("update-notice");
  const text = byId("update-text");
  const action = byId("update-action");
  const error = byId("update-error");
  const visible = updateState.draining || updateState.updateAvailable;
  notice.hidden = !visible;
  if (!visible) {
    error.hidden = true;
    return;
  }
  notice.dataset.tone = updateState.draining ? "draining" : "";
  text.textContent = updateState.draining
    ? "New tasks are held until current work finishes."
    : "New Symphony version available.";
  action.textContent = updateState.draining ? "Cancel" : "Update";
  action.disabled = updatePending;
  error.textContent = updateError;
  error.hidden = !updateError;
}

async function toggleDrain() {
  if (updatePending) return;
  const draining = !updateState.draining;
  updatePending = true;
  updateError = "";
  renderUpdateNotice();
  try {
    const result = await api("/api/drain", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Symphony": "1"},
      body: JSON.stringify({draining}),
    });
    updateState = {updateAvailable: updateState.updateAvailable, draining: result.draining === true};
  } catch (error) {
    updateError = error.message;
  } finally {
    updatePending = false;
    renderUpdateNotice();
  }
}

function showCodexStatus() {
  byId("status-model").textContent = [codex.model || "Codex", codex.effort].filter(Boolean).join(" ");
  const context = byId("status-context");
  context.hidden = codex.contextUsed == null;
  if (!context.hidden) context.textContent = "Context " + codex.contextUsed + "% used";
  for (const label of ["5h", "weekly"]) {
    const part = byId("status-" + label);
    const limit = codex.rateLimits?.[label];
    part.hidden = !limit;
    if (!limit) continue;
    part.textContent = label + " " + limit.left + "% left";
    part.dataset.level = limit.left <= 10 ? "danger" : limit.left <= 35 ? "caution" : "";
    part.title = limit.resetsAt ? "Resets at " + clockTime(limit.resetsAt) : "";
  }
  const timing = byId("turn-timing");
  const {startedAt, completedAt, durationMs} = codex.turnTiming || {};
  const working = canInterrupt && startedAt;
  timing.hidden = !working && durationMs == null;
  if (!timing.hidden) {
    const seconds = Math.max(0, Math.floor(working ? Date.now() / 1000 - startedAt : durationMs / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds / 60) % 60;
    const duration = [hours && hours + "h", minutes && minutes + "m", seconds % 60 + "s"].filter(Boolean).join(" ");
    timing.textContent = (working ? "Working for " : "Worked for ") + duration
      + (!working && completedAt ? " · " + clockTime(completedAt) : "");
  }
  interrupt.hidden = !canInterrupt;
}

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
}

function schedulePoll(delay) {
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, delay);
}

function wakePoll() {
  if (!pollStarted) return;
  if (polling) {
    pollAgain = true;
    return;
  }
  schedulePoll(0);
}

async function poll() {
  pollTimer = null;
  pollStarted = true;
  if (polling) {
    pollAgain = true;
    return;
  }
  polling = true;
  let delay = 3000;
  try {
    const state = await api("/api/state?after=" + lastMessage + "&issues=" + issueRevision);
    for (const item of state.messages) queueMessage(item);
    if (state.messages.length) {
      const latest = Number(state.messages[state.messages.length - 1].id);
      if (Number.isFinite(latest) && latest > lastMessage) lastMessage = latest;
    }
    if (stream !== state.stream) {
      stream = state.stream;
      queueStream(stream, state.streamHtml);
    }
    const tasksChanged = JSON.stringify(tasks) !== JSON.stringify(state.tasks);
    const choicesChanged = JSON.stringify([codex.models, codex.efforts])
      !== JSON.stringify([state.codex.models, state.codex.efforts]);
    const today = new Date().toDateString();
    const redrawIssues = tasksChanged || today !== issueDay;
    issueDay = today;
    tasks = state.tasks;
    if (state.projects) {
      if (!issueRevision) selected = state.view;
      updateProjects(state.projects, redrawIssues);
      issueRevision = state.issueRevision;
    } else if (redrawIssues) {
      renderPanes();
    }
    const notice = byId("project-error");
    if (notice.textContent !== state.issueError) notice.textContent = state.issueError;
    notice.hidden = !state.issueError;
    codex = state.codex;
    canInterrupt = state.canInterrupt;
    updateState = {updateAvailable: Boolean(state.updateAvailable), draining: Boolean(state.draining)};
    if (choicesChanged || tasksChanged) renderCommands();
    showCodexStatus();
    renderUpdateNotice();
    byId("connection-error").hidden = true;
    byId("paused").hidden = !state.paused;
    byId("paused").textContent = state.paused ? "Dispatch paused. " + state.paused : "";
    showLogin(state);
    delay = pollDelay(state, document.hidden);
  } catch {
    byId("connection-error").hidden = false;
  } finally {
    polling = false;
    if (pollAgain) {
      pollAgain = false;
      delay = 0;
    }
    schedulePoll(delay);
  }
}

async function submit(text, images = [], project = selected) {
  const errorField = byId("request-error");
  errorField.textContent = "";
  try {
    const result = await api("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Symphony": "1"},
      body: JSON.stringify({text, project, images}),
    });
    if (result.message) queueMessage(result.message);
    wakePoll();
    return {ok: true, message: result.message || null};
  } catch (error) {
    errorField.textContent = error.message;
    return {ok: false, message: null};
  }
}

async function interruptTurn() {
  if (!canInterrupt || interrupt.disabled) return;
  interrupt.disabled = true;
  try {
    await submit("/interrupt");
  } finally {
    interrupt.disabled = false;
  }
}

repos.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...repos.children];
  const offset = event.key === "ArrowLeft" ? -1 : 1;
  let index = (tabs.indexOf(event.target) + offset + tabs.length) % tabs.length;
  if (event.key === "Home") index = 0;
  if (event.key === "End") index = tabs.length - 1;
  event.preventDefault();
  tabs[index].focus();
  tabs[index].click();
});
const commands = [
  ["/model ", "Choose the Codex model"],
  ["/effort ", "Set reasoning effort"],
  ["/compact", "Compact the conversation context"],
  ["/status", "Show Codex usage and approved work"],
  ["/interrupt", "Stop the current Codex turn"],
  ["/login", "Sign in to Codex"],
  ["/continue", "Resume worker dispatch"],
  ["/retry ", "Retry a parked issue"],
];
const history = [];
let historyIndex = 0;
let historyDraft = "";
let sending = false;
let commandOptions = [];
let activeOption = 0;
let dismissedDraft = null;

function matchingCommands() {
  const match = /^(\/[\w-]*)(?: +(\S*))?$/.exec(messageField.value.toLowerCase());
  if (!match) return [];
  const [, command, prefix] = match;
  if (prefix === undefined) return commands.filter(([value]) => value.startsWith(command));
  let options = [];
  if (command === "/model") options = Object.entries(codex.models || {});
  if (command === "/effort") options = (codex.efforts || []).map((effort) => [effort, ""]);
  if (command === "/retry") {
    options = tasks.filter((task) => ["approving", "blocked", "finish_failed"].includes(task.phase))
      .map((task) => [task.issue, task.phase.replaceAll("_", " ")]);
  }
  return options.filter(([value]) => value.toLowerCase().startsWith(prefix))
    .map(([value, label]) => [command + " " + value, label === value ? "" : label]);
}

function hideCommands() {
  commandList.hidden = true;
  messageField.removeAttribute("aria-activedescendant");
}

function highlightOption(index) {
  activeOption = (index + commandOptions.length) % commandOptions.length;
  for (const [index, option] of [...commandList.children].entries()) {
    option.setAttribute("aria-selected", index === activeOption);
  }
  const option = commandList.children[activeOption];
  messageField.setAttribute("aria-activedescendant", option.id);
  option.scrollIntoView({block: "nearest"});
}

function renderCommands() {
  if (document.activeElement !== messageField || messageField.value === dismissedDraft) {
    hideCommands();
    return;
  }
  const previous = commandOptions[activeOption]?.[0];
  commandOptions = matchingCommands();
  if (!commandOptions.length) {
    hideCommands();
    return;
  }
  const rows = commandOptions.map(([value, description], index) => {
    const option = element("li");
    option.id = "command-option-" + index;
    option.setAttribute("role", "option");
    option.append(element("code", value.trimEnd()));
    if (description) option.append(element("span", description));
    option.addEventListener("mousedown", (event) => event.preventDefault());
    option.addEventListener("click", () => chooseCommand(index, true));
    return option;
  });
  commandList.replaceChildren(...rows);
  commandList.hidden = false;
  highlightOption(Math.max(0, commandOptions.findIndex(([value]) => value === previous)));
}

function writeDraft(value, suggest = false) {
  messageField.value = value;
  messageField.setSelectionRange(value.length, value.length);
  dismissedDraft = suggest ? null : value;
  renderCommands();
}

function chooseCommand(index, run) {
  const [value] = commandOptions[index];
  const needsArgument = value.endsWith(" ");
  messageField.focus();
  writeDraft(value, needsArgument);
  if (run && !needsArgument) composer.requestSubmit();
}

function recallHistory(direction) {
  if (!history.length || messageField.selectionStart !== messageField.selectionEnd) return false;
  if (historyIndex === history.length) {
    if (direction > 0 || messageField.selectionStart !== 0) return false;
    historyDraft = messageField.value;
  }
  historyIndex = Math.min(history.length, Math.max(0, historyIndex + direction));
  writeDraft(history[historyIndex] ?? historyDraft);
  return true;
}

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageField.value;
  const project = selected;
  if (sending || (!text.trim() && !pendingImages.length)) return;
  if (pendingImages.length && text.trim().startsWith("/")) {
    setComposerError("Images cannot be sent with slash commands.");
    return;
  }
  setComposerError("");
  sending = true;
  setSendStatus("Sending…");
  const accepted = [...pendingImages];
  let images = [];
  try {
    images = await Promise.all(accepted.map(imagePayload));
  } catch (error) {
    setComposerError(error.message);
    setSendStatus("");
    sending = false;
    return;
  }
  if (messageField.value === text) writeDraft("");
  const result = await submit(text, images, project);
  if (result.ok) {
    if (text.trim()) {
      history.push(text);
      if (history.length > 100) history.shift();
      historyIndex = history.length;
    }
    clearAttachments(accepted);
  } else if (!messageField.value) {
    writeDraft(text);
  }
  setSendStatus("");
  sending = false;
});

messageField.addEventListener("paste", (event) => {
  const items = [...(event.clipboardData?.items || [])];
  const files = items
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  for (const file of files) attachImage(file);
});

messageField.addEventListener("input", () => {
  historyIndex = history.length;
  dismissedDraft = null;
  renderCommands();
});
messageField.addEventListener("focus", renderCommands);
messageField.addEventListener("pointerdown", () => messageField.classList.add("pointer-focus"));
messageField.addEventListener("blur", () => {
  messageField.classList.remove("pointer-focus");
  hideCommands();
});
messageField.addEventListener("keydown", (event) => {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.repeat && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    return;
  }
  if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
  if (!commandList.hidden && ["Enter", "Tab"].includes(event.key)) {
    event.preventDefault();
    chooseCommand(activeOption, event.key === "Enter");
  } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    const direction = event.key === "ArrowUp" ? -1 : 1;
    if (!commandList.hidden) {
      event.preventDefault();
      highlightOption(activeOption + direction);
    } else if (recallHistory(direction)) {
      event.preventDefault();
    }
  } else if (event.key === "Enter") {
    event.preventDefault();
    composer.requestSubmit();
  }
});
interrupt.addEventListener("click", interruptTurn);
byId("update-action").addEventListener("click", toggleDrain);
backlogToggle.addEventListener("click", () => setPaneCollapsed("backlog", !backlogCollapsed));
queueToggle.addEventListener("click", () => setPaneCollapsed("queue", !queueCollapsed));
applyPaneState();
document.addEventListener("keydown", (event) => {
  if (document.querySelector("dialog[open]")) return;
  if (event.key !== "Escape" || event.defaultPrevented || event.repeat || event.isComposing
      || event.keyCode === 229 || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
  if (commandList.hidden) return;
  dismissedDraft = messageField.value;
  hideCommands();
  event.preventDefault();
});

setInterval(() => {
  for (const pill of document.querySelectorAll("[data-stage-start]")) updateStagePill(pill);
}, 30000);
