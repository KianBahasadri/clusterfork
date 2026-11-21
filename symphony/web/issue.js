const issueDialog = byId("issue-dialog");
const issueContent = byId("issue-content");
const issueTitle = byId("issue-dialog-title");
const detailCache = new Map();
const detailPreloadQueue = new Set();
let preloadingDetails = false;
let detailRequest = 0;
let invokingIssue = "";
let currentIssue = null;
let activeIssueTab = "issue";
let executionTimer = 0;
let executionRequest = 0;

function issueContextPath(identifier) {
  const params = new URLSearchParams({id: identifier});
  const issue = issueCache.get("")?.find((row) => row.identifier === identifier);
  if (issue) params.set("updated", issue.updatedAt);
  return "/api/issue?" + params;
}

function loadIssueDetails(identifier) {
  const path = issueContextPath(identifier);
  if (!detailCache.has(path)) {
    const request = api(path).catch((error) => {
      detailCache.delete(path);
      throw error;
    });
    detailCache.set(path, request);
  }
  return detailCache.get(path);
}

async function preloadIssueDetails(rows) {
  for (const issue of rows) {
    if (!detailCache.has(issueContextPath(issue.identifier))) detailPreloadQueue.add(issue.identifier);
  }
  if (preloadingDetails) return;
  preloadingDetails = true;
  async function preload() {
    for (const identifier of detailPreloadQueue) {
      detailPreloadQueue.delete(identifier);
      await loadIssueDetails(identifier).catch(() => {});
    }
  }
  await Promise.all([preload(), preload()]);
  preloadingDetails = false;
}

function issueLink(text, url) {
  const link = element("a", text);
  const target = new URL(url, location.origin);
  if (["https:", "http:"].includes(target.protocol)) {
    link.href = target.href;
    link.target = "_blank";
    link.rel = "noreferrer";
  }
  return link;
}

function issueMarkdown(html) {
  const body = element("div", "", "issue-markdown");
  body.innerHTML = html;
  for (const link of body.querySelectorAll("a")) {
    link.target = "_blank";
    link.rel = "noreferrer";
    if (!URL.canParse(link.href)) continue;
    const target = new URL(link.href);
    const identifier = target.hostname === "linear.app" && target.pathname.match(/\/issue\/([A-Z][A-Z0-9]*-[0-9]+)(?:\/|$)/)?.[1];
    if (identifier) link.addEventListener("click", (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      openIssue(identifier);
    });
  }
  for (const image of body.querySelectorAll("img")) image.loading = "lazy";
  return body;
}

function issueTime(value) {
  const time = element("time");
  time.dateTime = value;
  time.textContent = new Date(value).toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
  return time;
}

function personName(person) {
  return person?.displayName || person?.name || "Unassigned";
}

function issuePerson(person) {
  const row = element("span", "", "issue-person");
  if (person?.avatarUrl) {
    const image = element("img");
    image.src = person.avatarUrl;
    image.alt = "";
    image.width = 20;
    image.height = 20;
    image.loading = "lazy";
    row.append(image);
  }
  row.append(element("span", personName(person)));
  return row;
}

function detailSection(parent, title) {
  const section = element("section", "", "issue-detail-section");
  section.append(element("h3", title));
  parent.append(section);
  return section;
}

function issueReference(issue) {
  const button = element("button", "", "issue-reference");
  button.type = "button";
  button.append(stateIcon(issue.state),
    element("span", issue.identifier, "issue-link"), element("span", issue.title));
  button.addEventListener("click", () => openIssue(issue.identifier));
  return button;
}

function showReactions(parent, reactions) {
  if (!reactions.length) return;
  const row = element("div", "", "issue-meta");
  for (const [emoji, group] of Object.entries(Object.groupBy(reactions, (reaction) => reaction.emoji))) {
    row.append(issuePill(emoji + " " + group.length,
      group.map((reaction) => personName(reaction.user || reaction.externalUser)).join(", ")));
  }
  parent.append(row);
}

function historyValue(value, field) {
  if (value == null) return "none";
  if (field === "Priority") return ["No priority", "Urgent", "High", "Medium", "Low"][value];
  if (typeof value === "object") return value.name || value.identifier || "Cycle " + value.number;
  return String(value);
}

