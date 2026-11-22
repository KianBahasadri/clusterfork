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
const configButton = byId("config-open");
const configDialog = byId("config-dialog");
const configOpenCodeStatus = byId("config-opencode-status");
const configOpenCodeForm = byId("config-opencode-form");
const configOpenCodeKey = byId("config-opencode-key");
const configOpenCodeSave = byId("config-opencode-save");
const configOpenCodeClear = byId("config-opencode-clear");
const configOpenCodeFeedback = byId("config-opencode-feedback");
const configCodexStatus = byId("config-codex-status");
const configCodexDetails = byId("config-codex-details");
const configCodexLogin = byId("config-codex-login");
const configCodexSignin = byId("config-codex-signin");
const configCodexCancel = byId("config-codex-cancel");
const configCodexSignout = byId("config-codex-signout");
const configCodexFeedback = byId("config-codex-feedback");
const configContent = byId("config-agents-content");
const configStatus = byId("config-agents-status");
let configRequest = 0;
let configDataRequest = 0;
let configData = null;
let configPending = false;
let configAuthKey = "";
const pendingImages = [];
const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
const imageCount = 4;
const imageBytes = 5 * 1024 * 1024;
const imagesBytes = 10 * 1024 * 1024;
const expandedPlans = new Set();
const collapsedGroups = new Set();
const pendingPlans = new Set();
const approvingPlans = new Set();
const approvedPlans = new Set();
const proposalErrors = new Map();
const priorityIcons = ["ellipsis", "octagon-alert", "signal", "signal-medium", "signal-low"];
let issueCache = new Map();
let projectRepos = new Map();
let repoButtons = [];
const armedButtons = new Map();
let mermaidLoader = null;
let diagramKey = "";
let diagramBlobUrl = "";
let diagramSeq = 0;
let diagramRequest = 0;
let currentDiagram = null;
const dismissedDiagrams = new Map();
let issueRevision = "";
let selected = "";
let selectedRepo = null;
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
const chatScroll = new Map();
let pendingChatScrollRepo = null;
const seenRepoMessage = new Map();
const unreadRepos = new Set();
let repoActivityInitialized = false;
let pollTimer = null;
let polling = false;
let pollAgain = false;
let pollStarted = false;
let following = true;
let codex = {};
let canInterrupt = false;
let compacting = "";
let updateState = {updateAvailable: false, draining: false, update: null};
let updatePending = false;
let updateError = "";
const SAFE_COMPARE_URL = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/compare\/[0-9a-f]{40}\.\.\.[0-9a-f]{40}$/;
const BASE_TITLE = document.title || "Symphony";
let usageAlertActive = false;
let usageNotifiedKey = "";
let lastUsageAlert = null;

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

function renderConfigProviders() {
  const data = configData;
  const openCode = data?.opencodeGo;
  if (!openCode) {
    configOpenCodeStatus.textContent = "Loading OpenCode Go status…";
    configOpenCodeStatus.dataset.error = "";
    configOpenCodeKey.disabled = true;
    configOpenCodeSave.disabled = true;
    configOpenCodeClear.disabled = true;
  } else {
    configOpenCodeStatus.dataset.error = openCode.malformed ? "true" : "";
    configOpenCodeStatus.textContent = openCode.malformed
      ? "Stored auth file is unreadable. Wait for idle before replacing or removing it."
      : (openCode.configured ? "API key configured on PVC." : "Not configured.")
        + (openCode.busy ? " Worker active; changes are paused until idle." : "");
    const disabled = configPending || Boolean(openCode.busy);
    configOpenCodeKey.disabled = disabled;
    configOpenCodeSave.disabled = disabled;
    configOpenCodeClear.disabled = disabled || (!openCode.configured && !openCode.malformed);
  }
  const codexCfg = data?.codex;
  if (!codexCfg) {
    configCodexStatus.textContent = "Loading Codex account…";
    configCodexDetails.hidden = true;
    configCodexLogin.hidden = true;
    configCodexSignin.hidden = false;
    configCodexCancel.hidden = true;
    configCodexSignout.hidden = true;
    configCodexSignin.disabled = true;
    return;
  }
  const account = codexCfg.account || {};
  const busy = Boolean(codexCfg.busy);
  configCodexStatus.textContent = (codexCfg.signedIn ? "Signed in." : "Signed out.")
    + (busy ? " Coordinator turn active; account changes wait until idle." : "");
  const parts = [account.email, account.plan, account.workspace].filter(Boolean);
  configCodexDetails.textContent = parts.join(" · ");
  configCodexDetails.hidden = !parts.length;
  if (codexCfg.login) {
    const link = element("a", "Open device sign-in");
    link.href = codexCfg.login.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    configCodexLogin.replaceChildren(link, document.createTextNode(" and enter " + codexCfg.login.code + "."));
    configCodexLogin.hidden = false;
  } else {
    configCodexLogin.replaceChildren();
    configCodexLogin.hidden = true;
  }
  const codexDisabled = configPending || busy;
  configCodexSignin.hidden = Boolean(codexCfg.signedIn || codexCfg.login);
  configCodexCancel.hidden = !codexCfg.login;
  configCodexSignout.hidden = !codexCfg.signedIn;
  configCodexSignin.disabled = codexDisabled;
  configCodexCancel.disabled = codexDisabled;
  configCodexSignout.disabled = codexDisabled;
}

