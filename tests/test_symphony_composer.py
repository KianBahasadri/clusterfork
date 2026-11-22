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
                             capture_output=True, check=False)
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


if __name__ == "__main__":
    unittest.main()
