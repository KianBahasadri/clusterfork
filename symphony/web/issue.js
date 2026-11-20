const issueDialog = byId("issue-dialog");
const issueContent = byId("issue-content");
const issueTitle = byId("issue-dialog-title");
let detailRequest;
let invokingIssue = "";

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
  button.append(icon(stateIcons[issue.state.type] || "circle"),
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

function renderIssueContext(issue) {
  byId("issue-dialog-identifier").textContent = issue.identifier;
  issueTitle.textContent = issue.title;
  const layout = element("div", "", "issue-detail-layout");
  const body = element("div", "", "issue-detail-main");
  const properties = element("div", "", "issue-properties");
  const source = byId("issue-source");
  source.href = issue.url;
  source.hidden = false;
  const values = detailSection(properties, "Properties");
  const status = element("p", "", "issue-property");
  status.append(icon(stateIcons[issue.state.type] || "circle"), element("span", issue.state.name));
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

  body.append(issueTitle, issue.descriptionHtml ? issueMarkdown(issue.descriptionHtml) : element("p", "No description.", "muted"));
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
  issueContent.replaceChildren(layout);
}

async function openIssue(identifier) {
  detailRequest?.abort();
  const request = new AbortController();
  detailRequest = request;
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
    const issue = await api("/api/issue?id=" + encodeURIComponent(identifier), {signal: request.signal});
    if (!request.signal.aborted) renderIssueContext(issue);
  } catch (error) {
    if (request.signal.aborted) return;
    const message = element("p", error.message, "detail");
    message.setAttribute("role", "alert");
    const retry = element("button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => openIssue(identifier));
    issueContent.replaceChildren(issueTitle, message, retry);
  } finally {
    if (!request.signal.aborted) issueContent.setAttribute("aria-busy", "false");
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
  detailRequest?.abort();
  issueContent.setAttribute("aria-busy", "false");
  const trigger = byId("issue-open-" + invokingIssue) || repos.querySelector('[aria-selected="true"]');
  trigger?.focus({preventScroll: true});
});