function historyDescription(entry) {
  const changes = [];
  const fields = {Title: "title", Assignee: "assignee", Delegate: "agent", Priority: "priority",
    Estimate: "estimate", Team: "team", Parent: "parent", State: "status", Cycle: "cycle",
    Project: "project", ProjectMilestone: "milestone", DueDate: "due date",
    SlaStartedAt: "SLA start", SlaBreachesAt: "SLA deadline", SlaType: "SLA type"};
  for (const [field, label] of Object.entries(fields)) {
    const from = entry["from" + field];
    const to = entry["to" + field];
    if (from != null || to != null) {
      changes.push("Changed " + label + " from " + historyValue(from, field) + " to " + historyValue(to, field));
    }
  }
  if (entry.updatedDescription) changes.push("Updated the description");
  if (entry.addedLabels?.length) changes.push("Added labels: " + entry.addedLabels.map((label) => label.name).join(", "));
  if (entry.removedLabels?.length) changes.push("Removed labels: " + entry.removedLabels.map((label) => label.name).join(", "));
  if (entry.attachment) changes.push("Updated attachment: " + entry.attachment.title);
  for (const relation of entry.relationChanges || []) changes.push(relation.type + ": " + relation.identifier);
  if (entry.archived != null) changes.push(entry.archived ? "Archived the issue" : "Unarchived the issue");
  if (entry.trashed != null) changes.push(entry.trashed ? "Deleted the issue" : "Restored the issue");
  if (entry.autoClosed) changes.push("Automatically closed");
  if (entry.autoArchived) changes.push("Automatically archived");
  return changes.join(" · ") || "Updated the issue";
}

