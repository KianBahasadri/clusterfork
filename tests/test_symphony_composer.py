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
  querySelector() { return null; },
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
const fetch = async (_path, options) => {
  if (failNext) {
    failNext = false;
    return {ok: false, json: async () => ({error: "Fixture rejected the message."})};
  }
  sent.push(JSON.parse(options.body));
  return {ok: true, json: async () => ({})};
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

async function reset() {
  run('pendingImages.length = 0; messageField.value = ""; selected = "project-a"; history.length = 0;')
  context.__encoders = [];
  sent.length = 0;
  revoked.length = 0;
  failNext = false;
}

(async () => {
  const results = {};

  await reset();
  run('messageField.value = "First message"; attachImage({type: "image/png", size: 10, name: "one.png"});')
  const first = handler({preventDefault() {}});
  await tick();
  run('messageField.value = "Second draft"; selected = "project-b"; attachImage({type: "image/png", size: 20, name: "two.png"});')
  context.__encoders[0]({type: "image/png", data: "AAAA"});
  await first;
  results.delayed = {
    sent: sent[0],
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    revoked: revoked.slice(),
  };

  await reset();
  run('messageField.value = "Keep me"; attachImage({type: "image/png", size: 10, name: "keep.png"}); imagePayload = () => Promise.reject(new Error("Read failed"));')
  await handler({preventDefault() {}});
  results.readFailure = {
    sent: sent.length,
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    error: run('byId("request-error").textContent'),
  };
  run('imagePayload = () => new Promise((resolve) => __encoders.push(resolve));')

  await reset();
  failNext = true;
  run('messageField.value = "Retry me"; attachImage({type: "image/png", size: 10, name: "retry.png"});')
  const failed = handler({preventDefault() {}});
  await tick();
  context.__encoders[0]({type: "image/png", data: "BBBB"});
  await failed;
  results.failed = {
    sent: sent.length,
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
    error: run('byId("request-error").textContent'),
  };
  run('messageField.value = "Retry with changes"; selected = "project-c";')
  const retried = handler({preventDefault() {}});
  await tick();
  context.__encoders[1]({type: "image/png", data: "CCCC"});
  await retried;
  results.retried = {
    sent: sent[sent.length - 1],
    draft: run("messageField.value"),
    pending: run("pendingImages.length"),
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
        self.assertEqual(delayed["sent"], {
            "text": "First message", "project": "project-a",
            "images": [{"type": "image/png", "data": "AAAA"}],
        })
        self.assertEqual(delayed["draft"], "Second draft")
        self.assertEqual(delayed["pending"], 1)
        self.assertEqual(delayed["revoked"], ["blob:fixture-1"])

        self.assertEqual(result["readFailure"], {
            "sent": 0, "draft": "Keep me", "pending": 1, "error": "Read failed",
        })
        self.assertEqual(result["failed"], {
            "sent": 0, "draft": "Retry me", "pending": 1,
            "error": "Fixture rejected the message.",
        })
        self.assertEqual(result["retried"], {
            "sent": {
                "text": "Retry with changes", "project": "project-c",
                "images": [{"type": "image/png", "data": "CCCC"}],
            },
            "draft": "", "pending": 0,
        })


if __name__ == "__main__":
    unittest.main()
