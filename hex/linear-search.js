import { readFile } from "node:fs/promises";
import path from "node:path";

const attachmentName = /^clipboard-[1-9]\d*\.(png|jpg|webp)$/;
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
    const attachmentDirectory = process.env.HEX_LINEAR_ATTACHMENT_DIR;
    const uploads = new Map();
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
        description: "Upload a captured image after calling linear_prepare_file_upload for its filename. Use the returned Linear asset URL in the issue description.",
        options: { codemode: false },
        input: {
          type: "object",
          properties: {
            file: { type: "string" },
          },
          required: ["file"],
          additionalProperties: false,
        },
        async execute(input, { agent, sessionID }) {
          const file = path.resolve(input.file);
          const filename = path.basename(file);
          if (agent !== "linear-issue" || path.dirname(file) !== attachmentDirectory || !attachmentName.test(filename))
            throw new Error("Use an attachment supplied in this Hex request.");
          const upload = uploads.get(filename);
          if (!upload || upload.sessionID !== sessionID)
            throw new Error("Prepare the upload for this filename through Linear MCP first.");
          uploads.delete(filename);
          const bytes = await readFile(file);
          if (bytes.length > 5 * 1024 * 1024) throw new Error("The clipboard image exceeds 5 MiB.");
          const response = await fetch(upload.url, {
            method: "PUT", headers: upload.headers, body: bytes,
            redirect: "error", signal: AbortSignal.timeout(20000),
          });
          if (!response.ok) throw new Error(`Clipboard upload failed with HTTP ${response.status}. Prepare a fresh upload through MCP before retrying.`);
          return { content: `Clipboard image uploaded. Use this Linear asset URL when saving the issue: ${upload.assetUrl}` };
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
      if (event.tool === "linear_prepare_file_upload" && event.agent === "linear-issue" && attachmentDirectory) {
        const filename = event.input?.filename;
        if (typeof filename !== "string" || !attachmentName.test(filename)) return;
        uploads.delete(filename);
        try {
          const output = event.result.output;
          const { assetUrl, uploadRequest } = typeof output === "string" ? JSON.parse(output) : output;
          const url = new URL(uploadRequest.url);
          if (uploadRequest.method !== "PUT" || url.protocol !== "https:" || url.username || url.password || url.port ||
              new URL(assetUrl).origin !== "https://uploads.linear.app") return;
          uploads.set(filename, { sessionID: event.sessionID, url, headers: uploadRequest.headers, assetUrl });
          event.result = { content: [{ type: "text", text: `Upload prepared for ${filename}. Call hex_upload_clipboard with its file path.` }] };
        } catch (error) {
          if (!(error instanceof SyntaxError || error instanceof TypeError)) throw error;
        }
        return;
      }
      let text;
      if (event.tool === "linear_list_issues") text = searchRows(event.result.output, "issues", issueColumns);
      if (event.tool === "linear_list_projects") text = searchRows(event.result.output, "projects", projectColumns);
      if (text !== undefined) {
        event.result = { ...event.result, output: undefined, content: [{ type: "text", text }] };
      }
    });
  },
};