function issueLayout(issue) {
  const layout = element("div", "", "issue-detail-layout");
  const body = element("div", "", "issue-detail-main");
  const properties = element("div", "", "issue-properties");
  const values = detailSection(properties, "Properties");
  const status = element("p", "", "issue-property");
  status.append(stateIcon(issue.state), element("span", issue.state.name));
  values.append(status, issuePill(issue.priorityLabel, issue.priorityLabel, priorityIcons[issue.priority]),
    issuePerson(issue.assignee));
  if (issue.delegate) values.append(issuePerson(issue.delegate));
  if (issue.estimate != null) values.append(issuePill(issue.estimate + " Points", "Estimate", "triangle"));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const deadline = issueDeadline(issue.dueDate, today);
  if (deadline) values.append(deadline);
  if (issue.labels.length) {
    const labels = detailSection(properties, "Labels");
    const pills = element("div", "", "issue-meta");
    for (const label of issue.labels) {
      const pill = issuePill(label.name, label.name);
      const dot = element("span", "", "label-dot");
      dot.style.backgroundColor = label.color;
      pill.prepend(dot);
      pills.append(pill);
    }
    labels.append(pills);
  }
  if (issue.project) {
    const project = detailSection(properties, "Project");
    const link = issueLink("", issue.project.url);
    link.append(projectLabel(issue.project));
    project.append(link);
  }
  detailSection(properties, "Team").append(element("p", issue.team.name));
  if (issue.cycle) {
    const cycle = detailSection(properties, "Cycle");
    cycle.append(element("p", issue.cycle.name || "Cycle " + issue.cycle.number),
      element("p", new Date(issue.cycle.startsAt).toLocaleDateString() + " – "
        + new Date(issue.cycle.endsAt).toLocaleDateString(), "muted"));
  }
  if (issue.projectMilestone) {
    const milestone = detailSection(properties, "Milestone");
    milestone.append(element("p", issue.projectMilestone.name));
    if (issue.projectMilestone.targetDate) milestone.append(element("p", issue.projectMilestone.targetDate, "muted"));
    if (issue.projectMilestone.description) milestone.append(element("p", issue.projectMilestone.description));
  }
  if (issue.parent) detailSection(properties, "Parent issue").append(issueReference(issue.parent));
  if (issue.children.length) detailSection(properties, "Sub-issues").append(...issue.children.map(issueReference));
  const relations = new Map();
  for (const relation of [...issue.relations, ...issue.inverseRelations]) {
    const outgoing = relation.issue.identifier === issue.identifier;
    const other = outgoing ? relation.relatedIssue : relation.issue;
    const names = {blocks: outgoing ? "Blocking" : "Blocked by", related: "Related", duplicate: "Duplicates"};
    const title = names[relation.type] || relation.type.replaceAll("_", " ");
    if (!relations.has(title)) relations.set(title, new Map());
    relations.get(title).set(other.identifier, other);
  }
  for (const [title, related] of relations) {
    detailSection(properties, title).append(...[...related.values()].map(issueReference));
  }
  if (issue.releases.length) {
    const releases = detailSection(properties, "Releases");
    for (const release of issue.releases) releases.append(issueLink(release.name, release.url));
  }
  if (issue.subscribers.length) {
    detailSection(properties, "Subscribers").append(...issue.subscribers.map(issuePerson));
  }
  const dates = detailSection(properties, "Issue information");
  dates.append(element("p", "Created by " + personName(issue.creator), "muted"));
  const timestamps = {createdAt: "Created", updatedAt: "Updated", startedAt: "Started",
    completedAt: "Completed", canceledAt: "Canceled", archivedAt: "Archived",
    snoozedUntilAt: "Snoozed until", slaStartedAt: "SLA started", slaBreachesAt: "SLA deadline"};
  for (const [key, label] of Object.entries(timestamps)) {
    if (!issue[key]) continue;
    const row = element("p", label + " ", "muted");
    row.append(issueTime(issue[key]));
    dates.append(row);
  }
  if (issue.slaType) dates.append(element("p", "SLA: " + issue.slaType, "muted"));
  if (issue.branchName) dates.append(element("code", issue.branchName));
  if (issue.previousIdentifiers.length) dates.append(element("p", "Previously " + issue.previousIdentifiers.join(", "), "muted"));

  body.append(issue.descriptionHtml ? issueMarkdown(issue.descriptionHtml) : element("p", "No description.", "muted"));
  showReactions(body, issue.reactions);
  if (issue.attachments.length) {
    const resources = detailSection(body, "Attachments");
    for (const attachment of issue.attachments) {
      const row = element("article", "", "issue-resource");
      row.append(issueLink(attachment.title, attachment.url));
      if (attachment.subtitle) row.append(element("p", attachment.subtitle, "muted"));
      const author = attachment.creator || attachment.externalUserCreator;
      if (author) row.append(element("p", personName(author), "muted"));
      if (attachment.createdAt) row.append(issueTime(attachment.createdAt));
      resources.append(row);
    }
  }
  if (issue.documents.length) {
    const documents = detailSection(body, "Documents");
    for (const document of issue.documents) {
      const entry = element("details");
      entry.append(element("summary", document.title), issueMarkdown(document.contentHtml),
        issueLink("Open document in Linear", document.url));
      documents.append(entry);
    }
  }
  const comments = detailSection(body, "Comments");
  if (!issue.comments.length) comments.append(element("p", "No comments yet.", "muted"));
  const orderedComments = issue.comments.toSorted((a, b) => a.createdAt.localeCompare(b.createdAt));
  for (const comment of orderedComments) {
    const entry = element("article", "", "issue-comment");
    const heading = element("div", "", "issue-comment-heading");
    heading.append(issuePerson(comment.user || comment.externalUser || comment.botActor),
      issueLink("", comment.url));
    heading.lastChild.append(issueTime(comment.createdAt));
    entry.append(heading);
    if (comment.parentId) {
      const parent = issue.comments.find((item) => item.id === comment.parentId);
      entry.append(element("p", "Reply to " + personName(parent?.user || parent?.externalUser || parent?.botActor), "muted"));
    }
    if (comment.quotedText) entry.append(element("blockquote", comment.quotedText));
    entry.append(issueMarkdown(comment.bodyHtml));
    if (comment.editedAt) entry.append(element("p", "Edited " + new Date(comment.editedAt).toLocaleString(), "muted"));
    if (comment.resolvedAt) entry.append(element("p", "Resolved by " + personName(comment.resolvingUser), "muted"));
    showReactions(entry, comment.reactions);
    comments.append(entry);
  }
  const history = element("details", "", "issue-history");
  history.append(element("summary", "Activity · " + issue.history.length));
  const events = element("ol");
  for (const entry of issue.history.toSorted((a, b) => b.createdAt.localeCompare(a.createdAt))) {
    const event = element("li");
    event.append(element("p", personName(entry.actor || entry.botActor) + " · " + historyDescription(entry)), issueTime(entry.createdAt));
    events.append(event);
  }
  history.append(events);
  body.append(history);
  layout.append(body, properties);
  return layout;
}

