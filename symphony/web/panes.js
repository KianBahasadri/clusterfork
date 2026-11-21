const STATUS_GROUPS = ["Todo", "In Progress", "Done"];

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