async function loadConfigState() {
  const request = ++configDataRequest;
  renderConfigProviders();
  try {
    const result = await api("/api/config");
    if (request !== configDataRequest || !configDialog.open) return;
    configData = result;
    renderConfigProviders();
  } catch (error) {
    if (request !== configDataRequest || !configDialog.open) return;
    configOpenCodeStatus.textContent = "Could not load configuration: " + error.message;
    configOpenCodeStatus.dataset.error = "true";
    configCodexStatus.textContent = "Could not load configuration: " + error.message;
  }
}

async function mutateConfig(payload, feedbackNode, successText, clearInput = false) {
  if (configPending) return;
  configPending = true;
  feedbackNode.textContent = "";
  feedbackNode.dataset.error = "";
  renderConfigProviders();
  try {
    const result = await api("/api/config", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Symphony": "1"},
      body: JSON.stringify(payload),
    });
    configData = result;
    if (clearInput) configOpenCodeKey.value = "";
    feedbackNode.textContent = successText;
    feedbackNode.dataset.error = "";
    wakePoll();
  } catch (error) {
    feedbackNode.textContent = error.message;
    feedbackNode.dataset.error = "true";
    loadConfigState();
  } finally {
    configPending = false;
    renderConfigProviders();
  }
}

async function loadConfigAgents() {
  const request = ++configRequest;
  configContent.replaceChildren();
  configStatus.textContent = "Loading global AGENTS.md…";
  configStatus.dataset.error = "";
  try {
    const result = await api("/api/agents");
    if (request !== configRequest || !configDialog.open) return;
    const content = typeof result.content === "string" ? result.content : "";
    configContent.innerHTML = content && typeof result.html === "string" ? result.html : "";
    configStatus.textContent = content ? "" : "No global defaults are currently defined.";
  } catch (error) {
    if (request !== configRequest || !configDialog.open) return;
    configStatus.textContent = "Could not load global AGENTS.md: " + error.message;
    configStatus.dataset.error = "true";
  }
}

function openConfig() {
  configButton.setAttribute("aria-expanded", "true");
  configOpenCodeKey.value = "";
  configOpenCodeFeedback.textContent = "";
  configOpenCodeFeedback.dataset.error = "";
  configCodexFeedback.textContent = "";
  configCodexFeedback.dataset.error = "";
  configData = null;
  if (!configDialog.open) configDialog.showModal();
  byId("config-close").focus();
  loadConfigState();
  loadConfigAgents();
}