function stopExecutionRefresh() {
  clearInterval(executionTimer);
  executionTimer = 0;
}

function selectIssueTab(name, focus = false) {
  activeIssueTab = name;
  for (const tab of issueContent.querySelectorAll(".issue-tab")) {
    const active = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", active);
    tab.tabIndex = active ? 0 : -1;
    if (active && focus) tab.focus();
  }
  for (const panel of issueContent.querySelectorAll(".issue-tab-panel")) {
    panel.hidden = panel.dataset.panel !== name;
  }
  stopExecutionRefresh();
  if (!executionNeedsLoad(name, currentIssue)) return;
  loadExecutionPanel(currentIssue.identifier);
  executionTimer = setInterval(() => {
    if (executionShouldRefresh(issueDialog.open, activeIssueTab, currentIssue)) {
      loadExecutionPanel(currentIssue.identifier);
    }
  }, 15000);
}

function renderIssueContext(issue) {
  currentIssue = issue;
  byId("issue-dialog-identifier").textContent = issue.identifier;
  issueTitle.textContent = issue.title;
  const source = byId("issue-source");
  source.href = issue.url;
  source.hidden = false;

  const tabs = element("div", "", "issue-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Issue details");
  const issueTab = element("button", "Issue", "issue-tab");
  issueTab.type = "button";
  issueTab.id = "issue-tab-issue";
  issueTab.dataset.tab = "issue";
  issueTab.setAttribute("role", "tab");
  issueTab.setAttribute("aria-controls", "issue-panel-issue");
  issueTab.addEventListener("click", () => selectIssueTab("issue"));
  const executionTab = element("button", "Execution", "issue-tab");
  executionTab.type = "button";
  executionTab.id = "issue-tab-execution";
  executionTab.dataset.tab = "execution";
  executionTab.setAttribute("role", "tab");
  executionTab.setAttribute("aria-controls", "issue-panel-execution");
  executionTab.addEventListener("click", () => selectIssueTab("execution"));
  tabs.append(issueTab, executionTab);
  tabs.addEventListener("keydown", (event) => {
    const items = [...tabs.querySelectorAll('[role="tab"]')];
    const next = nextIssueTab(items.map((item) => item.dataset.tab),
      document.activeElement?.dataset?.tab, event.key);
    if (!next) return;
    event.preventDefault();
    selectIssueTab(next, true);
  });

  const issuePanel = element("div", "", "issue-tab-panel");
  issuePanel.id = "issue-panel-issue";
  issuePanel.dataset.panel = "issue";
  issuePanel.setAttribute("role", "tabpanel");
  issuePanel.setAttribute("aria-labelledby", "issue-tab-issue");
  issuePanel.append(issueLayout(issue));
  const executionPanel = element("div", "", "issue-tab-panel");
  executionPanel.id = "issue-panel-execution";
  executionPanel.dataset.panel = "execution";
  executionPanel.setAttribute("role", "tabpanel");
  executionPanel.setAttribute("aria-labelledby", "issue-tab-execution");
  executionPanel.hidden = true;
  issueContent.replaceChildren(issueTitle, tabs, issuePanel, executionPanel);
  selectIssueTab("issue");
}

function executionSection(parent, title) {
  const section = element("section", "", "execution-section");
  section.append(element("h3", title));
  parent.append(section);
  return section;
}

function executionMetric(label, value, detail) {
  const item = element("div", "", "execution-metric");
  item.append(element("span", label, "execution-metric-label"),
    element("strong", value), element("span", detail || "", "muted"));
  return item;
}

function executionCoverage(value, reason) {
  return element("p", value + (reason ? " · " + reason : ""), "muted execution-coverage");
}

