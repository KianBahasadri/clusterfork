const byId = (id) => document.getElementById(id);
const repos = byId("repos");
const issues = byId("issues");
const sidebar = issues.closest("aside");
const messages = byId("messages");
const chatLog = byId("chat-log");
const composer = byId("composer");
const messageField = byId("message");
const commandList = byId("command-list");
const issueCache = new Map();
const issueScroll = new Map();
const expandedPlans = new Set();
let selected = "";
let lastMessage = 0;
let tasks = [];
let projectsKey = "";
let refreshing = false;
let initializing = true;
let stream = "";
let codex = {};
let canInterrupt = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

function renderIssues() {
  for (const plan of issues.querySelectorAll("details")) {
    if (plan.open) expandedPlans.add(plan.dataset.issue);
    else expandedPlans.delete(plan.dataset.issue);
  }
  const scroll = sidebar.scrollTop;
  const focused = issues.contains(document.activeElement) ? document.activeElement.id : "";
  const fragment = document.createDocumentFragment();
  const cached = issueCache.get(selected);
  const rows = cached?.rows;
  if (!selected) {
    fragment.append(element("p", "Select a repository to browse its issues.", "muted"));
    for (const task of tasks) {
      const row = element("div", "", "issue");
      row.append(element("strong", task.issue), element("span", task.repo, "muted"),
                 element("span", task.phase.replaceAll("_", " "), "state"));
      if (task.detail) row.append(element("p", task.detail, "detail"));
      fragment.append(row);
    }
  } else if (cached?.error) {
    fragment.append(element("p", cached.error, "detail"));
  }
  for (const issue of rows || []) {
    const row = element("article", "", "issue");
    const title = element("a", issue.identifier, "issue-link");
    title.id = "issue-link-" + issue.identifier;
    const url = new URL(issue.url);
    if (url.protocol === "https:" && url.hostname === "linear.app") {
      title.href = url.href;
      title.target = "_blank";
      title.rel = "noreferrer";
    }
    const task = tasks.find((item) => item.issue === issue.identifier);
    row.append(title, element("span", (task?.phase || issue.state.name).replaceAll("_", " "), "state"),
               element("p", issue.title, "issue-title"));
    if (task?.detail) row.append(element("p", task.detail, "detail"));
    if (issue.plan) {
      const plan = element("details");
      const summary = element("summary", "Proposed plan");
      summary.id = "plan-summary-" + issue.identifier;
      plan.dataset.issue = issue.identifier;
      plan.open = expandedPlans.has(issue.identifier);
      plan.append(summary, element("div", issue.plan, "plan"));
      if (!task) {
        plan.append(element("p", "Tell the coordinator in chat when you approve this plan.", "muted"));
      }
      row.append(plan);
    }
    fragment.append(row);
  }
  if (selected && rows?.length === 0) {
    fragment.append(element("p", "No open issues for this repository.", "muted"));
  } else if (selected && !cached) {
    fragment.append(element("p", "Loading issues…", "muted"));
  }
  issues.replaceChildren(fragment);
  sidebar.scrollTop = scroll;
  if (focused) byId(focused)?.focus({preventScroll: true});
}

async function prefetchIssues(repo) {
  const previous = issueCache.get(repo);
  let next;
  try {
    next = {rows: await api("/api/issues?repo=" + encodeURIComponent(repo))};
  } catch (error) {
    next = {rows: previous?.rows, error: error.message};
  }
  if (JSON.stringify(previous) !== JSON.stringify(next)) {
    issueCache.set(repo, next);
    if (repo === selected) renderIssues();
  }
}

async function loadProjects() {
  if (refreshing) return;
  refreshing = true;
  const notice = byId("project-error");
  try {
    const projects = await api("/api/projects");
    notice.hidden = true;
    const key = JSON.stringify(projects);
    if (key !== projectsKey) {
      projectsKey = key;
      const focused = repos.contains(document.activeElement);
      const choices = [{label: "All repositories", repo: ""}, ...projects].map((row, index) => {
        const tab = element("button", row.label);
        tab.id = "repo-tab-" + index;
        tab.type = "button";
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", "issues");
        tab.dataset.repo = row.repo;
        tab.addEventListener("click", () => selectRepo(row.repo));
        return tab;
      });
      repos.replaceChildren(...choices);
      selectRepo(projects.some((project) => project.repo === selected) ? selected : "");
      if (focused) repos.querySelector('[aria-selected="true"]').focus({preventScroll: true});
    }
    await Promise.all(projects.map(({repo}) => prefetchIssues(repo)));
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
  } finally {
    refreshing = false;
  }
}