function closeConfig() {
  if (configDialog.open) configDialog.close();
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
      if (issue.planHash) {
        const actions = element("div", "", "plan-proposal-actions");
        const accept = element("button", "", "plan-accept-button");
        accept.type = "button";
        const key = "card:" + issue.identifier + ":" + issue.planHash;
        const alreadyApproved = approvedPlans.has(issue.identifier);
        const busy = approvingPlans.has(key);
        accept.textContent = alreadyApproved ? "Approved" : busy ? "Approving…" : "Accept";
        accept.disabled = alreadyApproved || busy;
        accept.dataset.state = alreadyApproved ? "approved" : busy ? "pending" : "active";
        accept.addEventListener("click", () => acceptPlan(issue.identifier, issue.planHash, key));
        const err = element("p", proposalErrors.get(key) || "", "plan-accept-error");
        err.setAttribute("role", "status");
        err.hidden = !proposalErrors.has(key);
        actions.append(accept, err);
        plan.append(actions);
      }
      plan.append(element("p", issue.state.name === "Todo"
        ? "This issue is awaiting plan approval in chat and will not run."
        : "Tell the coordinator in chat when you approve this plan.", "muted"));
    }
    row.append(plan);
  }
  return row;
}

async function acceptPlan(issueId, planHash, errorKey) {
  if (!issueId || !planHash || approvingPlans.has(errorKey) || approvedPlans.has(issueId)) return;
  approvingPlans.add(errorKey);
  proposalErrors.delete(errorKey);
  syncApproveButtons();
  renderPanes();
  try {
    await api("/api/approve", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Symphony": "1"},
      body: JSON.stringify({issue: issueId, planHash}),
    });
    approvedPlans.add(issueId);
    wakePoll();
  } catch (error) {
    proposalErrors.set(errorKey, error.message);
  } finally {
    approvingPlans.delete(errorKey);
    syncApproveButtons();
    renderPanes();
  }
}

function syncApproveButtons() {
  const containers = [...(messages.querySelectorAll?.(".plan-proposal-actions") || [])];
  if (!containers.length) return;
  const latestByIssue = new Map();
  for (const box of containers) {
    const issue = box.dataset.issue || "";
    const msgId = Number(box.dataset.messageId) || 0;
    if (issue && msgId >= (latestByIssue.get(issue) || 0)) latestByIssue.set(issue, msgId);
  }
  for (const box of containers) {
    const issue = box.dataset.issue || "";
    const planHash = box.dataset.planHash || "";
    const msgId = Number(box.dataset.messageId) || 0;
    const key = "chat:" + msgId;
    const button = box.querySelector(".plan-accept-button");
    const err = box.querySelector(".plan-accept-error");
    if (!button || !err) continue;
    const recorded = approvedPlans.has(issue) || tasks.some((item) => item.issue === issue);
    const superseded = !recorded && msgId !== latestByIssue.get(issue);
    const busy = approvingPlans.has(key);
    button.textContent = recorded ? "Approved" : superseded ? "Superseded" : busy ? "Approving…" : "Accept";
    button.disabled = recorded || superseded || busy;
    button.dataset.state = recorded ? "approved" : superseded ? "superseded" : busy ? "pending" : "active";
    if (recorded || superseded) proposalErrors.delete(key);
    const errText = proposalErrors.get(key) || "";
    err.textContent = errText;
    err.hidden = !errText;
    if (planHash && !recorded && !superseded && button.dataset.bound !== "1") {
      button.dataset.bound = "1";
      button.addEventListener("click", () => acceptPlan(issue, planHash, key));
    }
  }
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

function renderUnreadChips() {
  for (const tab of repos.children) {
    const repo = tab.dataset.repo || "";
    const unread = Boolean(repo && repo !== selectedRepo && unreadRepos.has(repo));
    tab.toggleAttribute("data-unread", unread);
    tab.setAttribute("aria-label", unread ? tab.title + " (unread activity)" : tab.title);
  }
}

function repoForProject(project = selected) {
  return projectRepos.has(project) ? (projectRepos.get(project) || "") : (selectedRepo || "");
}

function armedSetFor(repo) {
  let set = armedButtons.get(repo);
  if (!set) {
    set = new Set();
    armedButtons.set(repo, set);
  }
  return set;
}

function armedButtonsFor(repo) {
  const set = armedButtons.get(repo);
  if (!set?.size) return [];
  return repoButtons.filter((btn) => btn.repo === repo && btn.mode === "attach" && set.has(btn.label));
}

function renderRepoButtons(buttons = repoButtons, activeRepo = selectedRepo || "") {
  repoButtons = Array.isArray(buttons) ? buttons : [];
  const bar = byId("repo-buttons");
  if (!bar) return;
  const visible = repoButtons.filter((btn) => btn.repo === activeRepo);
  bar.hidden = !visible.length;
  if (!visible.length) {
    bar.replaceChildren();
    return;
  }
  const armed = armedButtons.get(activeRepo);
  bar.replaceChildren(...visible.map((btn) => {
    const button = element("button", btn.label, "repo-prompt-button");
    button.type = "button";
    button.dataset.mode = btn.mode;
    button.title = (btn.mode === "attach" ? "Attach to next message: " : "Send: ") + btn.prompt;
    if (btn.mode === "attach") {
      button.setAttribute("aria-pressed", String(Boolean(armed?.has(btn.label))));
    }
    button.addEventListener("click", () => handleRepoButtonClick(btn, activeRepo));
    return button;
  }));
}

async function handleRepoButtonClick(btn, activeRepo) {
  if (btn.mode === "attach") {
    const set = armedSetFor(activeRepo);
    if (set.has(btn.label)) set.delete(btn.label);
    else set.add(btn.label);
    renderRepoButtons(repoButtons, activeRepo);
    messageField.focus();
    return;
  }
  if (sending) return;
  sending = true;
  setComposerError("");
  setSendStatus("Sending…");
  try {
    await submit(btn.prompt, [], selected);
  } finally {
    setSendStatus("");
    sending = false;
  }
}

function loadMermaid() {
  if (globalThis.mermaid) return Promise.resolve(globalThis.mermaid);
  if (mermaidLoader) return mermaidLoader;
  mermaidLoader = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/mermaid.min.js";
    script.addEventListener("load", () => {
      if (globalThis.mermaid) resolve(globalThis.mermaid);
      else reject(new Error("Mermaid bundle did not initialize."));
    });
    script.addEventListener("error", () => {
      mermaidLoader = null;
      reject(new Error("Could not load Mermaid renderer (/mermaid.min.js)."));
    });
    document.head.append(script);
  });
  return mermaidLoader;
}