function executionKind(kind) {
  const labels = {
    attempt: "Worker attempt", startup: "Worker startup", worker: "Worker run",
    verification: "Verification", coordinator: "Coordinator", publication: "Publication",
    ci_wait: "CI waiting", ci_discovery: "CI discovery", ci_run: "CI run",
    ci_error: "CI detail failure", approval: "Approval", failure: "Failure",
    completion: "Completion", interruption: "Restart", gap: "Unobserved outage",
  };
  return labels[kind] || kind.replaceAll("_", " ");
}

function renderExecutionStages(panel, stages) {
  const section = executionSection(panel, "Linear stages");
  section.append(executionCoverage(stages.coverage, stages.reason));
  const grid = element("div", "", "execution-metrics");
  for (const stage of stages.stages) {
    const detail = stages.coverage === "complete" ? stage.visits + " visit" + (stage.visits === 1 ? "" : "s")
      + (stage.open ? " · open" : "") : "History incomplete";
    grid.append(executionMetric(stage.name,
      stages.coverage === "complete" ? formatSeconds(stage.seconds) : "Unavailable", detail));
  }
  section.append(grid);
  if (stages.current) {
    const current = element("p", "Current stage: " + stages.current.name + " since ", "muted");
    current.append(issueTime(stages.current.startedAt),
      document.createTextNode(" (" + stages.current.source + ")."));
    section.append(current);
  }
  if (stages.transitions.length) {
    const transitions = element("details", "", "execution-transitions");
    transitions.append(element("summary", "Status transitions · " + stages.transitions.length));
    const list = element("ol");
    for (const entry of stages.transitions) {
      const item = element("li");
      item.append(issueTime(entry.at), document.createTextNode(
        " · " + (entry.from?.name || "Unknown") + " → " + (entry.to?.name || "Unknown")));
      list.append(item);
    }
    transitions.append(list);
    section.append(transitions);
  }
}

function renderExecutionBreakdown(panel, execution) {
  const section = executionSection(panel, "In Progress breakdown");
  section.append(executionCoverage(execution.coverage, execution.reason));
  const unavailable = execution.coverage === "unavailable";
  const grid = element("div", "", "execution-metrics");
  const order = ["startup", "worker", "verification", "coordinator", "publication",
    "ci_workflow", "ci_wait", "ci_discovery"];
  for (const kind of order) {
    const value = execution.breakdown[kind];
    grid.append(executionMetric(executionKind(kind),
      unavailable ? "Unavailable" : formatSeconds(value),
      value == null ? "Not recorded"
        : kind === "ci_workflow" ? "Sum of workflow runs; may overlap"
          : kind === "ci_wait" ? "Waiting while CI ran" : "Recorded interval"));
  }
  grid.append(executionMetric("Measured total",
    unavailable ? "Unavailable" : formatSeconds(execution.measuredSeconds),
    execution.lowerBound ? "Lower bound; some categories were not recorded" : "Sum of recorded intervals"));
  grid.append(executionMetric("Concurrent wall time",
    unavailable ? "Unavailable" : formatSeconds(execution.wallSeconds),
    execution.lowerBound ? "Lower bound; some categories were not recorded" : "Union of recorded intervals"));
  grid.append(executionMetric("Overlap",
    unavailable ? "Unavailable" : formatSeconds(execution.overlapSeconds),
    execution.lowerBound ? "Lower bound; some categories were not recorded" : "Measured minus wall time"));
  section.append(grid);
}

