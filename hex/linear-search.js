import { readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const isHex = (event) => ["linear-issue", "linear-issue-preview"].includes(event.agent);
const day = (value) => value?.slice(0, 10);
const issueColumns = [
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
const projectColumns = [
  ["id", "Project"],
  ["name", "Name"],
  ["status", "Status", (value) => value?.name],
  ["teams", "Teams", (value) => value?.map((team) => team.key || team.name).join(", ")],
  ["summary", "Summary"],
  ["description", "Description"],
  ["url", "URL"],
];

function searchRows(output, field, columns) {
  if (typeof output === "string") {
    try {
      output = JSON.parse(output);
    } catch {
      return;
    }
  }
  if (!Array.isArray(output?.[field])) return;
  if (output[field].length === 0 && !output.hasNextPage) return `No matching ${field}.`;

  const lines = [columns.map(([, heading]) => heading).join("\t")];
  for (const issue of output[field]) {
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
  id: "hex-linear",
  async setup(ctx) {
    await ctx.tool.transform((editor) => {
      editor.remove("skill");
      editor.add({
        name: "hex_notify",
        description: "Queue a GNOME notification through Hex after verifying a Linear change. Supply the actual issue or project URL. Hex displays it when this run ends.",
        options: { codemode: false },
        input: {
          type: "object",
          properties: {
            title: { type: "string", minLength: 1 },
            message: { type: "string", minLength: 1 },
            url: { type: "string", pattern: "^https://linear\\.app/[^/]+/(issue|project)/" },
          },
          required: ["title", "message", "url"],
          additionalProperties: false,
        },
        async execute() {
          return { content: "GNOME notification queued for Hex." };
        },
      });
      editor.add({
        name: "hex_reply",
        description: "Ask for an essential missing detail through Hex's clickable Reply notification. Set copy_image when a missing or unrelated clipboard image must be replaced. Finish this turn after calling.",
        options: { codemode: false },
        input: {
          type: "object",
          properties: {
            question: { type: "string", minLength: 1 },
            copy_image: { type: "boolean" },
          },
          required: ["question", "copy_image"],
          additionalProperties: false,
        },
        async execute() {
          return { content: "Hex will request a reply and resume this session with the answer. End this turn now." };
        },
      });
      editor.add({
        name: "hex_upload_clipboard",
        description: "PUT a captured image file to the signed upload URL returned by Linear's prepare_file_upload MCP tool. Pass every uploadRequest header verbatim. Then use the MCP-provided assetUrl in the issue description.",
        options: { codemode: false },
        input: {
          type: "object",
          properties: {
            file: { type: "string" },
            url: { type: "string" },
            headers: { type: "object", additionalProperties: { type: "string" } },
          },
          required: ["file", "url", "headers"],
          additionalProperties: false,
        },
        async execute(input) {
          const file = path.resolve(input.file);
          const directory = path.dirname(file);
          if (path.dirname(directory) !== tmpdir() || !path.basename(directory).startsWith("hex-linear-") ||
              !/^clipboard-[1-9]\d*\.(png|jpg|webp)$/.test(path.basename(file))) {
            throw new Error("Use a captured image file supplied in the Hex request.");
          }
          const url = new URL(input.url);
          if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) {
            throw new Error("Use the HTTPS signed upload URL returned by Linear MCP.");
          }
          const bytes = await readFile(file);
          if (bytes.length > 5 * 1024 * 1024) throw new Error("The clipboard image exceeds 5 MiB.");
          const response = await fetch(url, {
            method: "PUT", headers: input.headers, body: bytes,
            redirect: "error", signal: AbortSignal.timeout(20000),
          });
          if (!response.ok) throw new Error(`Clipboard upload failed with HTTP ${response.status}. Prepare a fresh upload through MCP before retrying.`);
          return { content: "Clipboard bytes uploaded. Use the MCP-provided assetUrl when saving the issue." };
        },
      });
    });
    await ctx.tool.hook("execute.before", (event) => {
      if (!isHex(event)) return;
      if (event.tool === "linear_list_issues") {
        event.input = { limit: 10, ...event.input, fields: issueColumns.map(([field]) => field) };
      }
      if (event.tool === "linear_list_projects") {
        event.input = { limit: 50, ...event.input, fields: projectColumns.map(([field]) => field) };
      }
    });
    await ctx.tool.hook("execute.after", (event) => {
      if (!isHex(event) || event.status !== "completed") return;
      let text;
      if (event.tool === "linear_list_issues") text = searchRows(event.result.output, "issues", issueColumns);
      if (event.tool === "linear_list_projects") text = searchRows(event.result.output, "projects", projectColumns);
      if (text !== undefined) {
        event.result = { ...event.result, output: undefined, content: [{ type: "text", text }] };
      }
    });
  },
};
