"""Exercise the real composer submit listener with controlled encoding delays."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");

class FakeNode {
  constructor(tag = "div", id = "") {
    this.tag = tag;
    this.id = id;
    this.listeners = {};
    this.children = [];
    this.classList = {add() {}, remove() {}};
    this.style = {setProperty() {}};
    this.dataset = {};
    this.hidden = false;
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.title = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.disabled = false;
  }
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this[name] = String(value); }
  removeAttribute(name) { delete this[name]; }
  getAttribute(name) { return this[name]; }
  toggleAttribute(name, force) { if (force) this[name] = ""; else delete this[name]; }
  closest() { return new FakeNode("aside"); }
  focus() {}
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  requestSubmit() {}
}

const nodes = new Map();
const document = {
  activeElement: null,
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, new FakeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return new FakeNode(tag); },
  createElementNS(_namespace, tag) { return new FakeNode(tag); },
  querySelector(selector) {
    if (selector !== "main") return null;
    if (!nodes.has("main")) nodes.set("main", new FakeNode("main", "main"));
    return nodes.get("main");
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};

const revoked = [];
let sequence = 0;
const URL = {
  createObjectURL() { return "blob:fixture-" + (++sequence); },
  revokeObjectURL(url) { revoked.push(url); },
};
const sent = [];
let failNext = false;
let nextId = 0;
let holdNext = false;
let releasePost = null;
const fetch = async (_path, options) => {
  if (failNext) {
    failNext = false;
    return {ok: false, json: async () => ({error: "Fixture rejected the message."})};
  }
  if (holdNext) {
    holdNext = false;
    await new Promise((resolve) => { releasePost = resolve; });
  }
  const body = JSON.parse(options.body);
  sent.push(body);
  return {ok: true, json: async () => ({message: {
    id: ++nextId, role: "user", text: body.text, images: body.images || [],
  }})};
};
const context = vm.createContext({
  document, URL, fetch, console,
  setInterval: () => 0, setTimeout, clearTimeout,
  __encoders: [],
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext(
  "imagePayload = () => new Promise((resolve) => __encoders.push(resolve));",
  context,
);

const run = (code) => vm.runInContext(code, context);
const handler = nodes.get("composer").listeners.submit[0];
const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => {
  await tick();
  await new Promise((resolve) => setTimeout(resolve, 0));
};

async function reset() {
  run('pendingImages.length = 0; messageField.value = ""; selected = "project-a"; history.length = 0;')
  run('messages.children.length = 0; shownMessageIds.clear(); pendingMessageIds.clear(); pendingMessages.length = 0; pendingStream = null; renderedStream = ""; byId("stream").replaceChildren(); byId("welcome").hidden = false;')
  context.__encoders = [];
  sent.length = 0;
  revoked.length = 0;
  failNext = false;
  nextId = 0;
  holdNext = false;
  releasePost = null;
}

(async () => {
  const results = {};

  await reset();
  run('messageField.value = "First message"; attachImage({type: "image/png", size: 10, name: "one.png"});')
  const first = handler({preventDefault() {}});
  await tick();
  results.encoding = {
    status: run('byId("send-status").textContent'),
    hidden: run('byId("send-status").hidden'),
    busy: run('composer["aria-busy"]') || null,
  };
  const duplicate = await handler({preventDefault() {}});
  results.duplicate = {
    sent: sent.length,
    encoders: context.__encoders.length,
  };
  run('messageField.value = "Second draft"; selected = "project-b"; attachImage({type: "image/png", size: 20, name: "two.png"});')
  context.__encoders[0]({type: "image/png", data: "AAAA"});
  await first;
  await settle();
  results.delayed = {
    sent: sent[0],
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    revoked: revoked.slice(),
    status: run('byId("send-status").textContent'),
    statusHidden: run('byId("send-status").hidden'),
    busy: run('composer["aria-busy"]') || null,
    messages: run('[...messages.children].map((node) => ({id: node.dataset.messageId, text: node.children[1].textContent}))'),
    history: run("history.slice()"),
  };

  await reset();
  holdNext = true;
  run('messageField.value = "Delayed message";')
  const delayedPost = handler({preventDefault() {}});
  await tick();
  const waiting = {
    sent: sent.length,
    sending: run("sending"),
    status: run('byId("send-status").textContent'),
    hidden: run('byId("send-status").hidden'),
    busy: run('composer["aria-busy"]') || null,
  };
  releasePost();
  await delayedPost;
  await settle();
  results.delayedPost = {
    waiting,
    messages: run('[...messages.children].map((node) => ({id: node.dataset.messageId, text: node.children[1].textContent}))'),
    sending: run("sending"),
    status: run('byId("send-status").textContent'),
    hidden: run('byId("send-status").hidden'),
  };

  await reset();
  run('messageField.value = ""; attachImage({type: "image/png", size: 10, name: "only.png"});')
  const imageOnly = handler({preventDefault() {}});
  await tick();
  context.__encoders[0]({type: "image/png", data: "DDDD"});
  await imageOnly;
  await settle();
  results.imageOnly = {
    sent: sent[0],
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    messages: run('[...messages.children].map((node) => ({id: node.dataset.messageId, text: node.children[1].textContent}))'),
  };

  await reset();
  run('messageField.value = "Keep me"; attachImage({type: "image/png", size: 10, name: "keep.png"}); imagePayload = () => Promise.reject(new Error("Read failed"));')
  await handler({preventDefault() {}});
  await settle();
  results.readFailure = {
    sent: sent.length,
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    error: run('byId("request-error").textContent'),
    status: run('byId("send-status").textContent'),
    statusHidden: run('byId("send-status").hidden'),
    messages: run("messages.children.length"),
  };
  run('imagePayload = () => new Promise((resolve) => __encoders.push(resolve));')

  await reset();
  failNext = true;
  run('messageField.value = "Retry me"; attachImage({type: "image/png", size: 10, name: "retry.png"});')
  const failed = handler({preventDefault() {}});
  await tick();
  context.__encoders[0]({type: "image/png", data: "BBBB"});
  await failed;
  await settle();
  results.failed = {
    sent: sent.length,
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    error: run('byId("request-error").textContent'),
    status: run('byId("send-status").textContent'),
    statusHidden: run('byId("send-status").hidden'),
    messages: run("messages.children.length"),
  };
  run('messageField.value = "Retry with changes"; selected = "project-c";')
  const retried = handler({preventDefault() {}});
  await tick();
  context.__encoders[1]({type: "image/png", data: "CCCC"});
  await retried;
  await settle();
  results.retried = {
    sent: sent[sent.length - 1],
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    status: run('byId("send-status").textContent'),
    statusHidden: run('byId("send-status").hidden'),
    messages: run('[...messages.children].map((node) => ({id: node.dataset.messageId, text: node.children[1].textContent}))'),
  };

  console.log(JSON.stringify(results));
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
"""


class ComposerWebTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_delayed_encoding_preserves_destination_edits_and_retries(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        run = subprocess.run(["node", "-e", SCRIPT, str(app)], text=True,
                             capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)

        delayed = result["delayed"]
        self.assertEqual(result["encoding"], {
            "status": "Sending…", "hidden": False, "busy": None,
        })
        self.assertEqual(result["duplicate"], {"sent": 0, "encoders": 1})
        self.assertEqual(delayed["sent"], {
            "text": "First message", "project": "project-a",
            "images": [{"type": "image/png", "data": "AAAA"}],
        })
        self.assertEqual(delayed["draft"], "Second draft")
        self.assertEqual(delayed["pending"], 1)
        self.assertEqual(delayed["revoked"], ["blob:fixture-1"])
        self.assertEqual((delayed["status"], delayed["statusHidden"], delayed["busy"]),
                         ("", False, None))
        self.assertEqual(delayed["messages"], [{"id": "1", "text": "First message"}])
        self.assertEqual(delayed["history"], ["First message"])

        self.assertEqual(result["delayedPost"], {
            "waiting": {"sent": 0, "sending": True, "status": "Sending…",
                        "hidden": False, "busy": None},
            "messages": [{"id": "1", "text": "Delayed message"}],
            "sending": False, "status": "", "hidden": False,
        })
        self.assertEqual(result["imageOnly"], {
            "sent": {"text": "", "project": "project-a",
                     "images": [{"type": "image/png", "data": "DDDD"}]},
            "draft": "", "pending": 0,
            "messages": [{"id": "1", "text": ""}],
        })

        self.assertEqual(result["readFailure"], {
            "sent": 0, "draft": "Keep me", "pending": 1, "error": "Read failed",
            "status": "", "statusHidden": False, "messages": 0,
        })
        self.assertEqual(result["failed"], {
            "sent": 0, "draft": "Retry me", "pending": 1,
            "error": "Fixture rejected the message.",
            "status": "", "statusHidden": False, "messages": 0,
        })
        self.assertEqual(result["retried"], {
            "sent": {
                "text": "Retry with changes", "project": "project-c",
                "images": [{"type": "image/png", "data": "CCCC"}],
            },
            "draft": "", "pending": 0, "status": "", "statusHidden": False,
            "messages": [{"id": "1", "text": "Retry with changes"}],
        })

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_status_displays_zero_context_usage_when_signed_in(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
      id, textContent: "", hidden: true, dataset: {}, style: {setProperty() {}},
      listeners: {}, addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
      classList: {add() {}, remove() {}}, querySelector() { return null; },
      querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {},
      toggleAttribute() {}, focus() {}, closest() { return this; }, replaceChildren() {},
    });
    return nodes.get(id);
  },
  createElement() { return this.getElementById("tmp-" + Math.random()); },
  createElementNS() { return this.getElementById("svg-" + Math.random()); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const ctx = vm.createContext({document: doc, setInterval: () => 0, setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
vm.runInContext('coordinator = {model: "gemini-4-neon-high", contextUsed: 0, rateLimits: {}, turnTiming: {}}; canInterrupt = false; showCoordinatorStatus();', ctx);
const el = doc.getElementById("status-context");
console.log(JSON.stringify({hidden: el.hidden, text: el.textContent}));
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"hidden": False, "text": "Context 0% used"})

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_status_displays_queued_and_running_compaction(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
      id, textContent: "", hidden: true, dataset: {}, style: {setProperty() {}},
      listeners: {}, addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
      classList: {add() {}, remove() {}}, querySelector() { return null; },
      querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {},
      toggleAttribute() {}, focus() {}, closest() { return this; }, replaceChildren() {},
    });
    return nodes.get(id);
  },
  createElement() { return this.getElementById("tmp-" + Math.random()); },
  createElementNS() { return this.getElementById("svg-" + Math.random()); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const RealDate = Date;
const FakeDate = class extends RealDate {
  static now() { return 100000; }
};
const ctx = vm.createContext({document: doc, Date: FakeDate, setInterval: () => 0, setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const timing = doc.getElementById("turn-timing");
const snap = (code) => { vm.runInContext(code, ctx); return {hidden: timing.hidden, text: timing.textContent}; };
const results = {
  queuedIdle: snap('coordinator = {rateLimits: {}, turnTiming: {}}; canInterrupt = false; compacting = "queued"; showCoordinatorStatus();'),
  queuedWorking: snap('coordinator = {rateLimits: {}, turnTiming: {startedAt: 95}}; canInterrupt = true; compacting = "queued"; showCoordinatorStatus();'),
  runningFallback: snap('coordinator = {rateLimits: {}, turnTiming: {startedAt: 80, completedAt: 85, durationMs: 5000}}; canInterrupt = false; compacting = "running"; showCoordinatorStatus();'),
  runningTimed: snap('coordinator = {rateLimits: {}, turnTiming: {startedAt: 92}}; canInterrupt = false; compacting = "running"; showCoordinatorStatus();'),
  finished: snap('coordinator = {rateLimits: {}, turnTiming: {startedAt: 92, durationMs: 8000}}; canInterrupt = false; compacting = ""; showCoordinatorStatus();'),
};
console.log(JSON.stringify(results));
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {
            "queuedIdle": {"hidden": False, "text": "Compaction queued"},
            "queuedWorking": {"hidden": False, "text": "Working for 5s · Compaction queued"},
            "runningFallback": {"hidden": False, "text": "Compacting context…"},
            "runningTimed": {"hidden": False, "text": "Compacting for 8s"},
            "finished": {"hidden": False, "text": "Worked for 8s"},
        })

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_config_dialog_clears_key_on_save_remove_close_and_keeps_on_error(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (id) => ({
  id, textContent: "", value: "", hidden: false, disabled: false, open: false,
  dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
  addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
  classList: {add() {}, remove() {}}, querySelector() { return null; },
  querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {},
  toggleAttribute() {}, focus() {}, closest() { return makeNode("aside"); },
  append(...n) { this.children.push(...n); }, prepend(...n) { this.children.unshift(...n); },
  replaceChildren(...n) { this.children = n; },
  showModal() { this.open = true; },
  close() { this.open = false; for (const h of this.listeners.close || []) h(); },
});
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode(id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
let failNext = false;
const posts = [];
let state = {
  opencodeGo: {configured: false, malformed: false, busy: false},
  antigravity: {signedIn: false, account: {}, login: null, busy: false},
};
const fetch = async (path, options = {}) => {
  if (path === "/api/agents") return {ok: true, json: async () => ({content: "rules", html: "<p>rules</p>"})};
  if (path === "/api/config") {
    if ((options.method || "GET") === "POST") {
      if (failNext) {
        failNext = false;
        return {ok: false, json: async () => ({error: "Rejected key."})};
      }
      const body = JSON.parse(options.body);
      posts.push(body);
      if (body.action === "set_opencode_key") state.opencodeGo.configured = true;
      if (body.action === "clear_opencode_key") state.opencodeGo.configured = false;
      if (body.action === "antigravity_login") state.antigravity.login = {url: "https://example.test/auth"};
      if (body.action === "antigravity_cancel_login") state.antigravity.login = null;
      return {ok: true, json: async () => state};
    }
    return {ok: true, json: async () => state};
  }
  return {ok: true, json: async () => ({messages: [], busy: false, canInterrupt: false, stream: "", streamHtml: "", tasks: [], signedIn: false, login: null, paused: "", view: "", coordinator: {rateLimits: {}, turnTiming: {}}, issueError: ""})};
};
const ctx = vm.createContext({document: doc, fetch, setInterval: () => 0, setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  const draft = doc.getElementById("message");
  draft.value = "Keep my draft";
  vm.runInContext('openConfig();', ctx);
  await new Promise((r) => setTimeout(r, 10));
  const input = doc.getElementById("config-opencode-key");
  input.value = "canary-bad-key";
  failNext = true;
  await doc.getElementById("config-opencode-form").listeners.submit[0]({preventDefault() {}});
  await new Promise((r) => setTimeout(r, 10));
  const afterError = {value: input.value, feedback: doc.getElementById("config-opencode-feedback").textContent};
  input.value = "canary-good-key";
  await doc.getElementById("config-opencode-form").listeners.submit[0]({preventDefault() {}});
  await new Promise((r) => setTimeout(r, 10));
  const afterSave = {value: input.value, status: doc.getElementById("config-opencode-status").textContent};
  input.value = "stray-unsaved";
  vm.runInContext('closeConfig();', ctx);
  const afterClose = input.value;
  vm.runInContext('openConfig();', ctx);
  await new Promise((r) => setTimeout(r, 10));
  await doc.getElementById("config-opencode-clear").listeners.click[0]();
  await new Promise((r) => setTimeout(r, 10));
  const afterClear = {status: doc.getElementById("config-opencode-status").textContent, draft: draft.value};
  console.log(JSON.stringify({posts, afterError, afterSave, afterClose, afterClear}));
})();
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["afterError"], {"value": "canary-bad-key", "feedback": "Rejected key."})
        self.assertEqual(result["afterSave"], {"value": "", "status": "API key configured on PVC."})
        self.assertEqual(result["afterClose"], "")
        self.assertEqual(result["afterClear"], {"status": "Not configured.", "draft": "Keep my draft"})

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_plan_proposal_accept_buttons_render_superseded_and_approved_states(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => {
  const node = {
    tag, id, textContent: "", innerHTML: "", value: "", hidden: false, disabled: false, open: false,
    dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
    classList: {add() {}, remove() {}},
    addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
    setAttribute() {}, removeAttribute() {}, toggleAttribute() {}, focus() {},
    contains() { return false; },
    closest() { return makeNode("aside"); },
    append(...items) { this.children.push(...items); node._relink(); },
    prepend(...items) { this.children.unshift(...items); node._relink(); },
    replaceChildren(...items) { this.children = items; node._relink(); },
    _relink() { for (const c of this.children) if (c && typeof c === "object") c.parentNode = this; },
    querySelector(sel) {
      const cls = sel.startsWith(".") ? sel.slice(1) : null;
      for (const c of this.children) {
        if (!c || typeof c !== "object") continue;
        if (cls && String(c.className || "").split(/\s+/).includes(cls)) return c;
        const nested = c.querySelector?.(sel);
        if (nested) return nested;
      }
      return null;
    },
    querySelectorAll(sel) {
      const cls = sel.startsWith(".") ? sel.slice(1) : null;
      const out = [];
      for (const c of this.children) {
        if (!c || typeof c !== "object") continue;
        if (cls && String(c.className || "").split(/\s+/).includes(cls)) out.push(c);
        if (c.querySelectorAll) out.push(...c.querySelectorAll(sel));
      }
      return out;
    },
  };
  return node;
};
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const approved = [];
const fetch = async (path, options = {}) => {
  if (path === "/api/approve") {
    approved.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({approved: "KIAN-1", repo: "owner/repo"})};
  }
  return {ok: true, json: async () => ({})};
};
const ctx = vm.createContext({document: doc, fetch, setInterval: () => 0, setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
(async () => {
  const hash1 = "1".repeat(64);
  const hash2 = "2".repeat(64);
  vm.runInContext(`
    tasks = [];
    insertMessage({id: 1, role: "system", text: "Proposed v1", meta: {kind: "plan_proposal", issue: "KIAN-1", title: "One", planHash: "${hash1}"}});
    insertMessage({id: 2, role: "system", text: "Proposed v2", meta: {kind: "plan_proposal", issue: "KIAN-1", title: "One", planHash: "${hash2}"}});
    syncApproveButtons();
  `, ctx);
  const msgs = doc.getElementById("messages");
  const btns = msgs.querySelectorAll(".plan-accept-button");
  const before = btns.map((b) => ({text: b.textContent, disabled: b.disabled, state: b.dataset.state}));
  await btns[1].listeners.click[0]();
  await new Promise((r) => setTimeout(r, 10));
  const after = btns.map((b) => ({text: b.textContent, disabled: b.disabled, state: b.dataset.state}));
  console.log(JSON.stringify({before, after, approved}));
})();
"""
        panes = Path(__file__).resolve().parents[1] / "symphony/web/panes.js"
        run = subprocess.run(["node", "-e", probe, str(panes), str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["before"], [
            {"text": "Superseded", "disabled": True, "state": "superseded"},
            {"text": "Accept", "disabled": False, "state": "active"},
        ])
        self.assertEqual(res["after"], [
            {"text": "Approved", "disabled": True, "state": "approved"},
            {"text": "Approved", "disabled": True, "state": "approved"},
        ])
        self.assertEqual(res["approved"], [{"issue": "KIAN-1", "planHash": "2" * 64}])

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_repo_prompt_buttons_attach_toggle_prepend_and_send(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => {
  const node = {
    tag, id, textContent: "", innerHTML: "", value: "", hidden: false, disabled: false, open: false,
    dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
    classList: {add() {}, remove() {}},
    addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
    setAttribute(n, v) { this[n] = String(v); }, removeAttribute(n) { delete this[n]; },
    toggleAttribute() {}, focus() {}, setSelectionRange() {}, contains() { return false; },
    closest() { return makeNode("aside"); },
    append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    replaceChildren(...items) { this.children = items; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  return node;
};
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const sent = [];
const fetch = async (path, options = {}) => {
  if (path === "/api/chat") {
    const body = JSON.parse(options.body);
    sent.push(body);
    return {ok: true, json: async () => ({message: {id: sent.length, role: "user", text: body.text, images: []}})};
  }
  return {ok: true, json: async () => ({})};
};
const ctx = vm.createContext({document: doc, fetch, setInterval: () => 0, setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  vm.runInContext(`
    projectRepos = new Map([["alpha", "owner/a"], ["beta", "owner/b"]]);
    selected = "alpha"; selectedRepo = "owner/a";
    renderRepoButtons([
      {repo: "owner/a", label: "Idle note", prompt: "[Note: keep idle]", mode: "attach"},
      {repo: "owner/a", label: "Ship now", prompt: "Ship all ready", mode: "send"},
      {repo: "owner/b", label: "Other repo", prompt: "Other", mode: "attach"},
    ], "owner/a");
  `, ctx);
  const bar = doc.getElementById("repo-buttons");
  const visibleLabels = bar.children.map((b) => b.textContent);
  // Arm attach button, submit message, verify prompt prepended and disarmed afterward.
  bar.children[0].listeners.click[0]();
  const armedAttr = bar.children[0]["aria-pressed"];
  doc.getElementById("message").value = "Continue please.";
  await doc.getElementById("composer").listeners.submit[0]({preventDefault() {}});
  const disarmedAttr = bar.children[0]["aria-pressed"];
  // Toggle attach on then off, then click send button.
  bar.children[0].listeners.click[0]();
  bar.children[0].listeners.click[0]();
  const toggledOffAttr = bar.children[0]["aria-pressed"];
  await bar.children[1].listeners.click[0]();
  console.log(JSON.stringify({visibleLabels, armedAttr, disarmedAttr, toggledOffAttr, sent}));
})();
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["visibleLabels"], ["Idle note", "Ship now"])
        self.assertEqual(res["armedAttr"], "true")
        self.assertEqual(res["disarmedAttr"], "false")
        self.assertEqual(res["toggledOffAttr"], "false")
        self.assertEqual([s["text"] for s in res["sent"]], [
            "[Note: keep idle]\n\nContinue please.",
            "Ship all ready",
        ])

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_diagram_panel_renders_svg_blob_and_surfaces_mermaid_errors_loudly(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => {
  const node = {
    tag, id, textContent: "", innerHTML: "", value: "", hidden: true, disabled: false, open: false,
    dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
    classList: {add() {}, remove() {}},
    addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
    setAttribute(n, v) { this[n] = String(v); }, removeAttribute(n) { delete this[n]; },
    toggleAttribute() {}, focus() {}, setSelectionRange() {}, contains() { return false; },
    closest() { return makeNode("aside"); },
    append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    replaceChildren(...items) { this.children = items; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  return node;
};
const doc = {
  head: makeNode("head"),
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
let seq = 0;
const revoked = [];
class FakeBlob { constructor(parts, opts) { this.parts = parts; this.type = opts?.type; } }
const URL = {
  createObjectURL() { return "blob:svg-" + (++seq); },
  revokeObjectURL(url) { revoked.push(url); },
};
let failRender = false;
const mermaid = {
  initialize() {},
  async render(id, src) {
    if (failRender) throw new Error("Syntax error on line 2");
    return {svg: "<svg id='" + id + "'>" + src + "</svg>"};
  },
};
const ctx = vm.createContext({document: doc, URL, Blob: FakeBlob, mermaid, setInterval: () => 0, setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  await ctx.renderDiagramPanel({title: "Arch", source: "graph TD; A-->B;", updated: 1}, "owner/a");
  const panel = doc.getElementById("diagram-panel");
  const canvas = doc.getElementById("diagram-canvas");
  const err = doc.getElementById("diagram-error");
  const okSnap = {hidden: panel.hidden, imgSrc: canvas.children[0]?.src, errHidden: err.hidden};
  failRender = true;
  await ctx.renderDiagramPanel({title: "Broken", source: "graph ???", updated: 2}, "owner/a");
  const errSnap = {hidden: panel.hidden, imgs: canvas.children.length, errHidden: err.hidden, errText: err.textContent};
  doc.getElementById("diagram-close").listeners.click[0]();
  const closedHidden = panel.hidden;
  failRender = false;
  // Same updated timestamp stays hidden; newer timestamp reopens panel.
  await ctx.renderDiagramPanel({title: "Broken", source: "graph ???", updated: 2}, "owner/a");
  const stillHidden = panel.hidden;
  await ctx.renderDiagramPanel({title: "Fixed", source: "graph TD; B-->C;", updated: 3}, "owner/a");
  const reopenedSnap = {hidden: panel.hidden, imgSrc: canvas.children[0]?.src, title: doc.getElementById("diagram-title").textContent};
  doc.getElementById("diagram-close").listeners.click[0]();
  console.log(JSON.stringify({okSnap, errSnap, closedHidden, stillHidden, reopenedSnap, revoked}));
})();
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["okSnap"], {"hidden": False, "imgSrc": "blob:svg-1", "errHidden": True})
        self.assertEqual(res["errSnap"], {"hidden": False, "imgs": 0, "errHidden": False, "errText": "Mermaid render error: Syntax error on line 2"})
        self.assertTrue(res["closedHidden"])
        self.assertTrue(res["stillHidden"])
        self.assertEqual(res["reopenedSnap"], {"hidden": False, "imgSrc": "blob:svg-2", "title": "Fixed"})
        self.assertEqual(res["revoked"], ["blob:svg-1", "blob:svg-2"])

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_panes_format_duration_not_shadowed_by_app_turn_duration(self):
        web = Path(__file__).resolve().parents[1] / "symphony/web"
        panes = web / "panes.js"
        app = web / "app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => ({
  tag, id, textContent: "", innerHTML: "", value: "", hidden: true, disabled: false, open: false,
  dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
  classList: {add() {}, remove() {}},
  addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
  setAttribute(n, v) { this[n] = String(v); }, removeAttribute(n) { delete this[n]; },
  toggleAttribute() {}, focus() {}, setSelectionRange() {}, contains() { return false; },
  closest() { return makeNode("aside"); },
  append(...items) { this.children.push(...items); },
  prepend(...items) { this.children.unshift(...items); },
  replaceChildren(...items) { this.children = items; },
  querySelector() { return null; }, querySelectorAll() { return []; },
});
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const ctx = vm.createContext({document: doc, setInterval: () => 0, setTimeout, clearTimeout, Date});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const now = Date.parse("2026-11-22T00:30:00Z");
const labelNode = {textContent: ""};
const pill = {
  dataset: {stageStart: "2026-11-22T00:00:00Z", stageName: "Backlog", stageCoverage: "complete"},
  lastChild: labelNode,
  title: "",
};
ctx.updateStagePill(pill, now);
vm.runInContext('coordinator = {model: "gemini-4-neon-high", turnTiming: {startedAt: ' + Math.floor((now - 5000) / 1000) + '}}; canInterrupt = true;', ctx);
const origNow = Date.now;
Date.now = () => now;
try {
  ctx.showCoordinatorStatus();
} finally {
  Date.now = origNow;
}
console.log(JSON.stringify({
  fmtMs: ctx.formatDuration(90000),
  stage: ctx.stageDuration("2026-11-22T00:00:00Z", now),
  pillText: pill.lastChild.textContent,
  turnTimingText: doc.getElementById("turn-timing").textContent,
}));
"""
        run = subprocess.run(["node", "-e", probe, str(panes), str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["fmtMs"], "1m")
        self.assertEqual(res["stage"], "30m")
        self.assertEqual(res["pillText"], "30m")
        self.assertEqual(res["turnTimingText"], "Working for 5s")

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_usage_alert_banner_reset_date_title_and_background_notification(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => ({
  tag, id, textContent: "", innerHTML: "", value: "", hidden: true, disabled: false, open: false,
  dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
  classList: {add() {}, remove() {}},
  addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
  setAttribute(n, v) { this[n] = String(v); }, removeAttribute(n) { delete this[n]; },
  toggleAttribute() {}, focus() {}, setSelectionRange() {}, contains() { return false; },
  closest() { return makeNode("aside"); },
  append(...items) { this.children.push(...items); },
  prepend(...items) { this.children.unshift(...items); },
  replaceChildren(...items) { this.children = items; },
  querySelector() { return null; }, querySelectorAll() { return []; },
});
const doc = {
  title: "Symphony",
  hidden: true,
  hasFocus() { return false; },
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const sentNotifications = [];
function FakeNotification(title, opts) {
  sentNotifications.push({title, body: opts.body, tag: opts.tag});
}
FakeNotification.permission = "default";
FakeNotification.requestPermission = async () => {
  FakeNotification.permission = "granted";
  return "granted";
};
const ctx = vm.createContext({
  document: doc,
  Notification: FakeNotification,
  setInterval: () => 0, setTimeout, clearTimeout, Date,
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const box = doc.getElementById("usage-alert");
const text = doc.getElementById("usage-alert-text");
const btn = doc.getElementById("usage-notify-enable");
const sameDay = ctx.formatResetTime(1700001000, new Date(1700000000 * 1000));
const multiDay = ctx.formatResetTime(1700300000, new Date(1700000000 * 1000));
const resetEpoch = Math.floor(Date.now() / 1000) + 4 * 86400;

ctx.renderUsageAlert({active: true, source: "rate_limit", windows: ["weekly"], resetsAt: resetEpoch, detail: ""});
const beforePerm = {
  hidden: box.hidden,
  text: text.textContent,
  btnHidden: btn.hidden,
  title: doc.title,
  notifCount: sentNotifications.length,
};
(async () => {
  await btn.listeners.click[0]();
  const afterGrant = {btnHidden: btn.hidden, notifCount: sentNotifications.length};
  // Identical poll does NOT fire duplicate Notification.
  ctx.renderUsageAlert({active: true, source: "rate_limit", windows: ["weekly"], resetsAt: resetEpoch, detail: ""});
  const afterRepeat = sentNotifications.length;
  // Recovery clears banner, button, and document.title.
  ctx.renderUsageAlert(null);
  const cleared = {hidden: box.hidden, text: text.textContent, title: doc.title, btnHidden: btn.hidden};
  console.log(JSON.stringify({
    sameDayHasComma: sameDay.includes(","),
    multiDay,
    beforePerm,
    afterGrant,
    afterRepeat,
    sentNotifications,
    cleared,
  }));
})();
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertFalse(res["beforePerm"]["hidden"])
        self.assertIn("weekly at 0%", res["beforePerm"]["text"])
        self.assertIn("Resets ", res["beforePerm"]["text"])
        self.assertFalse(res["beforePerm"]["btnHidden"])
        self.assertTrue(res["beforePerm"]["title"].startswith("\u26a0 Out of usage \u00b7 Symphony"))
        self.assertEqual(res["beforePerm"]["notifCount"], 0)
        self.assertEqual(res["afterGrant"], {"btnHidden": True, "notifCount": 1})
        self.assertEqual(res["afterRepeat"], 1)
        self.assertEqual(len(res["sentNotifications"]), 1)
        self.assertTrue(res["cleared"]["hidden"])
        self.assertEqual(res["cleared"]["title"], "Symphony")
        self.assertTrue(any(c.isalpha() for c in res["multiDay"]))

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_update_notice_renders_safe_compare_link_and_commit_summary(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => ({
  tag, id, textContent: "", innerHTML: "", value: "", hidden: true, disabled: false, open: false,
  dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
  classList: {add() {}, remove() {}},
  addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
  setAttribute(n, v) { this[n] = String(v); }, removeAttribute(n) { delete this[n]; },
  toggleAttribute() {}, focus() {}, setSelectionRange() {}, contains() { return false; },
  closest() { return makeNode("aside"); },
  append(...items) { this.children.push(...items); },
  prepend(...items) { this.children.unshift(...items); },
  replaceChildren(...items) { this.children = items; },
  querySelector() { return null; }, querySelectorAll() { return []; },
});
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode("div", id));
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const ctx = vm.createContext({document: doc, setInterval: () => 0, setTimeout, clearTimeout});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const run = (code) => vm.runInContext(code, ctx);
const details = doc.getElementById("update-details");
const shaA = "1".repeat(40);
const shaB = "2".repeat(40);

// Unsafe compareUrl is rejected; plain banner still shows cleanly.
run(`updateState = {updateAvailable: true, draining: false, update: {runningCommit: "${shaA}", availableCommit: "${shaB}", compareUrl: "javascript:alert(1)", commits: []}}; renderUpdateNotice();`);
const unsafeHidden = details.hidden;

// Valid single-commit release renders safe link + subject.
run(`updateState = {updateAvailable: true, draining: false, update: {runningCommit: "${shaA}", availableCommit: "${shaB}", compareUrl: "https://github.com/KianBahasadri/clusterfork/compare/${shaA}...${shaB}", commitCount: 1, commits: ["<script>KIAN-1132</script> Fix"]}}; renderUpdateNotice();`);
const single = {
  hidden: details.hidden,
  href: details.children[0]?.href,
  rel: details.children[0]?.rel,
  target: details.children[0]?.target,
  linkText: details.children[0]?.textContent,
  commitText: details.children.map((n) => n.textContent),
};

// Multi-commit release renders list + remaining count.
run(`updateState = {updateAvailable: true, draining: false, update: {runningCommit: "${shaA}", availableCommit: "${shaB}", compareUrl: "https://github.com/KianBahasadri/clusterfork/compare/${shaA}...${shaB}", commitCount: 4, commits: ["C4", "C3"]}}; renderUpdateNotice();`);
const multi = {
  listItems: details.children[2]?.children?.map((n) => n.textContent),
};

run(`updateState = {updateAvailable: false, draining: false, update: null}; renderUpdateNotice();`);
console.log(JSON.stringify({unsafeHidden, single, multi, clearedHidden: details.hidden}));
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertTrue(res["unsafeHidden"])
        self.assertFalse(res["single"]["hidden"])
        self.assertEqual(res["single"]["target"], "_blank")
        self.assertEqual(res["single"]["rel"], "noreferrer")
        self.assertEqual(res["single"]["linkText"], "1111111\u21922222222")
        self.assertIn("<script>KIAN-1132</script> Fix", res["single"]["commitText"])
        self.assertEqual(res["multi"]["listItems"], ["C4", "C3", "(+2 more)"])
        self.assertTrue(res["clearedHidden"])

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_auto_reload_triggers_when_idle_and_defers_for_draft_image_dialog_or_send(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");

function boot(opts = {}) {
  const nodes = new Map();
  const makeNode = (tag = "div", id = "") => ({
    tag, id, textContent: "", innerHTML: "", value: "", hidden: true, disabled: false, open: false,
    dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
    classList: {add() {}, remove() {}},
    addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
    setAttribute(n, v) { this[n] = String(v); }, removeAttribute(n) { delete this[n]; },
    toggleAttribute() {}, focus() {}, setSelectionRange() {}, contains() { return false; },
    closest() { return makeNode("aside"); },
    append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    replaceChildren(...items) { this.children = items; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  });
  let reloads = 0;
  let releaseFetch = null;
  const doc = {
    activeElement: null,
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, makeNode(id.endsWith("dialog") ? "dialog" : "div", id));
      return nodes.get(id);
    },
    createElement(tag) { return makeNode(tag); },
    createElementNS(_ns, tag) { return makeNode(tag); },
    querySelector(sel) {
      if (sel === "dialog[open]") {
        const cfg = this.getElementById("config-dialog");
        if (cfg.open) return cfg;
        const issue = this.getElementById("issue-dialog");
        if (issue.open) return issue;
        return null;
      }
      return this.getElementById("main");
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const fetchFn = opts.holdSubmit
    ? async () => {
        await new Promise((r) => { releaseFetch = r; });
        return {ok: true, json: async () => ({message: {id: 1, role: "user", text: "hi", images: []}})};
      }
    : async () => ({ok: true, json: async () => ({})});
  const ctx = vm.createContext({
    document: doc,
    location: {reload() { reloads += 1; }},
    URL: {createObjectURL: () => "blob:1", revokeObjectURL() {}},
    fetch: fetchFn,
    setInterval: () => 0, setTimeout, clearTimeout,
  });
  vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
  return {ctx, doc, getReloads: () => reloads, releaseFetch: () => releaseFetch?.()};
}

(async () => {
  // 1. Missing/unchanged version never reloads; mismatch reloads once when idle.
  const b1 = boot();
  b1.ctx.checkServerVersion("");
  b1.ctx.checkServerVersion(null);
  b1.ctx.checkServerVersion("v1");
  b1.ctx.checkServerVersion("v1");
  const unchanged = b1.getReloads();
  b1.ctx.checkServerVersion("v2");
  b1.ctx.checkServerVersion("v2");
  b1.ctx.checkServerVersion("v3");
  const idleOnce = b1.getReloads();

  // 2. Draft text defers reload and shows notice; clearing text auto-reloads.
  const b2 = boot();
  b2.ctx.checkServerVersion("v1");
  b2.doc.getElementById("message").value = "unsent";
  b2.ctx.checkServerVersion("v2");
  const draftDeferred = {reloads: b2.getReloads(), noticeHidden: b2.doc.getElementById("reload-notice").hidden};
  b2.doc.getElementById("message").value = "   ";
  b2.doc.getElementById("message").listeners.input[0]();
  const afterDraftClear = b2.getReloads();

  // 3. Open dialog defers reload; closing dialog auto-reloads.
  const b3 = boot();
  b3.ctx.checkServerVersion("v1");
  b3.doc.getElementById("issue-dialog").open = true;
  b3.ctx.checkServerVersion("v2");
  const dialogDeferred = {reloads: b3.getReloads(), noticeHidden: b3.doc.getElementById("reload-notice").hidden};
  b3.doc.getElementById("issue-dialog").open = false;
  b3.doc.getElementById("issue-dialog").listeners.close[0]();
  const afterDialogClose = b3.getReloads();

  // 4. Pending image defers reload; removing image auto-reloads.
  const b4 = boot();
  b4.ctx.checkServerVersion("v1");
  b4.ctx.attachImage({type: "image/png", size: 10, name: "a.png"});
  b4.ctx.checkServerVersion("v2");
  const imgDeferred = b4.getReloads();
  vm.runInContext("removeAttachment(pendingImages[0]);", b4.ctx);
  const afterImgClear = b4.getReloads();

  // 5. In-flight submit defers reload (even after draft cleared) until submit finishes.
  const b5 = boot({holdSubmit: true});
  b5.ctx.checkServerVersion("v1");
  b5.doc.getElementById("message").value = "send me";
  const sub = b5.doc.getElementById("composer").listeners.submit[0]({preventDefault() {}});
  await new Promise((r) => setImmediate(r));
  b5.ctx.checkServerVersion("v2");
  const sendingDeferred = b5.getReloads();
  b5.releaseFetch();
  await sub;
  const afterSubmit = b5.getReloads();

  // 6. Manual Reload now works even while draft is non-empty.
  const b6 = boot();
  b6.ctx.checkServerVersion("v1");
  b6.doc.getElementById("message").value = "keep";
  b6.ctx.checkServerVersion("v2");
  b6.doc.getElementById("reload-action").listeners.click[0]();
  const manualReload = b6.getReloads();

  console.log(JSON.stringify({
    unchanged, idleOnce, draftDeferred, afterDraftClear,
    dialogDeferred, afterDialogClose, imgDeferred, afterImgClear,
    sendingDeferred, afterSubmit, manualReload,
  }));
})();
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["unchanged"], 0)
        self.assertEqual(res["idleOnce"], 1)
        self.assertEqual(res["draftDeferred"], {"reloads": 0, "noticeHidden": False})
        self.assertEqual(res["afterDraftClear"], 1)
        self.assertEqual(res["dialogDeferred"], {"reloads": 0, "noticeHidden": False})
        self.assertEqual(res["afterDialogClose"], 1)
        self.assertEqual(res["imgDeferred"], 0)
        self.assertEqual(res["afterImgClear"], 1)
        self.assertEqual(res["sendingDeferred"], 0)
        self.assertEqual(res["afterSubmit"], 1)
        self.assertEqual(res["manualReload"], 1)

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_working_turn_timer_picks_stable_css_animation_and_clears_when_idle(self):
        app = Path(__file__).resolve().parents[1] / "symphony/web/app.js"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (id) => ({
  id, textContent: "", value: "", hidden: true, disabled: false, open: false,
  dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
  addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
  classList: {add() {}, remove() {}}, querySelector() { return null; },
  querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {},
  toggleAttribute() {}, focus() {}, closest() { return makeNode("aside"); },
  append(...n) { this.children.push(...n); }, prepend(...n) { this.children.unshift(...n); },
  replaceChildren(...n) { this.children = n; },
});
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, makeNode(id));
    return nodes.get(id);
  },
  createElement() { return makeNode("tmp"); },
  createElementNS() { return makeNode("svg"); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
let nowMs = 100000;
const RealDate = Date;
const FakeDate = class extends RealDate {
  static now() { return nowMs; }
};
const rolls = [0.05, 0.30, 0.60, 0.90, 0.35, 0.85];
let rollIdx = 0;
const fakeMath = Object.create(Math);
fakeMath.random = () => rolls[rollIdx++] ?? 0;

let writes = 0;
const rawTiming = makeNode("turn-timing");
let animValue;
Object.defineProperty(rawTiming.dataset, "animation", {
  configurable: true,
  enumerable: true,
  get() { return animValue; },
  set(v) { writes += 1; animValue = v; },
});
nodes.set("turn-timing", rawTiming);

const ctx = vm.createContext({
  document: doc,
  Date: FakeDate,
  Math: fakeMath,
  setInterval: () => 0,
  setTimeout,
  clearTimeout,
  console,
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);

const snap = () => ({hidden: rawTiming.hidden, text: rawTiming.textContent, anim: rawTiming.dataset.animation || "", writes});

// Turn 1 (startedAt=95): picks index 0 ("rainbow"), stays stable across polls, elapsed seconds, and project switches.
vm.runInContext('coordinator = {rateLimits: {}, turnTiming: {startedAt: 95}}; canInterrupt = true; compacting = ""; showCoordinatorStatus();', ctx);
const t1Start = snap();
nowMs = 104000;
vm.runInContext('showCoordinatorStatus();', ctx);
const t1Poll = snap();
vm.runInContext('resetChatView("other"); showCoordinatorStatus();', ctx);
const t1Switch = snap();

// Completed ("Worked for"): clears animation.
vm.runInContext('coordinator = {rateLimits: {}, turnTiming: {startedAt: 95, completedAt: 105, durationMs: 10000}}; canInterrupt = false; showCoordinatorStatus();', ctx);
const afterComplete = snap();

// Same startedAt after idle poll picks a fresh random animation -> index 1 ("spinner").
vm.runInContext('coordinator = {rateLimits: {}, turnTiming: {startedAt: 95}}; canInterrupt = true; showCoordinatorStatus();', ctx);
const sameStartAfterIdle = snap();

// Interrupted / canInterrupt=false without durationMs clears animation.
vm.runInContext('canInterrupt = false; showCoordinatorStatus();', ctx);
const afterInterrupt = snap();

// New active turn -> index 2 ("pulse"), then direct jump to startedAt=120 with no idle poll -> index 3 ("shimmer").
vm.runInContext('coordinator = {rateLimits: {}, turnTiming: {startedAt: 110}}; canInterrupt = true; showCoordinatorStatus();', ctx);
const turnPulse = snap();
vm.runInContext('coordinator = {rateLimits: {}, turnTiming: {startedAt: 120}}; canInterrupt = true; showCoordinatorStatus();', ctx);
const turnShimmer = snap();

// Missing turnTiming clears animation.
vm.runInContext('coordinator = {rateLimits: {}}; canInterrupt = true; showCoordinatorStatus();', ctx);
const missingTiming = snap();

// Compaction running clears animation even if canInterrupt/startedAt happen to be set; queued still animates.
vm.runInContext('coordinator = {rateLimits: {}, turnTiming: {startedAt: 130}}; canInterrupt = true; compacting = "queued"; showCoordinatorStatus();', ctx);
const queuedAnim = snap();
vm.runInContext('compacting = "running"; showCoordinatorStatus();', ctx);
const compactingClears = snap();
vm.runInContext('compacting = ""; showCoordinatorStatus();', ctx);
const afterCompactingIdleStillStarted = snap();

console.log(JSON.stringify({
  t1Start, t1Poll, t1Switch, afterComplete, sameStartAfterIdle,
  afterInterrupt, turnPulse, turnShimmer, missingTiming,
  queuedAnim, compactingClears, afterCompactingIdleStillStarted,
}));
"""
        run = subprocess.run(["node", "-e", probe, str(app)], text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["t1Start"]["anim"], "rainbow")
        self.assertEqual(res["t1Start"]["writes"], 1)
        self.assertEqual(res["t1Poll"]["text"], "Working for 9s")
        self.assertEqual(res["t1Poll"]["anim"], "rainbow")
        self.assertEqual(res["t1Poll"]["writes"], 1)
        self.assertEqual(res["t1Switch"]["anim"], "rainbow")
        self.assertEqual(res["t1Switch"]["writes"], 1)
        self.assertEqual(res["afterComplete"]["anim"], "")
        self.assertTrue(res["afterComplete"]["text"].startswith("Worked for 10s"))
        self.assertEqual(res["sameStartAfterIdle"]["anim"], "spinner")
        self.assertEqual(res["afterInterrupt"]["anim"], "")
        self.assertEqual(res["turnPulse"]["anim"], "pulse")
        self.assertEqual(res["turnShimmer"]["anim"], "shimmer")
        self.assertEqual(res["missingTiming"]["anim"], "")
        self.assertEqual(res["queuedAnim"]["anim"], "spinner")
        self.assertEqual(res["queuedAnim"]["text"], "Working for 0s · Compaction queued")
        self.assertEqual(res["compactingClears"]["anim"], "")
        self.assertEqual(res["afterCompactingIdleStillStarted"]["anim"], "shimmer")

    def test_turn_timing_css_defines_tabular_nums_all_animations_and_reduced_motion(self):
        css = (Path(__file__).resolve().parents[1] / "symphony/web/style.css").read_text(encoding="utf-8")
        self.assertIn("font-variant-numeric: tabular-nums;", css)
        for variant in ("rainbow", "spinner", "pulse", "shimmer"):
            self.assertIn(f'#turn-timing[data-animation="{variant}"]', css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("-webkit-background-clip: border-box;", css)

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_header_chips_image_modal_compaction_and_issue_status_ui(self):
        web = Path(__file__).resolve().parents[1] / "symphony/web"
        probe = r"""
const fs = require("fs");
const vm = require("vm");
const nodes = new Map();
const makeNode = (tag = "div", id = "") => {
  const node = {
    tag, id, textContent: "", innerHTML: "", value: "", hidden: false, disabled: false, open: false,
    draggable: false, tabIndex: -1, href: "", src: "", alt: "", title: "", className: "", scrollLeft: 0,
    dataset: {}, style: {setProperty() {}}, listeners: {}, children: [],
    classList: {add(...c) { node.className = [node.className, ...c].filter(Boolean).join(" "); }, remove() {}},
    addEventListener(t, h) { (this.listeners[t] ||= []).push(h); },
    setAttribute(n, v) { this[n] = String(v); },
    getAttribute(n) { return this[n]; },
    removeAttribute(n) { delete this[n]; },
    toggleAttribute(n, f) { if (f) this[n] = ""; else delete this[n]; },
    focus() { doc.activeElement = this; },
    setSelectionRange() {},
    contains(other) {
      let cur = other;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentNode;
      }
      return false;
    },
    closest(sel) {
      if (sel === "aside") return this._aside ||= makeNode("aside");
      if (sel === ".project-chip" && this.className.includes("project-chip")) return this;
      return this.hidden ? this : null;
    },
    getBoundingClientRect() { return {left: 0, right: 100, top: 0, bottom: 30, width: 100, height: 30}; },
    append(...items) { this.children.push(...items); node._relink(); },
    prepend(...items) { this.children.unshift(...items); node._relink(); },
    replaceChildren(...items) { this.children = items; node._relink(); },
    _relink() { for (const c of this.children) if (c && typeof c === "object") c.parentNode = this; },
    showModal() { this.open = true; },
    close() { this.open = false; for (const h of this.listeners.close || []) h(); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const out = [];
      const match = (n) => {
        if (!n || typeof n !== "object") return false;
        if (sel === 'button[role="tab"]') return n.tag === "button" && n.role === "tab";
        if (sel === '[role="option"]') return n.role === "option";
        if (sel === '[aria-selected="true"]') return n["aria-selected"] === "true";
        if (sel.startsWith(".")) return String(n.className || "").split(/\s+/).includes(sel.slice(1));
        if (sel.startsWith("#")) return n.id === sel.slice(1);
        return false;
      };
      for (const c of this.children) {
        if (!c || typeof c !== "object") continue;
        if (match(c)) out.push(c);
        if (c.querySelectorAll) out.push(...c.querySelectorAll(sel));
      }
      return out;
    },
  };
  return node;
};
const doc = {
  activeElement: null,
  getElementById(id) {
    if (!nodes.has(id)) {
      const created = nodes.get("repos")?.querySelector?.("#" + id);
      if (created) return created;
      nodes.set(id, makeNode(id.endsWith("dialog") ? "dialog" : "div", id));
    }
    return nodes.get(id);
  },
  createElement(tag) { return makeNode(tag); },
  createElementNS(_ns, tag) { return makeNode(tag); },
  querySelector() { return this.getElementById("main"); },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const store = new Map();
const localStorage = {
  getItem(k) { return store.has(k) ? store.get(k) : null; },
  setItem(k, v) { store.set(k, String(v)); },
};
const statusPosts = [];
const fetch = async (path, options = {}) => {
  if (path === "/api/issue-status") {
    statusPosts.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({issue: "KIAN-1", state: {id: "s-todo", name: "Todo", type: "unstarted", color: "#00f"}, updatedAt: "2026-11-28T13:00:00Z"})};
  }
  return {ok: true, json: async () => ({
    messages: [], busy: false, canInterrupt: false, stream: "", streamHtml: "",
    tasks: [], signedIn: true, login: null, paused: "", view: "", repo: "",
    repoActivity: {}, coordinator: {}, issueError: "", updateAvailable: false, draining: false,
  })};
};
const ctx = vm.createContext({
  document: doc, localStorage, fetch, URL: globalThis.URL, URLSearchParams: globalThis.URLSearchParams,
  location: {origin: "https://symphony.test"}, setInterval: () => 0, setTimeout, clearTimeout, console,
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
ctx.poll = () => {};
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), ctx);
(async () => {
  const projects = [
    {id: "proj-a", name: "Alpha", icon: "", repo: "owner/a", status: {name: "In Progress", type: "started", color: "#f2c94c"}, issues: [
      {identifier: "KIAN-1", title: "One", url: "https://linear.app/a", state: {name: "Backlog", type: "backlog", color: "#bbb"}, priority: 2, priorityLabel: "High", estimate: 2, dueDate: null, labels: [], plan: null, planHtml: "", project: "Alpha", projectId: "proj-a"},
    ]},
    {id: "proj-paused", name: "Sleeping", icon: "", repo: "owner/p", status: {name: "Paused", type: "backlog", color: "#888"}, issues: []},
    {id: "proj-b", name: "Beta", icon: "", repo: "owner/b", status: {name: "Backlog", type: "backlog", color: "#f2994a"}, issues: []},
  ];
  vm.runInContext('workByRepo = {"owner/a": "working"}', ctx);
  ctx.updateProjects(projects, true);
  const reposEl = doc.getElementById("repos");
  const allTabs = reposEl.querySelectorAll('button[role="tab"]').map((t) => ({
    id: t.id, project: t.dataset.project, paused: t.dataset.paused || "",
    status: t.querySelector(".project-status")?.dataset.status || "",
    symbol: t.querySelector(".project-status")?.textContent || "",
  }));
  const pausedListHiddenBefore = doc.getElementById("paused-projects-list").hidden;
  doc.getElementById("paused-projects-toggle").listeners.click[0]();
  const pausedListHiddenAfter = doc.getElementById("paused-projects-list").hidden;

  // Drag Beta before Alpha
  const alphaTab = reposEl.querySelectorAll('button[role="tab"]').find((t) => t.dataset.project === "proj-a");
  const betaTab = reposEl.querySelectorAll('button[role="tab"]').find((t) => t.dataset.project === "proj-b");
  betaTab.listeners.dragstart[0]({clientX: 50, dataTransfer: {setData() {}, effectAllowed: ""}});
  reposEl.listeners.drop[0]({target: alphaTab, clientX: 10, clientY: 15, preventDefault() {}});
  const savedOrder = JSON.parse(localStorage.getItem("symphony.projectOrder") || "[]");

  // Switching to All repositories keeps combined issue + project pill
  ctx.selectProject("");
  const projectPills = doc.getElementById("backlog").querySelectorAll(".issue-project").length;

  // Image modal opens and restores focus on close
  const gallery = ctx.imageGallery([{name: "abc.png", type: "image/png"}], "message-images");
  const imgBtn = gallery.children[0];
  imgBtn.focus();
  imgBtn.listeners.click[0]();
  const imgOpen = doc.getElementById("image-dialog").open;
  doc.getElementById("image-close").listeners.click[0]();
  const imgClosed = !doc.getElementById("image-dialog").open;
  const restoredImgFocus = doc.activeElement === imgBtn;

  // Expandable compaction message
  const compactEl = ctx.createMessage({
    id: 10, role: "system", text: "Coordinator context compacted.",
    meta: {kind: "compaction", compactionText: "Summary line", compactionHtml: "<p>Summary line</p>"},
  });
  const compactDetails = compactEl.querySelector(".compaction-details");

  // Issue status picker updates Linear status via /api/issue-status
  const layout = ctx.issueLayout({
    identifier: "KIAN-1", title: "One", descriptionHtml: "", priority: 2, priorityLabel: "High",
    assignee: null, labels: [], project: null, team: {name: "Kian", states: [
      {id: "s-back", name: "Backlog", type: "backlog", color: "#bbb"},
      {id: "s-todo", name: "Todo", type: "unstarted", color: "#00f"},
    ]},
    state: {id: "s-back", name: "Backlog", type: "backlog", color: "#bbb"},
    children: [], relations: [], inverseRelations: [], releases: [], subscribers: [],
    attachments: [], documents: [], comments: [], history: [], reactions: [], previousIdentifiers: [],
    creator: null,
  });
  const trigger = layout.querySelector("#issue-status-trigger");
  trigger.listeners.click[0]();
  const options = layout.querySelectorAll('[role="option"]');
  await options[1].listeners.click[0]();

  console.log(JSON.stringify({
    allTabs, pausedListHiddenBefore, pausedListHiddenAfter, savedOrder, projectPills,
    imgOpen, imgClosed, restoredImgFocus,
    compactSummary: compactDetails?.children?.[0]?.textContent || "",
    compactHtml: compactDetails?.children?.[1]?.innerHTML || "",
    compactOpen: compactDetails?.open,
    statusPosts,
    triggerAfter: trigger.textContent || trigger.querySelector(".issue-status-current")?.children?.[1]?.textContent || "",
  }));
})();
"""
        run = subprocess.run(
            ["node", "-e", probe, str(web / "panes.js"), str(web / "app.js"), str(web / "issue.js")],
            text=True, capture_output=True, check=False, timeout=15,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        res = json.loads(run.stdout)
        self.assertEqual(res["allTabs"][0], {
            "id": "project-tab-all", "project": "", "paused": "", "status": "", "symbol": "",
        })
        self.assertEqual([t["project"] for t in res["allTabs"][1:]], ["proj-a", "proj-b", "proj-paused"])
        self.assertEqual([t["status"] for t in res["allTabs"][1:]], ["working", "idle", "stopped"])
        self.assertEqual(res["allTabs"][2]["symbol"], "Zzz")
        self.assertTrue(res["pausedListHiddenBefore"])
        self.assertFalse(res["pausedListHiddenAfter"])
        self.assertEqual(res["savedOrder"], ["proj-b", "proj-a", "proj-paused"])
        self.assertEqual(res["projectPills"], 1)
        self.assertTrue(res["imgOpen"])
        self.assertTrue(res["imgClosed"])
        self.assertTrue(res["restoredImgFocus"])
        self.assertEqual(res["compactSummary"], "Coordinator context compacted.")
        self.assertEqual(res["compactHtml"], "<p>Summary line</p>")
        self.assertFalse(res["compactOpen"])
        self.assertEqual(res["statusPosts"], [{"issue": "KIAN-1", "stateId": "s-todo"}])


if __name__ == "__main__":
    unittest.main()