function renderExecutionCi(panel, ci) {
  const section = executionSection(panel, "GitHub CI");
  section.append(executionCoverage(ci.coverage, ci.reason));
  const unavailable = ci.coverage === "unavailable";
  const grid = element("div", "", "execution-metrics");
  grid.append(
    executionMetric("Workflow total", unavailable ? "Unavailable" : formatSeconds(ci.workflowSeconds),
      ci.lowerBound ? "Lower bound; some boundaries were not observed" : "Sum of retained run durations"),
    executionMetric("Concurrent wall", unavailable ? "Unavailable" : formatSeconds(ci.wallSeconds),
      ci.lowerBound ? "Lower bound; some boundaries were not observed" : "Union of retained run durations"),
    executionMetric("Job total", formatSeconds(ci.jobSeconds),
      ci.jobLowerBound ? "Lower bound; job details incomplete" : "Sum of retained job durations"),
    executionMetric("Job wall", formatSeconds(ci.jobWallSeconds),
      ci.jobLowerBound ? "Lower bound; job details incomplete" : "Union of retained job durations"),
    executionMetric("Waiting", unavailable ? "Unavailable" : formatSeconds(ci.waitingSeconds),
      "CI watcher waiting"),
    executionMetric("Attempts", unavailable ? "Unavailable" : String(ci.attemptCount), "Retained run attempts"),
    executionMetric("Failures", unavailable ? "Unavailable" : String(ci.failureCount),
      "Retained non-success conclusions"),
    executionMetric("Jobs", unavailable ? "Unavailable" : String(ci.jobCount), "Recorded job identities"),
  );
  section.append(grid);
  for (const run of ci.runs) {
    const article = element("article", "", "execution-resource");
    const heading = element("div", "", "execution-resource-heading");
    heading.append(element("strong", run.workflow),
      element("span", "attempt " + run.attempt + " · " + run.status
        + (run.conclusion ? " · " + run.conclusion : ""), "muted"));
    article.append(heading);
    if (run.attemptUrl || run.url) article.append(issueLink("Open run in GitHub", run.attemptUrl || run.url));
    if (run.diagnostic) article.append(element("p", run.diagnostic, "detail"));
    for (const job of run.jobs) {
      const row = element("div", "", "execution-job");
      row.append(element("span", job.name + " · " + job.status
        + (job.conclusion ? " · " + job.conclusion : ""), "muted"));
      if (job.url) row.append(issueLink("Job log", job.url));
      if (job.diagnostic) row.append(element("p", job.diagnostic, "detail"));
      article.append(row);
    }
    section.append(article);
  }
}

function renderExecutionTimeline(panel, events) {
  const section = executionSection(panel, "Timeline");
  if (!events.length) {
    section.append(element("p", "No execution events were recorded.", "muted"));
    return;
  }
  const list = element("ol", "", "execution-timeline");
  for (const event of events) {
    const item = element("li", "", "execution-event");
    const heading = element("div", "", "execution-event-heading");
    heading.append(element("strong", executionKind(event.kind)), issueTime(event.startedAt));
    item.append(heading);
    if (event.detail) item.append(element("p", event.detail));
    if (event.endedAt && !event.open) {
      const ended = element("p", "Ended ", "muted");
      ended.append(issueTime(event.endedAt));
      item.append(ended);
    } else if (event.endedAt) {
      const observed = element("p", "Last observed ", "muted");
      observed.append(issueTime(event.endedAt));
      item.append(observed);
    }
    if (event.open) item.append(element("p", "Ongoing or boundary unavailable.", "muted"));
    if (event.data?.unknown) item.append(element("p", "Duration unavailable.", "muted"));
    if (event.data?.url) item.append(issueLink("Open GitHub run", event.data.url));
    list.append(item);
  }
  section.append(list);
}

function renderExecutionDiagnostics(panel, diagnostics) {
  const section = executionSection(panel, "Diagnostics");
  const verification = diagnostics.verification;
  section.append(element("p", verification.available
    ? "Verification exited " + verification.exit + (verification.detail ? " · " + verification.detail : "")
    : "Verification outcome unavailable.", verification.available ? "" : "muted"));
  if (diagnostics.checks.available) {
    const checks = element("details", "", "execution-transitions");
    checks.append(element("summary", "Verification command"),
      element("pre", diagnostics.checks.command, "execution-log"));
    section.append(checks);
  } else {
    section.append(element("p", diagnostics.checks.detail, "muted"));
  }
  const log = element("details", "", "execution-transitions");
  log.append(element("summary", "Worker log excerpt"
    + (diagnostics.workerLog.truncated ? " · truncated" : "")));
  if (diagnostics.workerLog.available) {
    log.append(element("pre", diagnostics.workerLog.text, "execution-log"));
  } else {
    log.append(element("p", diagnostics.workerLog.detail, "muted"));
  }
  section.append(log);
}