function clearDiagramPanel(hide = true) {
  if (diagramBlobUrl) {
    URL?.revokeObjectURL?.(diagramBlobUrl);
    diagramBlobUrl = "";
  }
  byId("diagram-canvas")?.replaceChildren();
  const err = byId("diagram-error");
  if (err) {
    err.textContent = "";
    err.hidden = true;
  }
  if (hide) {
    const panel = byId("diagram-panel");
    if (panel) panel.hidden = true;
    currentDiagram = null;
  }
  diagramKey = "";
}

async function renderDiagramPanel(diagram, activeRepo = selectedRepo || "") {
  const panel = byId("diagram-panel");
  if (!panel) return;
  if (!diagram || dismissedDiagrams.get(activeRepo) === diagram.updated) {
    clearDiagramPanel(true);
    return;
  }
  currentDiagram = {repo: activeRepo, updated: diagram.updated};
  const key = JSON.stringify([activeRepo, diagram.updated, diagram.title, diagram.source]);
  panel.hidden = false;
  byId("diagram-title").textContent = diagram.title || "Diagram";
  byId("diagram-source").textContent = diagram.source || "";
  if (key === diagramKey) return;
  diagramKey = key;
  const req = ++diagramRequest;
  const err = byId("diagram-error");
  err.textContent = "";
  err.hidden = true;
  try {
    const renderer = await loadMermaid();
    if (req !== diagramRequest || currentDiagram?.updated !== diagram.updated) return;
    const dark = globalThis.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
    renderer.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      htmlLabels: false,
      theme: dark ? "dark" : "default",
    });
    const rendered = await renderer.render("symphony-diagram-" + (++diagramSeq), diagram.source);
    if (req !== diagramRequest || currentDiagram?.updated !== diagram.updated) return;
    const svg = rendered?.svg;
    if (typeof svg !== "string" || !svg.trim()) {
      throw new Error("Mermaid returned empty SVG output.");
    }
    const blobUrl = URL.createObjectURL(new Blob([svg], {type: "image/svg+xml"}));
    if (diagramBlobUrl) URL?.revokeObjectURL?.(diagramBlobUrl);
    diagramBlobUrl = blobUrl;
    const image = element("img", "", "diagram-svg");
    image.src = blobUrl;
    image.alt = diagram.title || "Mermaid diagram";
    byId("diagram-canvas").replaceChildren(image);
  } catch (error) {
    if (req !== diagramRequest) return;
    if (diagramBlobUrl) {
      URL?.revokeObjectURL?.(diagramBlobUrl);
      diagramBlobUrl = "";
    }
    byId("diagram-canvas").replaceChildren();
    err.textContent = "Mermaid render error: " + (error?.message || "Invalid diagram syntax.");
    err.hidden = false;
  }
}

