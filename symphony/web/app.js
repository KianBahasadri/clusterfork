const byId = (id) => document.getElementById(id);
const repos = byId("repos");
const issues = byId("issues");
const messages = byId("messages");
const chatLog = byId("chat-log");
const messageField = byId("message");
let selected = "";
let lastMessage = 0;
let tasks = [];
let issueRows = [];
let issueRequest = 0;
let initializing = true;
let stream = "";

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
  const expanded = new Set([...issues.querySelectorAll("details[open]")].map((item) => item.dataset.issue));
  const fragment = document.createDocumentFragment();
  if (!selected) {
    fragment.append(element("p", "Select a repository to browse its issues.", "muted"));
    for (const task of tasks) {
      const row = element("div", "", "issue");
      row.append(element("strong", task.issue), element("span", task.repo, "muted"),
                 element("span", task.phase.replaceAll("_", " "), "state"));
      if (task.detail) row.append(element("p", task.detail, "detail"));
      fragment.append(row);
    }
  }
  for (const issue of issueRows) {
    const row = element("article", "", "issue");
    const title = element("a", issue.identifier, "issue-link");
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
      plan.dataset.issue = issue.identifier;
      plan.open = expanded.has(issue.identifier);
      plan.append(element("summary", "Proposed plan"), element("div", issue.plan, "plan"));
      if (!task) {
        plan.append(element("p", "Tell the coordinator in chat when you approve this plan.", "muted"));
      }
      row.append(plan);
    }
    fragment.append(row);
  }
  if (selected && !issueRows.length) fragment.append(element("p", "No open issues for this repository.", "muted"));
  issues.replaceChildren(fragment);
}

async function loadIssues() {
  const request = ++issueRequest;
  if (!selected) {
    issueRows = [];
    renderIssues();
    return;
  }
  try {
    const rows = await api("/api/issues?repo=" + encodeURIComponent(selected));
    if (request !== issueRequest) return;
    issueRows = rows;
    renderIssues();
  } catch (error) {
    if (request === issueRequest) issues.replaceChildren(element("p", error.message, "detail"));
  }
}

async function loadProjects() {
  try {
    const projects = await api("/api/projects");
    const choices = [{label: "All repositories", repo: ""}, ...projects].map((row) => {
      const tab = element("button", row.label);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", "issues");
      tab.dataset.repo = row.repo;
      tab.addEventListener("click", () => selectRepo(row.repo));
      return tab;
    });
    repos.replaceChildren(...choices);
    await selectRepo(choices.some((tab) => tab.dataset.repo === selected) ? selected : "");
  } catch (error) {
    issues.replaceChildren(element("p", error.message, "detail"));
  }
}

function selectRepo(repo) {
  if (selected !== repo) issueRows = [];
  selected = repo;
  for (const tab of repos.children) {
    const active = tab.dataset.repo === repo;
    tab.setAttribute("aria-selected", active);
    tab.tabIndex = active ? 0 : -1;
  }
  return loadIssues();
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
    if (JSON.stringify(tasks) !== JSON.stringify(state.tasks)) {
      tasks = state.tasks;
      renderIssues();
    }
    byId("interrupt").hidden = !state.canInterrupt;
    byId("connection").textContent = state.busy ? "Coordinator working" : "Connected";
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

async function submit(text, keepDraft = true) {
  if (!text.trim()) return;
  const errorField = byId("request-error");
  errorField.textContent = "";
  try {
    await api("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Symphony": "1"},
      body: JSON.stringify({text, repo: selected}),
    });
    if (!keepDraft && messageField.value === text) messageField.value = "";
    chatLog.scrollTop = chatLog.scrollHeight;
  } catch (error) {
    errorField.textContent = error.message;
  }
}

repos.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const tabs = [...repos.children];
  const offset = event.key === "ArrowLeft" ? -1 : 1;
  const next = tabs[(tabs.indexOf(event.target) + offset + tabs.length) % tabs.length];
  event.preventDefault();
  next.focus();
  next.click();
});
byId("refresh").addEventListener("click", loadProjects);
byId("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  submit(messageField.value, false);
});
byId("interrupt").addEventListener("click", () => submit("/interrupt"));
messageField.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    byId("composer").requestSubmit();
  }
});
const commandList = byId("command-list");
for (const command of ["/compact", "/status", "/model ", "/effort ", "/continue", "/retry "]) {
  const button = element("button", command);
  button.type = "button";
  button.addEventListener("click", () => {
    messageField.value = command;
    messageField.focus();
    commandList.hidden = true;
  });
  commandList.append(button);
}
byId("commands").addEventListener("click", () => { commandList.hidden = !commandList.hidden; });
setInterval(() => { if (selected) loadIssues(); }, 30000);
poll();