function renderExecution(data, panel) {
  panel.replaceChildren();
  const summary = element("div", "", "execution-summary");
  summary.append(element("h3", "Execution details"),
    element("p", "Updated " + new Date(data.generatedAt).toLocaleString([], {
      dateStyle: "medium", timeStyle: "medium"}), "muted"));
  panel.append(summary);
  renderExecutionStages(panel, data.stages);
  renderExecutionBreakdown(panel, data.execution);
  renderExecutionCi(panel, data.execution.ci);
  renderExecutionTimeline(panel, data.execution.events);
  renderExecutionDiagnostics(panel, data.execution.diagnostics);
}

async function loadExecutionPanel(identifier) {
  const panel = byId("issue-panel-execution");
  if (!panel) return;
  const request = ++executionRequest;
  if (!panel.dataset.loaded) {
    panel.setAttribute("aria-busy", "true");
    const loading = element("p", "Loading execution details…", "muted");
    loading.setAttribute("role", "status");
    panel.replaceChildren(loading);
  }
  try {
    const data = await api("/api/execution?id=" + encodeURIComponent(identifier));
    if (!executionRequestCurrent(request, executionRequest, activeIssueTab, issueDialog.open,
      identifier, currentIssue?.identifier)) return;
    const view = panel.dataset.loaded
      ? captureExecutionView(panel, issueContent, getSelection(), document.activeElement) : null;
    renderExecution(data, panel);
    restoreExecutionView(panel, view, issueContent, getSelection());
    panel.dataset.loaded = "true";
  } catch (error) {
    if (!executionRequestCurrent(request, executionRequest, activeIssueTab, issueDialog.open,
      identifier, currentIssue?.identifier)) return;
    const message = element("p", error.message, "detail");
    message.setAttribute("role", "alert");
    const retry = element("button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => loadExecutionPanel(identifier));
    panel.replaceChildren(message, retry);
  } finally {
    if (request === executionRequest) panel.setAttribute("aria-busy", "false");
  }
}


async function openIssue(identifier) {
  const request = ++detailRequest;
  stopExecutionRefresh();
  executionRequest++;
  activeIssueTab = "issue";
  currentIssue = null;
  if (!issueDialog.open) {
    invokingIssue = identifier;
    issueDialog.showModal();
  }
  byId("issue-dialog-identifier").textContent = identifier;
  const cached = issueCache.get("")?.find((issue) => issue.identifier === identifier);
  issueTitle.textContent = cached?.title || "Issue details";
  byId("issue-source").hidden = true;
  issueContent.scrollTop = 0;
  issueContent.setAttribute("aria-busy", "true");
  const loading = element("p", "Loading issue details…", "muted");
  loading.setAttribute("role", "status");
  issueContent.replaceChildren(issueTitle, loading);
  try {
    const issue = await loadIssueDetails(identifier);
    if (request === detailRequest) renderIssueContext(issue);
  } catch (error) {
    if (request !== detailRequest) return;
    const message = element("p", error.message, "detail");
    message.setAttribute("role", "alert");
    const retry = element("button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => openIssue(identifier));
    issueContent.replaceChildren(issueTitle, message, retry);
  } finally {
    if (request === detailRequest) issueContent.setAttribute("aria-busy", "false");
  }
}

byId("issue-close").addEventListener("click", () => issueDialog.close());
issueDialog.addEventListener("keydown", (event) => {
  if (event.key !== "Tab" || event.ctrlKey || event.altKey || event.metaKey) return;
  const controls = [...issueDialog.querySelectorAll("button:not([disabled]), a[href], summary")]
    .filter((control) => control.checkVisibility());
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
let backdropPressed = false;
issueDialog.addEventListener("pointerdown", (event) => {
  const bounds = issueDialog.getBoundingClientRect();
  backdropPressed = event.target === issueDialog
    && (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom);
});
issueDialog.addEventListener("click", (event) => {
  if (backdropPressed && event.target === issueDialog) issueDialog.close();
  backdropPressed = false;
});
issueDialog.addEventListener("close", () => {
  detailRequest++;
  executionRequest++;
  stopExecutionRefresh();
  currentIssue = null;
  activeIssueTab = "issue";
  issueContent.setAttribute("aria-busy", "false");
  const trigger = byId("issue-open-" + invokingIssue) || repos.querySelector('[aria-selected="true"]');
  trigger?.focus({preventScroll: true});
});
poll();