function updateRepoActivity(activity, activeRepo) {
  if (!activity || typeof activity !== "object") return;
  if (!repoActivityInitialized) {
    repoActivityInitialized = true;
    for (const [repo, id] of Object.entries(activity)) {
      seenRepoMessage.set(repo, Number(id) || 0);
    }
    unreadRepos.delete(activeRepo);
    renderUnreadChips();
    return;
  }
  for (const [repo, raw] of Object.entries(activity)) {
    const id = Number(raw) || 0;
    const previous = seenRepoMessage.get(repo) || 0;
    if (id > previous) {
      seenRepoMessage.set(repo, id);
      if (repo !== activeRepo) unreadRepos.add(repo);
    }
  }
  unreadRepos.delete(activeRepo);
  renderUnreadChips();
}

function resetChatView(repo) {
  if (chatFrame !== null) {
    if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(chatFrame);
    else clearTimeout(chatFrame);
    chatFrame = null;
  }
  lastMessage = 0;
  pendingMessages = [];
  pendingMessageIds.clear();
  shownMessageIds.clear();
  pendingStream = null;
  stream = "";
  renderedStream = "";
  compacting = "";
  clearDiagramPanel(false);
  messages.replaceChildren();
  updateStream("", "");
  byId("welcome").hidden = false;
  following = !chatScroll.has(repo);
  pendingChatScrollRepo = repo;
}

