const tool = "linear_list_issues";
const isSearch = (event) => event.agent === "linear-issue" && event.tool === tool;
const day = (value) => value?.slice(0, 10);
const columns = [
  ["id", "Issue"],
  ["title", "Title"],
  ["project", "Project"],
  ["status", "Status"],
  ["statusType", "Status type"],
  ["labels", "Labels", (value) => value?.join(", ")],
  ["priority", "Priority", (value) => value?.name],
  ["estimate", "Estimate", (value) => value?.name],
  ["createdAt", "Created", day],
  ["updatedAt", "Updated", day],
  ["dueDate", "Due"],
];

function searchRows(output) {
  if (typeof output === "string") {
    try {
      output = JSON.parse(output);
    } catch {
      return;
    }
  }
  if (!Array.isArray(output?.issues)) return;
  if (output.issues.length === 0 && !output.hasNextPage) return "No matching issues.";

  const lines = [columns.map(([, heading]) => heading).join("\t")];
  for (const issue of output.issues) {
    const cells = columns.map(([field, , format]) => {
      const value = format ? format(issue[field]) : issue[field];
      return String(value ?? "").replace(/\s+/g, " ").trim() || "-";
    });
    lines.push(cells.join("\t"));
  }
  if (output.hasNextPage && output.cursor) lines.push(`Next page cursor: ${output.cursor}`);
  return lines.join("\n");
}

export default {
  id: "hex-linear-search",
  async setup(ctx) {
    await ctx.tool.hook("execute.before", (event) => {
      if (isSearch(event)) {
        event.input = { limit: 10, ...event.input, fields: columns.map(([field]) => field) };
      }
    });
    await ctx.tool.hook("execute.after", (event) => {
      if (!isSearch(event) || event.status !== "completed") return;
      const text = searchRows(event.result.output);
      if (text !== undefined) {
        event.result = { ...event.result, output: undefined, content: [{ type: "text", text }] };
      }
    });
  },
};