function selectRepo(repo) {
  issueScroll.set(selected, sidebar.scrollTop);
  selected = repo;
  for (const tab of repos.children) {
    const active = tab.dataset.repo === repo;
    tab.setAttribute("aria-selected", active);
    tab.tabIndex = active ? 0 : -1;
    if (active) issues.setAttribute("aria-labelledby", tab.id);
  }
  renderIssues();
  sidebar.scrollTop = issueScroll.get(repo) || 0;
}

function appendMessage(role, text) {
  const label = {user: "You", assistant: "Coordinator", system: "Symphony", worker: "Worker"};
  const entry = element("article", "", "message " + role);
  entry.append(element("h2", label[role] || "Symphony"), element("div", text, "message-text"));
  messages.append(entry);
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
  byId("composer-shortcuts").textContent = canInterrupt
    ? "Enter to send · Esc to interrupt" : "Enter to send · Shift+Enter for a new line";
}

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
}

async function poll() {
  let delay = 3000;
  try {
    const state = await api("/api/state?after=" + lastMessage);
    if (initializing) {
      initializing = false;
      selected = state.view;
      loadProjects();
    }
    const pinned = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80;
    for (const item of state.messages) {
      appendMessage(item.role, item.text);
      lastMessage = item.id;
    }
    byId("welcome").hidden = Boolean(lastMessage);
    if (stream !== state.stream) {
      stream = state.stream;
      byId("stream").textContent = stream;
    }
    if (pinned) chatLog.scrollTop = chatLog.scrollHeight;
    const tasksChanged = JSON.stringify(tasks) !== JSON.stringify(state.tasks);
    const choicesChanged = JSON.stringify([codex.models, codex.efforts])
      !== JSON.stringify([state.codex.models, state.codex.efforts]);
    tasks = state.tasks;
    if (tasksChanged) renderIssues();
    codex = state.codex;
    canInterrupt = state.canInterrupt;
    if (choicesChanged || tasksChanged) renderCommands();
    showCodexStatus();
    byId("connection").textContent = "Connected";
    byId("paused").hidden = !state.paused;
    byId("paused").textContent = state.paused ? "Dispatch paused. " + state.paused : "";
    showLogin(state);
    delay = state.messages.length === 200 ? 0 : state.busy ? 600 : 2000;
  } catch {
    byId("connection").textContent = "Reconnecting…";
  } finally {
    setTimeout(poll, delay);
  }
}

async function submit(text) {
  const errorField = byId("request-error");
  errorField.textContent = "";
  try {
    await api("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Symphony": "1"},
      body: JSON.stringify({text, repo: selected}),
    });
    chatLog.scrollTop = chatLog.scrollHeight;
    return true;
  } catch (error) {
    errorField.textContent = error.message;
    return false;
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
byId("refresh").addEventListener("click", loadProjects);

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
  if (sending || !text.trim()) return;
  sending = true;
  writeDraft("");
  if (await submit(text)) {
    history.push(text);
    if (history.length > 100) history.shift();
    historyIndex = history.length;
  } else if (!messageField.value) {
    writeDraft(text);
  }
  sending = false;
});

messageField.addEventListener("input", () => {
  historyIndex = history.length;
  dismissedDraft = null;
  renderCommands();
});
messageField.addEventListener("focus", renderCommands);
messageField.addEventListener("blur", hideCommands);
messageField.addEventListener("keydown", (event) => {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.repeat && (event.key === "Escape" || (event.key === "Enter" && !event.shiftKey))) {
    event.preventDefault();
    return;
  }
  if (event.key === "Escape") {
    if (!commandList.hidden) {
      dismissedDraft = messageField.value;
      hideCommands();
    } else if (canInterrupt) {
      submit("/interrupt");
    } else {
      return;
    }
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
setInterval(loadProjects, 30000);
poll();