function updateProjects(projects, redraw) {
  const previous = issueCache.get(selected);
  projectRepos = new Map(projects.map((project) => [project.id, project.repo || ""]));
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
    tab.dataset.repo = project.repo || "";
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
  const hasRepo = projectRepos.has(project);
  const nextRepo = hasRepo ? projectRepos.get(project) : selectedRepo;
  const switchedRepo = hasRepo && selectedRepo !== null && nextRepo !== selectedRepo;
  if (switchedRepo) {
    chatScroll.set(selectedRepo, chatLog.scrollTop);
    selectedRepo = nextRepo;
    unreadRepos.delete(nextRepo);
    resetChatView(nextRepo);
  } else if (hasRepo && selectedRepo === null) {
    selectedRepo = nextRepo;
    unreadRepos.delete(nextRepo);
  }
  for (const tab of repos.children) {
    const active = tab.dataset.project === project;
    tab.setAttribute("aria-selected", active);
    tab.tabIndex = active ? 0 : -1;
    if (active) {
      backlog.setAttribute("aria-labelledby", tab.id);
      statusIssues.setAttribute("aria-labelledby", tab.id);
    }
  }
  renderUnreadChips();
  renderRepoButtons(repoButtons, selectedRepo || "");
  renderPanes();
  restorePaneScroll(project);
  if (switchedRepo) wakePoll();
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
  if (item.meta?.kind === "plan_proposal" && item.meta.issue && item.meta.planHash) {
    const actions = element("div", "", "plan-proposal-actions");
    actions.dataset.issue = item.meta.issue;
    actions.dataset.planHash = item.meta.planHash;
    actions.dataset.messageId = String(item.id || 0);
    const accept = element("button", "Accept", "plan-accept-button");
    accept.type = "button";
    const err = element("p", "", "plan-accept-error");
    err.setAttribute("role", "status");
    err.hidden = true;
    actions.append(accept, err);
    entry.append(actions);
  }
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
    syncApproveButtons();
  }
  if (pendingStream) {
    const next = pendingStream;
    pendingStream = null;
    updateStream(next.text, next.html);
  }
  byId("welcome").hidden = shownMessageIds.size > 0;
  if (pendingChatScrollRepo !== null && pendingChatScrollRepo === selectedRepo) {
    const target = pendingChatScrollRepo;
    pendingChatScrollRepo = null;
    if (chatScroll.has(target)) chatLog.scrollTop = chatScroll.get(target) || 0;
    else keepBottom();
  } else {
    keepBottom();
  }
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
  const details = byId("update-details");
  const action = byId("update-action");
  const error = byId("update-error");
  const visible = updateState.draining || updateState.updateAvailable;
  notice.hidden = !visible;
  if (!visible) {
    if (details) {
      details.replaceChildren();
      details.hidden = true;
    }
    error.hidden = true;
    return;
  }
  notice.dataset.tone = updateState.draining ? "draining" : "";
  text.textContent = updateState.draining
    ? "New tasks are held until current work finishes."
    : "New Symphony version available.";
  if (details) {
    const release = updateState.updateAvailable ? updateState.update : null;
    const compareUrl = typeof release?.compareUrl === "string" && SAFE_COMPARE_URL.test(release.compareUrl)
      ? release.compareUrl : "";
    const commits = Array.isArray(release?.commits)
      ? release.commits.filter((item) => typeof item === "string" && item.trim())
      : [];
    if (compareUrl || commits.length) {
      const children = [];
      if (compareUrl) {
        const shortRun = typeof release.runningCommit === "string" ? release.runningCommit.slice(0, 7) : "";
        const shortAvail = typeof release.availableCommit === "string" ? release.availableCommit.slice(0, 7) : "";
        const range = shortRun && shortAvail ? shortRun + "\u2192" + shortAvail : "Compare release";
        const link = element("a", range, "update-compare");
        link.href = compareUrl;
        link.target = "_blank";
        link.rel = "noreferrer";
        children.push(link);
        if (Number.isInteger(release.commitCount) && release.commitCount > 0) {
          const noun = release.commitCount === 1 ? "commit" : "commits";
          children.push(element("span", "(" + release.commitCount + " " + noun + ")", "muted"));
        }
      }
      if (commits.length === 1 && (!Number.isInteger(release?.commitCount) || release.commitCount <= 1)) {
        children.push(element("span", commits[0], "update-commit-single"));
      } else if (commits.length) {
        const list = element("ul", "", "update-commits");
        for (const subject of commits) list.append(element("li", subject));
        if (Number.isInteger(release?.commitCount) && release.commitCount > commits.length) {
          list.append(element("li", "(+" + (release.commitCount - commits.length) + " more)", "muted"));
        }
        children.push(list);
      }
      details.replaceChildren(...children);
      details.hidden = false;
    } else {
      details.replaceChildren();
      details.hidden = true;
    }
  }
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
    updateState = {...updateState, draining: result.draining === true};
  } catch (error) {
    updateError = error.message;
  } finally {
    updatePending = false;
    renderUpdateNotice();
  }
}

function formatTurnDuration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  return [hours && hours + "h", minutes && minutes + "m", seconds % 60 + "s"].filter(Boolean).join(" ");
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
    part.title = limit.resetsAt ? "Resets " + formatResetTime(limit.resetsAt) : "";
  }
  const timing = byId("turn-timing");
  const {startedAt, completedAt, durationMs} = codex.turnTiming || {};
  const working = Boolean(canInterrupt && startedAt);
  const compactTiming = compacting === "running" && startedAt && completedAt == null && durationMs == null;
  const elapsedSeconds = working || compactTiming
    ? Math.max(0, Math.floor(Date.now() / 1000 - startedAt)) : 0;
  if (compacting === "running") {
    timing.hidden = false;
    timing.textContent = compactTiming
      ? "Compacting for " + formatTurnDuration(elapsedSeconds) : "Compacting context…";
  } else if (compacting === "queued") {
    timing.hidden = false;
    timing.textContent = working
      ? "Working for " + formatTurnDuration(elapsedSeconds) + " · Compaction queued"
      : "Compaction queued";
  } else {
    timing.hidden = !working && durationMs == null;
    if (!timing.hidden) {
      const duration = formatTurnDuration(working ? elapsedSeconds : Math.max(0, Math.floor(durationMs / 1000)));
      timing.textContent = (working ? "Working for " : "Worked for ") + duration
        + (!working && completedAt ? " · " + clockTime(completedAt) : "");
    }
  }
  interrupt.hidden = !canInterrupt;
}

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
}

function formatResetTime(seconds, now = new Date()) {
  if (typeof seconds === "string") return seconds;
  if (!Number.isFinite(seconds)) return "";
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  if (date.toDateString() === now.toDateString()) return clockTime(seconds);
  return date.toLocaleString([], {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
}

function renderUsageAlert(alert) {
  lastUsageAlert = alert || null;
  const box = byId("usage-alert");
  const textNode = byId("usage-alert-text");
  const enableButton = byId("usage-notify-enable");
  if (!alert?.active) {
    box.hidden = true;
    textNode.textContent = "";
    enableButton.hidden = true;
    usageNotifiedKey = "";
    if (usageAlertActive) {
      document.title = BASE_TITLE;
      usageAlertActive = false;
    }
    return;
  }
  const windows = Array.isArray(alert.windows) ? alert.windows : [];
  const parts = [windows.length
    ? "Coordinator out of usage (" + windows.join(", ") + " at 0%)."
    : "Coordinator out of usage."];
  if (alert.resetsAt != null) {
    const formatted = formatResetTime(alert.resetsAt);
    if (formatted) parts.push("Resets " + formatted + ".");
  }
  if (alert.detail) parts.push(alert.detail);
  const message = parts.join(" ");
  textNode.textContent = message;
  box.hidden = false;
  usageAlertActive = true;
  document.title = "\u26a0 Out of usage \u00b7 " + BASE_TITLE;
  const notifier = globalThis.Notification;
  if (typeof notifier === "function") {
    enableButton.hidden = notifier.permission !== "default";
    const key = JSON.stringify([alert.source || "", windows, alert.resetsAt ?? null, alert.detail || ""]);
    if (notifier.permission === "granted" && key !== usageNotifiedKey) {
      usageNotifiedKey = key;
      const unfocused = document.hidden || (typeof document.hasFocus === "function" && !document.hasFocus());
      if (unfocused) {
        try {
          new notifier("Symphony coordinator out of usage", {body: message, tag: "symphony-usage-alert"});
        } catch {
          // Ignore browsers that block constructing Notification directly.
        }
      }
    }
  } else {
    enableButton.hidden = true;
  }
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
  const pollProject = selected;
  const pollRepo = selectedRepo;
  let delay = 3000;
  try {
    const state = await api(
      "/api/state?after=" + lastMessage
      + "&issues=" + encodeURIComponent(issueRevision)
      + "&project=" + encodeURIComponent(pollProject)
    );
    if (selectedRepo !== null && selectedRepo !== pollRepo) {
      pollAgain = true;
      return;
    }
    const activeRepo = typeof state.repo === "string" ? state.repo : (selectedRepo || "");
    if (selectedRepo === null && typeof state.repo === "string") selectedRepo = state.repo;
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
    syncApproveButtons();
    if (state.projects) {
      if (!issueRevision) selected = state.view;
      updateProjects(state.projects, redrawIssues);
      issueRevision = state.issueRevision;
    } else if (redrawIssues) {
      renderPanes();
    }
    updateRepoActivity(state.repoActivity, selectedRepo ?? activeRepo);
    renderRepoButtons(state.buttons || [], selectedRepo ?? activeRepo);
    renderDiagramPanel(state.diagram || null, selectedRepo ?? activeRepo);
    const notice = byId("project-error");
    if (notice.textContent !== state.issueError) notice.textContent = state.issueError;
    notice.hidden = !state.issueError;
    codex = state.codex;
    canInterrupt = state.canInterrupt;
    compacting = typeof state.compacting === "string" ? state.compacting : "";
    updateState = {
      updateAvailable: Boolean(state.updateAvailable),
      draining: Boolean(state.draining),
      update: state.update || null,
    };
    if (choicesChanged || tasksChanged) renderCommands();
    showCodexStatus();
    renderUpdateNotice();
    renderUsageAlert(state.usageAlert || null);
    byId("connection-error").hidden = true;
    byId("paused").hidden = !state.paused;
    byId("paused").textContent = state.paused ? "Dispatch paused. " + state.paused : "";
    showLogin(state);
    const nextAuthKey = JSON.stringify([Boolean(state.signedIn), state.login?.url || "", state.login?.code || ""]);
    if (nextAuthKey !== configAuthKey) {
      configAuthKey = nextAuthKey;
      if (configDialog.open) loadConfigState();
    } else if (!state.signedIn && configAuthKey === "") {
      configAuthKey = nextAuthKey;
    }
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
  ["/new", "Start a fresh session for this repository"],
  ["/compact", "Compact the conversation context"],
  ["/status", "Show Codex usage and approved work"],
  ["/mcp", "Show all coordinator and MCP tools"],
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
  const repo = repoForProject(project);
  const attachedPrompts = !text.trim().startsWith("/") ? armedButtonsFor(repo) : [];
  if (sending || (!text.trim() && !pendingImages.length && !attachedPrompts.length)) return;
  if (pendingImages.length && text.trim().startsWith("/")) {
    setComposerError("Images cannot be sent with slash commands.");
    return;
  }
  const outgoing = attachedPrompts.length
    ? attachedPrompts.map((btn) => btn.prompt).concat(text.trim() ? [text] : []).join("\n\n")
    : text;
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
  const result = await submit(outgoing, images, project);
  if (result.ok) {
    if (text.trim()) {
      history.push(text);
      if (history.length > 100) history.shift();
      historyIndex = history.length;
    }
    if (attachedPrompts.length) {
      const set = armedButtons.get(repo);
      for (const btn of attachedPrompts) set?.delete(btn.label);
      renderRepoButtons(repoButtons, selectedRepo ?? repo);
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
byId("diagram-close").addEventListener("click", () => {
  if (currentDiagram) dismissedDiagrams.set(currentDiagram.repo, currentDiagram.updated);
  clearDiagramPanel(true);
});
byId("update-action").addEventListener("click", toggleDrain);
byId("usage-notify-enable").addEventListener("click", async () => {
  const notifier = globalThis.Notification;
  if (typeof notifier !== "function" || typeof notifier.requestPermission !== "function") return;
  try {
    await notifier.requestPermission();
  } finally {
    renderUsageAlert(lastUsageAlert);
  }
});
configButton.addEventListener("click", openConfig);
byId("config-close").addEventListener("click", closeConfig);
configOpenCodeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  mutateConfig(
    {action: "set_opencode_key", apiKey: configOpenCodeKey.value},
    configOpenCodeFeedback,
    "OpenCode Go API key saved.",
    true,
  );
});
configOpenCodeClear.addEventListener("click", () => {
  mutateConfig({action: "clear_opencode_key"}, configOpenCodeFeedback, "OpenCode Go API key removed.", true);
});
configCodexSignin.addEventListener("click", () => {
  mutateConfig({action: "codex_login"}, configCodexFeedback, "Device sign-in ready.");
});
configCodexCancel.addEventListener("click", () => {
  mutateConfig({action: "codex_cancel_login"}, configCodexFeedback, "Device sign-in canceled.");
});
configCodexSignout.addEventListener("click", () => {
  mutateConfig({action: "codex_logout"}, configCodexFeedback, "Signed out of Codex.");
});
configDialog.addEventListener("keydown", (event) => {
  if (event.key !== "Tab" || event.ctrlKey || event.altKey || event.metaKey) return;
  const controls = [...configDialog.querySelectorAll(
    "button:not([disabled]), input:not([disabled]), a[href], [tabindex='0']"
  )].filter((control) => control.checkVisibility());
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});
configDialog.addEventListener("close", () => {
  if (configDialog.open) return;
  configRequest++;
  configDataRequest++;
  configOpenCodeKey.value = "";
  configButton.setAttribute("aria-expanded", "false");
  configButton.focus({preventScroll: true});
});
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
