"""Exercise Symphony chat rendering and scheduling in managed Chromium."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import unittest
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - Playwright is optional outside the image.
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1] / "symphony/web"


def browser_available():
    if sync_playwright is None:
        return False
    managed = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if managed and list(Path(managed).glob("chromium-*")):
        return True
    return bool(shutil.which("chromium") or shutil.which("chromium-browser")
                or shutil.which("google-chrome") or shutil.which("chrome"))


@unittest.skipUnless(browser_available(), "A local Playwright Chromium is required.")
class ChatBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = None
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
        except Exception as error:  # pragma: no cover - environment-dependent skip.
            if cls.playwright is not None:
                cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium could not start: {error}") from error

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1200, "height": 800})
        self.load_app(self.page)

    def load_app(self, page):
        html = (ROOT / "index.html").read_text()
        html = re.sub(r'<script src="[^"]+" defer></script>\s*', "", html)
        html = html.replace('<link rel="stylesheet" href="/style.css">', "")
        page.set_content(html, wait_until="domcontentloaded")
        page.add_style_tag(path=str(ROOT / "style.css"))
        page.add_script_tag(path=str(ROOT / "panes.js"))
        page.add_script_tag(path=str(ROOT / "app.js"))

    def issue(self, identifier, state="Backlog", project="project-a", **extra):
        state_type = {"Backlog": "backlog", "Todo": "unstarted",
                      "In Progress": "started", "Done": "completed"}[state]
        data = {
            "identifier": identifier, "title": "Title for " + identifier,
            "url": "https://linear.app/issue/" + identifier, "description": "",
            "priority": 2, "priorityLabel": "High", "estimate": 2, "dueDate": None,
            "createdAt": "2026-11-01T00:00:00Z", "updatedAt": "2026-11-01T00:00:00Z",
            "completedAt": None, "labels": [], "plan": None, "planHtml": "",
            "state": {"name": state, "type": state_type, "color": "#888888"},
            "project": "Project A", "projectId": project,
        }
        data.update(extra)
        return data

    def render_cards(self, projects, tasks=()):
        self.page.evaluate("""({projects, taskRows}) => {
          tasks = taskRows;
          expandedPlans.clear();
          selected = projects[0][0];
          issueCache = new Map(projects);
          issueCache.set("", projects.flatMap(([, rows]) => rows));
          renderPanes();
        }""", {"projects": projects, "taskRows": list(tasks)})

    def hold_chat_requests(self):
        self.page.evaluate("""() => {
          window.__requests = [];
          window.__pending = [];
          window.__opened = [];
          window.openIssue = (identifier) => window.__opened.push(identifier);
          window.fetch = (path, options) => {
            const body = JSON.parse(options.body);
            const request = {path, body};
            window.__requests.push(request);
            return new Promise((resolve) => {
              window.__pending.push({...request, resolve});
            });
          };
          window.__respond = (index, id, text, images = []) => {
            const item = window.__pending.splice(index, 1)[0];
            item.resolve({ok: true, json: async () => ({message: {
              id, role: "user", text: text ?? item.body.text,
              images,
            }})});
          };
          window.__reject = (index, message) => {
            const item = window.__pending.splice(index, 1)[0];
            item.resolve({ok: false, json: async () => ({error: message})});
          };
        }""")

    @staticmethod
    def stub_config_fetch(page, agents=None, error="", config=None):
        cfg = config or {
            "opencodeGo": {"configured": False, "malformed": False, "busy": False},
            "codex": {"signedIn": True, "account": {"type": "chatgpt"}, "login": None, "busy": False},
        }
        page.evaluate("""({agents, error, cfg}) => {
          window.fetch = async (path, options) => {
            if (path === "/api/agents") {
              return error
                ? {ok: false, json: async () => ({error})}
                : {ok: true, json: async () => ({content: agents})};
            }
            if (path === "/api/config") {
              if ((options?.method || "GET") === "POST") {
                return {ok: true, json: async () => cfg};
              }
              return {ok: true, json: async () => cfg};
            }
            return {ok: true, json: async () => ({
              messages: [], busy: false, canInterrupt: false, stream: "", streamHtml: "",
              tasks: [], signedIn: true, login: null, paused: "", view: "",
              codex: {}, issueError: "", updateAvailable: false, draining: false,
            })};
          };
        }""", {"agents": agents, "error": error, "cfg": cfg})

    def tearDown(self):
        self.page.close()

    def test_live_stream_preserves_nodes_and_completes_once(self):
        result = self.page.evaluate("""() => {
          updateStream("Hello", "<p>Hello</p>");
          const first = document.querySelector("#stream p");
          const text = first.firstChild;
          updateStream("Hello world", "<p>Hello world</p>");
          const preserved = document.querySelector("#stream p") === first
            && first.firstChild === text;
          updateStream("Hello world\\n\\n- one\\n- two",
            "<p>Hello world</p>\\n<ul>\\n<li>one</li>\\n<li>two</li>\\n</ul>");
          const listPreserved = document.querySelector("#stream p") === first;
          updateStream("Hello world\\n\\n- one\\n- two\\n\\n| A | B |\\n| - | - |\\n| 1 | 2 |",
            "<p>Hello world</p>\\n<ul>\\n<li>one</li>\\n<li>two</li>\\n</ul>\\n"
            + "<table>\\n<thead>\\n<tr>\\n<th>A</th>\\n<th>B</th>\\n</tr>\\n</thead>\\n"
            + "<tbody>\\n<tr>\\n<td>1</td>\\n<td>2</td>\\n</tr>\\n</tbody>\\n</table>");
          updateStream("Hello world\\n\\n- one\\n- two\\n\\n| A | B |\\n| - | - |\\n| 1 | 2 |\\n| 3 | 4 |",
            "<p>Hello world</p>\\n<ul>\\n<li>one</li>\\n<li>two</li>\\n</ul>\\n"
            + "<table>\\n<thead>\\n<tr>\\n<th>A</th>\\n<th>B</th>\\n</tr>\\n</thead>\\n"
            + "<tbody>\\n<tr>\\n<td>1</td>\\n<td>2</td>\\n</tr>\\n<tr>\\n<td>3</td>\\n<td>4</td>\\n</tr>\\n"
            + "</tbody>\\n</table>");
          const tableRows = document.querySelectorAll("#stream tbody tr").length;
          const tablePreserved = document.querySelector("#stream p") === first;
          updateStream("```text\\n" + "line\\n".repeat(8) + "```",
            "<pre><code class=\\"language-text\\">" + "line\\n".repeat(8) + "</code></pre>");
          const style = document.createElement("style");
          style.textContent = "#stream pre { max-height: 40px; overflow: auto; }";
          document.head.append(style);
          const pre = document.querySelector("#stream pre");
          pre.scrollTop = 12;
          updateStream("```text\\n" + "line\\n".repeat(9) + "```",
            "<pre><code class=\\"language-text\\">" + "line\\n".repeat(9) + "</code></pre>");
          return {
            preserved, listPreserved, tableRows, tablePreserved,
            prePreserved: document.querySelector("#stream pre") === pre,
            code: document.querySelector("#stream pre code").textContent,
            codeScroll: pre.scrollTop,
          };
        }""")
        self.assertEqual(result, {
            "preserved": True, "listPreserved": True,
            "tableRows": 2, "tablePreserved": True,
            "prePreserved": True,
            "code": "line\n" * 9,
            "codeScroll": 12,
        })

        final = self.page.evaluate("""() => {
          const text = "First paragraph.\\n\\nSecond paragraph with `code`.";
          const html = "<p>First paragraph.</p>\\n<p>Second paragraph with <code>code</code>.</p>";
          queueStream(text, html);
          flushChat();
          const live = document.querySelector("#stream");
          const before = live.textContent;
          queueMessage({id: 1, role: "assistant", text, html});
          queueStream("", "");
          flushChat();
          return {
            before,
            after: document.querySelector("#stream").textContent,
            responses: document.querySelectorAll("#messages .message.assistant").length,
            message: document.querySelector("#messages .message.assistant .message-text").textContent,
          };
        }""")
        self.assertEqual(final["after"], "")
        self.assertEqual(final["responses"], 1)
        self.assertEqual(final["message"], "First paragraph.\nSecond paragraph with code.")
        self.assertIn("Second paragraph", final["before"])

        interrupted = self.page.evaluate("""() => {
          queueStream("stale partial", "<p>stale partial</p>");
          flushChat();
          queueStream("", "");
          flushChat();
          return {
            text: document.querySelector("#stream").textContent,
            pending: pendingStream,
            frame: chatFrame,
          };
        }""")
        self.assertEqual(interrupted, {"text": "", "pending": None, "frame": None})

    def test_acknowledgements_reconcile_by_id_without_moving_the_cursor(self):
        result = self.page.evaluate("""() => {
          lastMessage = 0;
          queueMessage({id: 5, role: "user", text: "acknowledged"});
          flushChat();
          const afterAck = {cursor: lastMessage, ids: [...document.querySelectorAll("#messages .message")]
            .map((node) => node.dataset.messageId)};
          queueMessage({id: 2, role: "system", text: "earlier"});
          queueMessage({id: 3, role: "worker", text: "same"});
          queueMessage({id: 4, role: "worker", text: "same"});
          queueMessage({id: 5, role: "user", text: "acknowledged"});
          queueMessage({id: 3, role: "worker", text: "same"});
          flushChat();
          const nodes = [...document.querySelectorAll("#messages .message")];
          return {
            afterAck,
            ids: nodes.map((node) => node.dataset.messageId),
            texts: nodes.map((node) => node.querySelector(".message-text").textContent),
          };
        }""")
        self.assertEqual(result["afterAck"]["cursor"], 0)
        self.assertEqual(result["afterAck"]["ids"], ["5"])
        self.assertEqual(result["ids"], ["2", "3", "4", "5"])
        self.assertEqual(result["texts"], ["earlier", "same", "same", "acknowledged"])

        duplicate = self.page.evaluate("""() => {
          queueMessage({id: 7, role: "user", text: "poll first"});
          flushChat();
          queueMessage({id: 7, role: "user", text: "poll first"});
          flushChat();
          return document.querySelectorAll("#messages .message").length;
        }""")
        self.assertEqual(duplicate, 5)

    def test_scrolling_up_stops_following_stream_and_acceptance(self):
        result = self.page.evaluate("""() => {
          for (let id = 1; id <= 40; id += 1) {
            queueMessage({id, role: "user", text: "message " + id + "\\nline\\nline\\nline"});
          }
          flushChat();
          chatLog.scrollTop = chatLog.scrollHeight;
          chatLog.dispatchEvent(new Event("scroll"));
          const atBottom = following;
          queueMessage({id: 41, role: "user", text: "next"});
          flushChat();
          const followed = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 2;
          chatLog.scrollTop = 0;
          chatLog.dispatchEvent(new Event("scroll"));
          const stopped = following;
          queueStream("new reply", "<p>new reply</p>");
          flushChat();
          queueMessage({id: 42, role: "user", text: "another"});
          flushChat();
          const held = chatLog.scrollTop;
          keepBottom();
          return {atBottom, followed, stopped, held, afterKeep: chatLog.scrollTop};
        }""")
        self.assertTrue(result["atBottom"])
        self.assertTrue(result["followed"])
        self.assertFalse(result["stopped"])
        self.assertEqual(result["held"], 0)
        self.assertEqual(result["afterKeep"], 0)

    def test_sending_and_error_live_regions_stay_outside_busy_ancestors(self):
        session = self.page.context.new_cdp_session(self.page)
        try:
            tree = session.send("Accessibility.getFullAXTree")
        finally:
            session.detach()
        roles = {node.get("role", {}).get("value") for node in tree["nodes"]}
        self.assertIn("status", roles)
        self.assertIn("alert", roles)
        result = self.page.evaluate("""async () => {
          const status = document.getElementById("send-status");
          const error = document.getElementById("request-error");
          const composer = document.getElementById("composer");
          const visible = (node) => node.isConnected && node.checkVisibility()
            && getComputedStyle(node).display !== "none"
            && getComputedStyle(node).visibility !== "hidden"
            && !node.closest('[aria-busy="true"]');
          const initial = {
            status: status.textContent,
            error: error.textContent,
            statusRole: status.getAttribute("role"),
            errorRole: error.getAttribute("role"),
            statusVisible: visible(status),
            errorVisible: visible(error),
            composerBusy: composer.getAttribute("aria-busy"),
          };
          const originalFetch = window.fetch;
          let release = null;
          let requests = 0;
          window.fetch = () => {
            requests += 1;
            return new Promise((resolve) => { release = resolve; });
          };
          const message = document.getElementById("message");
          try {
            message.value = "Accessible send";
            composer.requestSubmit();
            await new Promise((resolve) => setTimeout(resolve, 0));
            const pending = {
              status: status.textContent,
              statusVisible: visible(status),
              error: error.textContent,
              errorVisible: visible(error),
              busyAncestor: Boolean(status.closest('[aria-busy="true"]')),
              requests,
            };
            composer.requestSubmit();
            await new Promise((resolve) => setTimeout(resolve, 0));
            pending.requestsAfterDuplicate = requests;
            release({ok: true, json: async () => ({message: {
              id: 1, role: "user", text: "Accessible send", images: [],
            }})});
            await new Promise((resolve) => setTimeout(resolve, 50));
            const accepted = {
              status: status.textContent,
              statusVisible: visible(status),
              error: error.textContent,
              errorVisible: visible(error),
              message: document.querySelector("#messages .message.user .message-text").textContent,
              busyAncestor: Boolean(status.closest('[aria-busy="true"]')),
            };
            window.fetch = async () => ({
              ok: false, json: async () => ({error: "Fixture rejected the message."}),
            });
            message.value = "Failed send";
            composer.requestSubmit();
            await new Promise((resolve) => setTimeout(resolve, 50));
            const failed = {
              status: status.textContent,
              statusVisible: visible(status),
              error: error.textContent,
              errorVisible: visible(error),
              draft: message.value,
              busyAncestor: Boolean(error.closest('[aria-busy="true"]')),
            };
            return {initial, pending, accepted, failed};
          } finally {
            window.fetch = originalFetch;
          }
        }""")
        self.assertEqual(result["initial"], {
            "status": "", "error": "", "statusRole": "status", "errorRole": "alert",
            "statusVisible": True, "errorVisible": True, "composerBusy": None,
        })
        self.assertEqual({key: value for key, value in result["pending"].items()
                          if key != "requestsAfterDuplicate"}, {
            "status": "Sending…", "statusVisible": True, "error": "",
            "errorVisible": True, "busyAncestor": False, "requests": 1,
        })
        self.assertEqual(result["pending"]["requestsAfterDuplicate"], 1)
        self.assertEqual(result["accepted"], {
            "status": "", "statusVisible": True, "error": "", "errorVisible": True,
            "message": "Accessible send", "busyAncestor": False,
        })
        self.assertEqual(result["failed"], {
            "status": "", "statusVisible": True,
            "error": "Fixture rejected the message.", "errorVisible": True,
            "draft": "Failed send", "busyAncestor": False,
        })

    def test_selection_survives_in_place_stream_updates(self):
        result = self.page.evaluate("""() => {
          updateStream("Hello", "<p>Hello</p>");
          const liveText = document.querySelector("#stream p").firstChild;
          const selection = window.getSelection();
          const range = document.createRange();
          range.setStart(liveText, 0);
          range.setEnd(liveText, 4);
          selection.removeAllRanges();
          selection.addRange(range);
          queueMessage({id: 1, role: "assistant", text: "Alpha beta",
            html: "<p>Alpha beta</p>"});
          flushChat();
          const messageText = document.querySelector("#messages .message-text p").firstChild;
          updateStream("Hello world", "<p>Hello world</p>");
          return {
            selected: selection.toString(),
            liveNode: document.querySelector("#stream p").firstChild === liveText,
            messageText: messageText.nodeValue,
          };
        }""")
        self.assertEqual(result, {
            "selected": "Hell", "liveNode": True, "messageText": "Alpha beta",
        })

    def test_poll_cursor_never_skips_history_ahead_of_an_acknowledgement(self):
        result = self.page.evaluate("""async () => {
          const originalFetch = window.fetch;
          const originalTimeout = window.setTimeout;
          const scheduled = [];
          let response = null;
          window.fetch = () => Promise.resolve({ok: true, json: async () => response});
          window.setTimeout = (callback, delay) => {
            scheduled.push(delay);
            return scheduled.length;
          };
          const state = (messages) => ({
            messages, busy: false, canInterrupt: false, stream: "", streamHtml: "",
            tasks: [], codex: {models: {}, efforts: []}, projects: null, issueError: "",
            updateAvailable: false, draining: false, paused: "", view: "", signedIn: true,
            login: null,
          });
          try {
            lastMessage = 0;
            issueRevision = "";
            pollStarted = true;
            polling = false;
            pollAgain = false;
            pollTimer = 0;
            queueMessage({id: 5, role: "user", text: "acknowledged"});
            flushChat();
            const ackCursor = lastMessage;
            response = state([
              {id: 2, role: "system", text: "earlier"},
              {id: 3, role: "worker", text: "same"},
              {id: 4, role: "worker", text: "same"},
              {id: 5, role: "user", text: "acknowledged"},
            ]);
            await poll();
            flushChat();
            const afterHistory = {
              cursor: lastMessage,
              ids: [...document.querySelectorAll("#messages .message")]
                .map((node) => node.dataset.messageId),
            };
            response = state([{id: 6, role: "user", text: "poll first"}]);
            await poll();
            flushChat();
            queueMessage({id: 6, role: "user", text: "poll first"});
            flushChat();
            const afterDuplicateAck = {
              cursor: lastMessage,
              count: document.querySelectorAll("#messages .message").length,
            };
            return {ackCursor, afterHistory, afterDuplicateAck, scheduled};
          } finally {
            window.fetch = originalFetch;
            window.setTimeout = originalTimeout;
          }
        }""")
        self.assertEqual(result["ackCursor"], 0)
        self.assertEqual(result["afterHistory"], {
            "cursor": 5, "ids": ["2", "3", "4", "5"],
        })
        self.assertEqual(result["afterDuplicateAck"], {"cursor": 6, "count": 5})
        self.assertTrue(result["scheduled"])

    def test_poll_scheduler_keeps_one_request_and_one_successor(self):
        result = self.page.evaluate("""async () => {
          const originalFetch = window.fetch;
          const originalTimeout = window.setTimeout;
          const requests = [];
          const scheduled = [];
          let resolveRequest;
          window.fetch = (path) => {
            requests.push(path);
            return new Promise((resolve) => { resolveRequest = resolve; });
          };
          window.setTimeout = (callback, delay) => {
            scheduled.push(delay);
            return scheduled.length;
          };
          lastMessage = 0;
          issueRevision = "";
          pollStarted = false;
          polling = false;
          pollAgain = false;
          pollTimer = 0;
          const state = {
            messages: [], busy: true, canInterrupt: false, stream: "", streamHtml: "",
            tasks: [], codex: {models: {}, efforts: []}, projects: null, issueError: "",
            updateAvailable: false, draining: false, paused: "", view: "", signedIn: true,
            login: null,
          };
          try {
            const first = poll();
            const started = {requests: requests.length, polling};
            const second = poll();
            wakePoll();
            wakePoll();
            const whilePending = {requests: requests.length, pollAgain};
            resolveRequest({ok: true, json: async () => state});
            await first;
            await second;
            return {
              started, whilePending, scheduled,
              polling, pollAgain,
              paths: requests,
            };
          } finally {
            window.fetch = originalFetch;
            window.setTimeout = originalTimeout;
          }
        }""")
        self.assertEqual(result["started"], {"requests": 1, "polling": True})
        self.assertEqual(result["whilePending"], {"requests": 1, "pollAgain": True})
        self.assertEqual(result["scheduled"], [0])
        self.assertFalse(result["polling"])
        self.assertFalse(result["pollAgain"])
        self.assertTrue(all(path.startswith("/api/state?") for path in result["paths"]))

    def test_long_stream_appends_without_rebuilding_earlier_blocks(self):
        result = self.page.evaluate("""() => {
          const paragraphs = Array.from({length: 150}, (_, index) =>
            "Paragraph " + index + " with enough text to wrap in the chat column.");
          const text = paragraphs.join("\\n\\n");
          const html = paragraphs.map((paragraph) => "<p>" + paragraph + "</p>").join("\\n");
          updateStream(text, html);
          const first = document.querySelector("#stream p");
          const last = document.querySelector("#stream p:last-child");
          const before = document.querySelectorAll("#stream p").length;
          const grown = text + "\\n\\nParagraph 150 is new.";
          updateStream(grown, html + "\\n<p>Paragraph 150 is new.</p>");
          return {
            before,
            after: document.querySelectorAll("#stream p").length,
            first: document.querySelector("#stream p") === first,
            last: document.querySelector("#stream p:nth-child(150)") === last,
            tail: document.querySelector("#stream p:last-child").textContent,
            text: document.querySelector("#stream").textContent,
          };
        }""")
        self.assertEqual(result["before"], 150)
        self.assertEqual(result["after"], 151)
        self.assertTrue(result["first"])
        self.assertTrue(result["last"])
        self.assertEqual(result["tail"], "Paragraph 150 is new.")
        self.assertTrue(result["text"].startswith("Paragraph 0 with enough text"))
        self.assertTrue(result["text"].endswith("Paragraph 150 is new."))

    def test_quick_plan_action_renders_on_unrecorded_backlog_cards_only(self):
        long_title = "Investigate " + "an-unbroken-description-" * 12
        rows = [
            self.issue("KIAN-1", title=long_title),
            self.issue("KIAN-2", plan="Keep this plan.", planHtml="<p>Keep this plan.</p>"),
            self.issue("KIAN-3"),
            self.issue("KIAN-4", state="Todo"),
            self.issue("KIAN-5", state="In Progress"),
            self.issue("KIAN-6", state="Done"),
        ]
        self.render_cards([["project-a", rows]],
                          [{"issue": "KIAN-3", "phase": "working", "detail": "Worker running."}])
        result = self.page.evaluate("""() => {
          const action = (id) => document.getElementById("issue-propose-" + id);
          const preview = (id) => document.getElementById("issue-open-" + id);
          const card = (id) => preview(id).closest("article");
          const first = preview("KIAN-1");
          const quick = action("KIAN-1");
          const box = quick.getBoundingClientRect();
          const style = getComputedStyle(quick);
          return {
            backlog: [...document.querySelectorAll("#backlog .issue-link")].map((node) => node.textContent),
            actions: [...document.querySelectorAll(".issue-plan-action")].map((node) => node.id),
            sibling: first.parentElement === quick.parentElement && first.nextElementSibling === quick,
            row: first.parentElement.className,
            types: [first.type, quick.type],
            label: quick.getAttribute("aria-label"),
            title: quick.title,
            icon: quick.querySelector("use").getAttribute("href"),
            nested: document.querySelectorAll("button button").length,
            size: [box.width, box.height],
            radius: style.borderRadius,
            overflow: card("KIAN-1").scrollWidth > card("KIAN-1").clientWidth + 1,
            plan: Boolean(document.querySelector("details[data-issue='KIAN-2']")),
            planOpen: document.querySelector("details[data-issue='KIAN-2']").open,
          };
        }""")
        self.assertEqual(result["backlog"], ["KIAN-1", "KIAN-2", "KIAN-3"])
        self.assertEqual(result["actions"], ["issue-propose-KIAN-1", "issue-propose-KIAN-2"])
        self.assertTrue(result["sibling"])
        self.assertEqual(result["row"], "issue-card-row")
        self.assertEqual(result["types"], ["button", "button"])
        self.assertEqual(result["label"], "Propose a plan for KIAN-1")
        self.assertEqual(result["title"], "Propose a plan for KIAN-1")
        self.assertEqual(result["icon"], "#lucide-arrow-right")
        self.assertEqual(result["nested"], 0)
        self.assertEqual(result["size"], [34, 34])
        self.assertEqual(result["radius"], "50%")
        self.assertFalse(result["overflow"])
        self.assertTrue(result["plan"])
        self.assertFalse(result["planOpen"])

        self.assertEqual(self.page.get_by_role(
            "button", name="Propose a plan for KIAN-1", exact=True).count(), 1)
        self.assertEqual(self.page.get_by_role(
            "button", name="Propose a plan for KIAN-2", exact=True).count(), 1)
        self.assertEqual(self.page.get_by_role(
            "button", name="Propose a plan for KIAN-3", exact=True).count(), 0)
        for identifier in ("KIAN-4", "KIAN-5", "KIAN-6"):
            self.assertEqual(self.page.locator("#issue-propose-" + identifier).count(), 0)

        self.page.locator("#plan-summary-KIAN-2").click()
        self.assertTrue(self.page.locator(
            "details[data-issue='KIAN-2']").evaluate("node => node.open"))

        self.page.locator("#issue-open-KIAN-1").focus()
        self.page.keyboard.press("Tab")
        focus = self.page.evaluate("""() => ({
          id: document.activeElement.id,
          outline: getComputedStyle(document.activeElement).outlineWidth,
        })""")
        self.assertEqual(focus["id"], "issue-propose-KIAN-1")
        self.assertNotEqual(focus["outline"], "0px")
        self.page.evaluate("renderPanes()")
        self.assertEqual(self.page.evaluate("document.activeElement.id"),
                         "issue-propose-KIAN-1")

    def test_quick_plan_request_captures_project_and_survives_redraws(self):
        self.render_cards([
            ["project-a", [self.issue("KIAN-10")]],
            ["project-b", [self.issue("KIAN-11", project="project-b"),
                           self.issue("KIAN-12", project="")]],
        ])
        self.hold_chat_requests()
        self.page.evaluate("""() => {
          messageField.value = "Unsent draft";
          messageField.setSelectionRange(2, 7);
          history.push("Earlier");
        }""")
        self.page.locator("#issue-propose-KIAN-10").click()
        self.page.wait_for_function("window.__requests.length === 1")
        pending = self.page.evaluate("""() => ({
          request: window.__requests[0],
          selected,
          disabled: document.getElementById("issue-propose-KIAN-10").disabled,
          draft: messageField.value,
          selection: [messageField.selectionStart, messageField.selectionEnd],
          history: history.slice(),
          opened: window.__opened.slice(),
        })""")
        self.assertEqual(pending["request"], {
            "path": "/api/chat",
            "body": {"text": "Propose a plan for KIAN-10.", "project": "project-a", "images": []},
        })
        self.assertEqual(pending["selected"], "project-a")
        self.assertTrue(pending["disabled"])
        self.assertEqual(pending["draft"], "Unsent draft")
        self.assertEqual(pending["selection"], [2, 7])
        self.assertEqual(pending["history"], ["Earlier"])
        self.assertEqual(pending["opened"], [])

        self.page.evaluate('selectProject("project-b")')
        switched = self.page.evaluate("""() => ({
          selected,
          absent: document.getElementById("issue-propose-KIAN-10") === null,
          requests: window.__requests.length,
        })""")
        self.assertEqual(switched, {"selected": "project-b", "absent": True, "requests": 1})
        self.page.evaluate('selectProject("project-a")')
        redrawn = self.page.evaluate("""() => {
          const current = document.getElementById("issue-propose-KIAN-10");
          const first = current;
          first.disabled = false;
          first.click();
          renderPanes();
          const replacement = document.getElementById("issue-propose-KIAN-10");
          replacement.disabled = false;
          replacement.click();
          return {
            disabled: replacement.disabled,
            replaced: replacement !== first,
            requests: window.__requests.length,
            pending: pendingPlans.has("KIAN-10"),
          };
        }""")
        self.assertEqual(redrawn, {
            "disabled": False, "replaced": True, "requests": 1, "pending": True,
        })
        self.page.evaluate("renderPanes()")
        self.assertTrue(self.page.locator("#issue-propose-KIAN-10").is_disabled())

        self.page.evaluate("__respond(0, 100)")
        self.page.wait_for_function(
            "document.getElementById('issue-propose-KIAN-10').disabled === false")
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .message').length === 1")
        acknowledged = self.page.evaluate("""() => ({
          text: document.querySelector("#messages .message.user .message-text").textContent,
          error: document.getElementById("request-error").textContent,
          pending: pendingPlans.has("KIAN-10"),
        })""")
        self.assertEqual(acknowledged, {
            "text": "Propose a plan for KIAN-10.", "error": "", "pending": False,
        })
        self.page.evaluate("""() => {
          queueMessage({id: 100, role: "user", text: "Propose a plan for KIAN-10.", images: []});
          flushChat();
        }""")
        self.assertEqual(self.page.locator("#messages .message").count(), 1)

        self.page.evaluate('selectProject("project-b")')
        self.page.locator("#issue-propose-KIAN-12").click()
        self.page.wait_for_function("window.__requests.length === 2")
        self.assertEqual(self.page.evaluate("window.__requests[1]"), {
            "path": "/api/chat",
            "body": {"text": "Propose a plan for KIAN-12.", "project": "project-b", "images": []},
        })
        self.page.evaluate("__respond(0, 101)")
        self.page.wait_for_function(
            "document.getElementById('issue-propose-KIAN-12').disabled === false")

    def test_quick_plan_action_handles_keyboard_errors_and_deliberate_retry(self):
        self.render_cards([["project-a", [self.issue("KIAN-20")]]])
        self.hold_chat_requests()
        for index, activation in enumerate(("mouse", "Enter", "Space"), start=1):
            action = self.page.locator("#issue-propose-KIAN-20")
            if activation == "mouse":
                action.click()
            else:
                action.press(activation)
            self.page.wait_for_function(f"window.__requests.length === {index}")
            self.page.evaluate(f"__respond(0, {100 + index})")
            self.page.wait_for_function(
                "document.getElementById('issue-propose-KIAN-20').disabled === false")
            self.page.wait_for_function(
                f"document.querySelectorAll('#messages .message').length === {index}")
        requests = self.page.evaluate("window.__requests.map((item) => item.body)")
        self.assertEqual(requests, [
            {"text": "Propose a plan for KIAN-20.", "project": "project-a", "images": []},
        ] * 3)
        self.assertEqual(self.page.evaluate("window.__opened"), [])

        self.page.locator("#issue-propose-KIAN-20").click()
        self.page.wait_for_function("window.__requests.length === 4")
        self.page.evaluate("__reject(0, 'Fixture rejected the plan.')")
        self.page.wait_for_function(
            "document.getElementById('request-error').textContent === 'Fixture rejected the plan.'")
        failed = self.page.evaluate("""() => ({
          disabled: document.getElementById("issue-propose-KIAN-20").disabled,
          pending: pendingPlans.has("KIAN-20"),
          messages: document.querySelectorAll("#messages .message").length,
          error: document.getElementById("request-error").textContent,
        })""")
        self.assertEqual(failed, {
            "disabled": False, "pending": False, "messages": 3,
            "error": "Fixture rejected the plan.",
        })

        self.page.locator("#issue-propose-KIAN-20").click()
        self.page.wait_for_function("window.__requests.length === 5")
        self.page.evaluate("__respond(0, 105)")
        self.page.wait_for_function(
            "document.getElementById('issue-propose-KIAN-20').disabled === false")
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .message').length === 4")
        retried = self.page.evaluate("""() => ({
          error: document.getElementById("request-error").textContent,
          texts: [...document.querySelectorAll("#messages .message-text")]
            .map((node) => node.textContent),
        })""")
        self.assertEqual(retried, {
            "error": "",
            "texts": ["Propose a plan for KIAN-20."] * 4,
        })
        self.page.evaluate("""() => {
          queueMessage({id: 105, role: "user", text: "Propose a plan for KIAN-20.", images: []});
          flushChat();
        }""")
        self.assertEqual(self.page.locator("#messages .message").count(), 4)

        self.page.locator("#issue-open-KIAN-20").click()
        self.assertEqual(self.page.evaluate("window.__opened"), ["KIAN-20"])
        self.assertEqual(self.page.evaluate("window.__requests.length"), 5)

    def test_quick_plan_action_preserves_composer_drafts_and_concurrent_send(self):
        self.render_cards([["project-a", [self.issue("KIAN-30")]]])
        self.hold_chat_requests()
        self.page.evaluate("""() => {
          window.__revoked = [];
          const revoke = URL.revokeObjectURL.bind(URL);
          URL.revokeObjectURL = (url) => {
            window.__revoked.push(url);
            revoke(url);
          };
          messageField.value = "Draft survives";
          messageField.setSelectionRange(1, 5);
          history.push("Earlier");
          attachImage(new File([new Uint8Array([1, 2, 3])], "one.png", {type: "image/png"}));
        }""")
        initial_urls = self.page.evaluate("pendingImages.map((image) => image.url)")
        self.page.locator("#issue-propose-KIAN-30").click()
        self.page.wait_for_function("window.__requests.length === 1")
        self.page.evaluate("""() => {
          messageField.value = "Edited during request";
          messageField.setSelectionRange(3, 9);
          attachImage(new File([new Uint8Array([4, 5, 6])], "two.png", {type: "image/png"}));
        }""")
        edited_urls = self.page.evaluate("pendingImages.map((image) => image.url)")
        self.assertEqual(len(edited_urls), 2)
        self.assertEqual(edited_urls[:1], initial_urls)

        self.page.evaluate("__reject(0, 'Fixture rejected the plan.')")
        self.page.wait_for_function(
            "document.getElementById('request-error').textContent === 'Fixture rejected the plan.'")
        failed = self.page.evaluate("""() => ({
          disabled: document.getElementById("issue-propose-KIAN-30").disabled,
          draft: messageField.value,
          selection: [messageField.selectionStart, messageField.selectionEnd],
          urls: pendingImages.map((image) => image.url),
          attachments: document.querySelectorAll(".composer-attachment").length,
          revoked: window.__revoked.slice(),
          history: history.slice(),
          opened: window.__opened.slice(),
        })""")
        self.assertEqual(failed, {
            "disabled": False, "draft": "Edited during request", "selection": [3, 9],
            "urls": edited_urls, "attachments": 2, "revoked": [],
            "history": ["Earlier"], "opened": [],
        })

        self.page.locator("#issue-propose-KIAN-30").click()
        self.page.wait_for_function("window.__requests.length === 2")
        self.page.evaluate("""() => {
          messageField.value = "Edited again";
          messageField.setSelectionRange(0, 6);
          attachImage(new File([new Uint8Array([7, 8, 9])], "three.png", {type: "image/png"}));
        }""")
        self.page.evaluate("__respond(0, 201)")
        self.page.wait_for_function(
            "document.getElementById('issue-propose-KIAN-30').disabled === false")
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .message').length === 1")
        succeeded = self.page.evaluate("""() => ({
          draft: messageField.value,
          selection: [messageField.selectionStart, messageField.selectionEnd],
          urls: pendingImages.map((image) => image.url),
          attachments: document.querySelectorAll(".composer-attachment").length,
          revoked: window.__revoked.slice(),
          history: history.slice(),
          error: document.getElementById("request-error").textContent,
        })""")
        self.assertEqual(succeeded["draft"], "Edited again")
        self.assertEqual(succeeded["selection"], [0, 6])
        self.assertEqual(len(succeeded["urls"]), 3)
        self.assertEqual(succeeded["urls"][:2], edited_urls)
        self.assertEqual(succeeded["attachments"], 3)
        self.assertEqual(succeeded["revoked"], [])
        self.assertEqual(succeeded["history"], ["Earlier"])
        self.assertEqual(succeeded["error"], "")

        self.page.evaluate("""() => {
          messageField.value = "Composer alongside plan";
          messageField.setSelectionRange(0, 0);
        }""")
        self.page.locator("#issue-propose-KIAN-30").click()
        self.page.wait_for_function("window.__requests.length === 3")
        self.page.evaluate("document.getElementById('composer').requestSubmit()")
        self.page.wait_for_function("window.__requests.length === 4")
        concurrent = self.page.evaluate("""() => ({
          requests: window.__requests.map((item) => item.body),
          sending,
          status: document.getElementById("send-status").textContent,
          disabled: document.getElementById("issue-propose-KIAN-30").disabled,
        })""")
        self.assertEqual(concurrent["requests"][2], {
            "text": "Propose a plan for KIAN-30.", "project": "project-a", "images": [],
        })
        self.assertEqual(concurrent["requests"][3]["text"], "Composer alongside plan")
        self.assertEqual(concurrent["requests"][3]["project"], "project-a")
        self.assertEqual(len(concurrent["requests"][3]["images"]), 3)
        self.assertTrue(concurrent["sending"])
        self.assertEqual(concurrent["status"], "Sending…")
        self.assertTrue(concurrent["disabled"])

        self.page.evaluate("""() => {
          messageField.value = "Next unsent draft";
          messageField.setSelectionRange(0, 4);
        }""")
        self.page.evaluate("__respond(0, 202)")
        self.page.wait_for_function(
            "document.getElementById('issue-propose-KIAN-30').disabled === false")
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .message').length === 2")
        after_quick = self.page.evaluate("""() => ({
          draft: messageField.value,
          sending,
          status: document.getElementById("send-status").textContent,
          images: pendingImages.length,
          history: history.slice(),
          messages: document.querySelectorAll("#messages .message").length,
        })""")
        self.assertEqual(after_quick, {
            "draft": "Next unsent draft", "sending": True, "status": "Sending…",
            "images": 3, "history": ["Earlier"], "messages": 2,
        })

        self.page.evaluate("__respond(0, 203)")
        self.page.wait_for_function("sending === false")
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .message').length === 3")
        final = self.page.evaluate("""() => ({
          draft: messageField.value,
          sending,
          status: document.getElementById("send-status").textContent,
          images: pendingImages.length,
          attachmentsHidden: document.getElementById("attachments").hidden,
          revoked: window.__revoked.length,
          history: history.slice(),
          texts: [...document.querySelectorAll("#messages .message-text")]
            .map((node) => node.textContent),
        })""")
        self.assertEqual(final, {
            "draft": "Next unsent draft", "sending": False, "status": "",
            "images": 0, "attachmentsHidden": True, "revoked": 3,
            "history": ["Earlier", "Composer alongside plan"],
            "texts": ["Propose a plan for KIAN-30.",
                      "Propose a plan for KIAN-30.", "Composer alongside plan"],
        })

    def test_quick_plan_action_fits_desktop_and_coarse_narrow_layouts(self):
        long_title = "A long issue title " + "that keeps wrapping " * 16
        self.render_cards([["project-a", [
            self.issue("KIAN-40", title=long_title),
            self.issue("KIAN-41", plan="A detailed plan.", planHtml="<p>A detailed plan.</p>"),
        ]]])
        desktop = {}
        for scheme in ("light", "dark"):
            self.page.emulate_media(color_scheme=scheme)
            desktop[scheme] = self.page.evaluate("""() => {
              const action = document.getElementById("issue-propose-KIAN-40");
              const card = action.closest("article");
              const box = action.getBoundingClientRect();
              return {
                size: [box.width, box.height],
                overflow: card.scrollWidth > card.clientWidth + 1,
                border: getComputedStyle(action).borderStyle,
              };
            }""")
        for scheme in ("light", "dark"):
            self.assertEqual(desktop[scheme], {
                "size": [34, 34], "overflow": False, "border": "solid",
            })

        coarse = self.browser.new_page(
            viewport={"width": 390, "height": 844}, has_touch=True)
        try:
            self.load_app(coarse)
            coarse.evaluate("""({rows}) => {
              tasks = [];
              selected = "project-a";
              issueCache = new Map([["project-a", rows], ["", rows]]);
              renderPanes();
            }""", {"rows": [
                self.issue("KIAN-40", title=long_title),
                self.issue("KIAN-41", plan="A detailed plan.",
                           planHtml="<p>A detailed plan.</p>"),
            ]})
            narrow = coarse.evaluate("""() => {
              const action = document.getElementById("issue-propose-KIAN-40");
              const card = action.closest("article");
              const box = action.getBoundingClientRect();
              const plan = document.querySelector("details[data-issue='KIAN-41']");
              plan.open = true;
              return {
                size: [box.width, box.height],
                cardOverflow: card.scrollWidth > card.clientWidth + 1,
                backlogOverflow: document.getElementById("backlog").scrollWidth
                  > document.getElementById("backlog").clientWidth + 1,
                planOverflow: plan.scrollWidth > plan.clientWidth + 1,
                composerVisible: document.getElementById("composer")
                  .getBoundingClientRect().bottom <= window.innerHeight + 1,
              };
            }""")
            self.assertEqual(narrow, {
                "size": [44, 44], "cardOverflow": False, "backlogOverflow": False,
                "planOverflow": False, "composerVisible": True,
            })
        finally:
            coarse.close()

    def test_panel_collapse_controls_are_independent_and_accessible(self):
        self.render_cards([["project-a", [
            self.issue("KIAN-1"), self.issue("KIAN-2"),
            self.issue("KIAN-3", state="Todo"),
            self.issue("KIAN-4", state="In Progress"),
        ]]], [{"issue": "KIAN-4", "phase": "working", "detail": "Worker running."}])
        self.page.evaluate("""() => {
          window.__requests = [];
          window.fetch = (...args) => {
            window.__requests.push(args[0]);
            return Promise.reject(new Error("Unexpected request"));
          };
          window.__tasksBefore = JSON.stringify(tasks);
        }""")
        initial = self.page.evaluate("""() => {
          const toggle = (id) => {
            const node = document.getElementById(id);
            return {
              type: node.type,
              expanded: node.getAttribute("aria-expanded"),
              controls: node.getAttribute("aria-controls"),
              label: node.getAttribute("aria-label"),
              title: node.title,
              icon: node.querySelector("use").getAttribute("href"),
            };
          };
          return {
            collapsed: [backlogCollapsed, queueCollapsed],
            hidden: [backlog.hidden, statusIssues.hidden],
            headings: [document.getElementById("backlog-heading").textContent,
                       document.getElementById("queue-heading").textContent],
            toggles: [toggle("backlog-toggle"), toggle("queue-toggle")],
            selected,
            messages: document.querySelectorAll("#messages .message").length,
          };
        }""")
        self.assertEqual(initial, {
            "collapsed": [False, False],
            "hidden": [False, False],
            "headings": ["Backlog", "Queue"],
            "toggles": [
                {"type": "button", "expanded": "true", "controls": "backlog",
                 "label": "Collapse Backlog panel", "title": "Collapse Backlog panel",
                 "icon": "#lucide-chevron-left"},
                {"type": "button", "expanded": "true", "controls": "status-issues",
                 "label": "Collapse Queue panel", "title": "Collapse Queue panel",
                 "icon": "#lucide-chevron-left"},
            ],
            "selected": "project-a", "messages": 0,
        })

        def state():
            return self.page.evaluate("""() => ({
              collapsed: [backlogCollapsed, queueCollapsed],
              hidden: [backlog.hidden, statusIssues.hidden],
              expanded: [backlogToggle.getAttribute("aria-expanded"),
                         queueToggle.getAttribute("aria-expanded")],
              labels: [backlogToggle.getAttribute("aria-label"),
                       queueToggle.getAttribute("aria-label")],
              focus: document.activeElement.id,
            })""")

        self.page.locator("#backlog-toggle").click()
        self.assertEqual(state(), {
            "collapsed": [True, False], "hidden": [True, False],
            "expanded": ["false", "true"],
            "labels": ["Expand Backlog panel", "Collapse Queue panel"],
            "focus": "backlog-toggle",
        })
        self.page.locator("#backlog-toggle").press("Enter")
        self.assertEqual(state(), {
            "collapsed": [False, False], "hidden": [False, False],
            "expanded": ["true", "true"],
            "labels": ["Collapse Backlog panel", "Collapse Queue panel"],
            "focus": "backlog-toggle",
        })
        self.page.locator("#backlog-toggle").press("Space")
        self.page.locator("#queue-toggle").press("Enter")
        self.assertEqual(state(), {
            "collapsed": [True, True], "hidden": [True, True],
            "expanded": ["false", "false"],
            "labels": ["Expand Backlog panel", "Expand Queue panel"],
            "focus": "queue-toggle",
        })
        self.page.locator("#queue-toggle").press("Space")
        self.assertEqual(state(), {
            "collapsed": [True, False], "hidden": [True, False],
            "expanded": ["false", "true"],
            "labels": ["Expand Backlog panel", "Collapse Queue panel"],
            "focus": "queue-toggle",
        })
        self.page.locator("#backlog-toggle").click()
        self.page.locator("#queue-toggle").click()
        self.assertEqual(state()["collapsed"], [False, True])
        self.page.locator("#backlog-toggle").click()
        self.assertEqual(state()["collapsed"], [True, True])

        excluded = self.page.evaluate("""() => {
          const card = document.getElementById("issue-open-KIAN-1");
          card.focus();
          return {
            focusedInBacklog: Boolean(document.activeElement.closest("#backlog")),
            contentVisible: backlog.checkVisibility(),
            cardVisible: card.checkVisibility(),
          };
        }""")
        self.assertEqual(excluded, {
            "focusedInBacklog": False, "contentVisible": False, "cardVisible": False,
        })
        self.assertFalse(self.page.locator("#issue-open-KIAN-1").is_visible())
        self.assertEqual(self.page.get_by_role(
            "button", name="Propose a plan for KIAN-1", exact=True).count(), 0)
        self.assertEqual(self.page.evaluate("""() => ({
          requests: window.__requests,
          tasksUnchanged: JSON.stringify(tasks) === window.__tasksBefore,
          selected,
          messages: document.querySelectorAll("#messages .message").length,
          draft: messageField.value,
        })"""), {
            "requests": [], "tasksUnchanged": True, "selected": "project-a",
            "messages": 0, "draft": "",
        })

        for scheme in ("light", "dark"):
            self.page.emulate_media(color_scheme=scheme)
            self.page.locator("#backlog-toggle").focus()
            self.page.keyboard.press("Tab")
            self.page.keyboard.press("Shift+Tab")
            focus = self.page.evaluate("""() => ({
              id: document.activeElement.id,
              outline: getComputedStyle(document.activeElement).outlineWidth,
            })""")
            self.assertEqual(focus["id"], "backlog-toggle")
            self.assertNotEqual(focus["outline"], "0px")

        reloaded = self.browser.new_page(viewport={"width": 1200, "height": 800})
        try:
            self.load_app(reloaded)
            fresh = reloaded.evaluate("""() => ({
              collapsed: [backlogCollapsed, queueCollapsed],
              hidden: [backlog.hidden, statusIssues.hidden],
              expanded: [backlogToggle.getAttribute("aria-expanded"),
                         queueToggle.getAttribute("aria-expanded")],
              labels: [backlogToggle.getAttribute("aria-label"),
                       queueToggle.getAttribute("aria-label")],
            })""")
        finally:
            reloaded.close()
        self.assertEqual(fresh, {
            "collapsed": [False, False], "hidden": [False, False],
            "expanded": ["true", "true"],
            "labels": ["Collapse Backlog panel", "Collapse Queue panel"],
        })

    def test_panel_collapse_preserves_scroll_disclosures_and_pending_plan(self):
        self.page.set_viewport_size({"width": 1200, "height": 480})
        self.page.emulate_media(reduced_motion="reduce")
        rows = [self.issue("KIAN-A1", plan="Plan A1.", planHtml="<p>Plan A1.</p>")]
        rows += [self.issue("KIAN-A" + str(index)) for index in range(2, 31)]
        rows += [self.issue("KIAN-T" + str(index), state="Todo") for index in range(1, 21)]
        rows += [self.issue("KIAN-P" + str(index), state="In Progress") for index in range(1, 11)]
        rows_b = [self.issue("KIAN-B" + str(index), project="project-b")
                  for index in range(1, 31)]
        rows_b += [self.issue("KIAN-BT" + str(index), state="Todo", project="project-b")
                   for index in range(1, 16)]
        self.render_cards([["project-a", rows], ["project-b", rows_b]])

        self.page.locator("#plan-summary-KIAN-A1").click()
        self.assertTrue(self.page.locator(
            "details[data-issue='KIAN-A1']").evaluate("node => node.open"))
        self.page.evaluate(
            "document.getElementById('status-summary-In%20Progress').click()")
        self.assertFalse(self.page.evaluate("""() => {
          return [...document.querySelectorAll("#status-issues .issue-group")]
            .find((node) => node.dataset.group === JSON.stringify(["project-a", "In Progress"]))
            .open;
        }"""))

        self.hold_chat_requests()
        self.page.locator("#issue-propose-KIAN-A1").click()
        self.page.wait_for_function("window.__requests.length === 1")

        offsets = self.page.evaluate("""() => {
          backlog.scrollTop = 140;
          statusIssues.scrollTop = 70;
          return [backlog.scrollTop, statusIssues.scrollTop,
                  backlog.scrollHeight > backlog.clientHeight,
                  statusIssues.scrollHeight > statusIssues.clientHeight];
        }""")
        self.assertEqual(offsets, [140, 70, True, True])
        self.page.evaluate('selectProject("project-b")')
        self.page.evaluate("""() => {
          backlog.scrollTop = 45;
          statusIssues.scrollTop = 25;
        }""")
        self.page.evaluate('selectProject("project-a")')
        self.assertEqual(self.page.evaluate(
            "[backlog.scrollTop, statusIssues.scrollTop]"), [140, 70])

        self.page.locator("#backlog-toggle").click()
        self.page.locator("#queue-toggle").click()
        saved = self.page.evaluate("""() => ({
          collapsed: [backlogCollapsed, queueCollapsed],
          scrolls: [backlogScroll.get("project-a"), statusScroll.get("project-a"),
                    backlogScroll.get("project-b"), statusScroll.get("project-b")],
        })""")
        self.assertEqual(saved, {
            "collapsed": [True, True], "scrolls": [140, 70, 45, 25],
        })

        self.page.evaluate('selectProject("project-b")')
        self.page.evaluate('selectProject("project-a")')
        self.assertEqual(self.page.evaluate(
            "[backlogScroll.get('project-a'), statusScroll.get('project-a')]"), [140, 70])

        self.page.evaluate("""({issue}) => {
          const rows = issueCache.get("project-a").slice();
          rows.push(issue);
          issueCache.set("project-a", rows);
          issueCache.set("", [...issueCache.get(""), issue]);
          renderPanes();
        }""", {"issue": self.issue("KIAN-NEW")})
        refreshed = self.page.evaluate("""() => {
          const group = [...document.querySelectorAll("#status-issues .issue-group")]
            .find((node) => node.dataset.group === JSON.stringify(["project-a", "In Progress"]));
          return {
            collapsed: [backlogCollapsed, queueCollapsed],
            hidden: [backlog.hidden, statusIssues.hidden],
            newIssue: Boolean(document.getElementById("issue-open-KIAN-NEW")),
            planOpen: document.querySelector("details[data-issue='KIAN-A1']").open,
            groupOpen: group.open,
            scrolls: [backlogScroll.get("project-a"), statusScroll.get("project-a")],
            pending: pendingPlans.has("KIAN-A1"),
            disabled: document.getElementById("issue-propose-KIAN-A1").disabled,
            requests: window.__requests.length,
          };
        }""")
        self.assertEqual(refreshed, {
            "collapsed": [True, True], "hidden": [True, True], "newIssue": True,
            "planOpen": True, "groupOpen": False, "scrolls": [140, 70],
            "pending": True, "disabled": True, "requests": 1,
        })

        self.page.evaluate("__respond(0, 301)")
        self.page.wait_for_function("pendingPlans.has('KIAN-A1') === false")
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .message').length === 1")
        self.page.locator("#backlog-toggle").click()
        self.page.locator("#queue-toggle").click()
        restored = self.page.evaluate("""() => ({
          collapsed: [backlogCollapsed, queueCollapsed],
          hidden: [backlog.hidden, statusIssues.hidden],
          scrolls: [backlog.scrollTop, statusIssues.scrollTop],
          newIssue: Boolean(document.getElementById("issue-open-KIAN-NEW")),
          planOpen: document.querySelector("details[data-issue='KIAN-A1']").open,
          groupOpen: [...document.querySelectorAll("#status-issues .issue-group")]
            .find((node) => node.dataset.group === JSON.stringify(["project-a", "In Progress"]))
            .open,
          disabled: document.getElementById("issue-propose-KIAN-A1").disabled,
          message: document.querySelector("#messages .message .message-text").textContent,
          requests: window.__requests.length,
        })""")
        self.assertEqual(restored, {
            "collapsed": [False, False], "hidden": [False, False], "scrolls": [140, 70],
            "newIssue": True, "planOpen": True, "groupOpen": False, "disabled": False,
            "message": "Propose a plan for KIAN-A1.", "requests": 1,
        })

        self.page.evaluate(
            'issueCache.set("project-empty", []); selectProject("project-empty")')
        self.page.locator("#backlog-toggle").click()
        self.page.locator("#queue-toggle").click()
        self.page.locator("#backlog-toggle").click()
        self.page.locator("#queue-toggle").click()
        self.assertEqual(self.page.evaluate("""() => ({
          backlog: document.getElementById("backlog").textContent,
          groups: [...document.querySelectorAll("#status-issues .issue-group-list")]
            .map((node) => node.textContent),
          collapsed: [backlogCollapsed, queueCollapsed],
        })"""), {
            "backlog": "No Backlog issues for this project.",
            "groups": ["No Todo issues.", "No In Progress issues.", "No Done issues."],
            "collapsed": [False, False],
        })

    def test_panel_collapse_reclaims_space_across_breakpoints(self):
        self.render_cards([["project-a", [
            self.issue("KIAN-" + str(index)) for index in range(1, 16)
        ]]])

        def metrics():
            return self.page.evaluate("""() => {
              const box = (selector) => {
                const rect = document.querySelector(selector).getBoundingClientRect();
                return {width: rect.width, height: rect.height,
                        top: rect.top, bottom: rect.bottom};
              };
              return {
                chat: box(".chat"),
                backlog: box(".backlog-pane"),
                queue: box(".status-pane"),
                composer: box("#composer"),
                toggles: ["backlog-toggle", "queue-toggle"].map((id) => {
                  const node = document.getElementById(id);
                  const rect = node.getBoundingClientRect();
                  return {visible: node.checkVisibility(), width: rect.width,
                          height: rect.height, bottom: rect.bottom};
                }),
                hidden: [backlog.hidden, statusIssues.hidden],
                collapsed: [backlogCollapsed, queueCollapsed],
                overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
              };
            }""")

        self.page.set_viewport_size({"width": 1200, "height": 800})
        expanded = metrics()
        self.page.locator("#backlog-toggle").click()
        left_rail = metrics()
        self.page.locator("#queue-toggle").click()
        both_rails = metrics()
        self.page.locator("#backlog-toggle").click()
        right_rail = metrics()
        self.assertGreater(left_rail["chat"]["width"], expanded["chat"]["width"] + 150)
        self.assertGreater(both_rails["chat"]["width"], left_rail["chat"]["width"] + 150)
        self.assertGreater(right_rail["chat"]["width"], expanded["chat"]["width"] + 150)
        self.assertLessEqual(left_rail["backlog"]["width"], 57)
        self.assertLessEqual(both_rails["backlog"]["width"], 57)
        self.assertLessEqual(both_rails["queue"]["width"], 57)
        self.assertLessEqual(right_rail["queue"]["width"], 57)

        self.page.evaluate('setPaneCollapsed("backlog", false)')
        self.page.evaluate('setPaneCollapsed("queue", false)')
        self.page.set_viewport_size({"width": 900, "height": 800})
        stacked = metrics()
        self.page.locator("#backlog-toggle").click()
        stacked_left = metrics()
        self.page.locator("#queue-toggle").click()
        stacked_both = metrics()
        self.page.locator("#backlog-toggle").click()
        stacked_right = metrics()
        self.assertGreater(stacked_left["chat"]["height"], stacked["chat"]["height"] + 40)
        self.assertGreater(stacked_both["chat"]["height"], stacked_left["chat"]["height"] + 40)
        self.assertGreater(stacked_right["chat"]["height"], stacked["chat"]["height"] + 40)
        self.assertLessEqual(stacked_left["backlog"]["height"], 70)
        self.assertLessEqual(stacked_both["queue"]["height"], 70)
        self.assertLessEqual(stacked_right["queue"]["height"], 70)
        self.page.locator("#backlog-toggle").click()

        for width in (681, 680, 390, 901, 1200):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 844})
                current = metrics()
                self.assertEqual(current["collapsed"], [True, True])
                self.assertEqual(current["hidden"], [True, True])
                self.assertTrue(all(toggle["visible"] for toggle in current["toggles"]))
                self.assertLessEqual(current["composer"]["bottom"], 845)
                self.assertFalse(current["overflow"])
                if width > 900:
                    self.assertLessEqual(current["backlog"]["width"], 57)
                    self.assertLessEqual(current["queue"]["width"], 57)
                    self.assertGreater(current["chat"]["width"], width - 120)
                else:
                    self.assertLessEqual(current["backlog"]["height"], 70)
                    self.assertLessEqual(current["queue"]["height"], 70)
                    self.assertGreater(current["chat"]["height"], 600)

    def test_sparse_panes_keep_cards_and_groups_at_natural_height(self):
        self.render_cards([["project-a", [
            self.issue("KIAN-1", title="One short issue"),
        ]]])
        sparse = self.page.evaluate("""() => {
          const pane = document.getElementById("backlog");
          const queue = document.getElementById("status-issues");
          const card = pane.querySelector(".issue");
          const row = card.querySelector(".issue-card-row");
          const meta = card.querySelector(".issue-meta");
          const cardRect = card.getBoundingClientRect();
          return {
            paneHeight: pane.clientHeight,
            cardHeight: cardRect.height,
            rowHeight: row.getBoundingClientRect().height,
            trailing: cardRect.bottom - meta.getBoundingClientRect().bottom,
            alignContent: getComputedStyle(pane).alignContent,
            queueAlignContent: getComputedStyle(queue).alignContent,
            emptyGroups: [...queue.querySelectorAll(".issue-group")]
              .map((group) => group.getBoundingClientRect().height),
            queueScroll: queue.scrollHeight,
            queueClient: queue.clientHeight,
          };
        }""")
        self.assertEqual(sparse["alignContent"], "start")
        self.assertEqual(sparse["queueAlignContent"], "start")
        self.assertGreater(sparse["paneHeight"], 600)
        self.assertLess(sparse["cardHeight"], 160)
        self.assertLess(sparse["rowHeight"], 130)
        self.assertLess(sparse["trailing"], 40)
        self.assertTrue(all(height < 120 for height in sparse["emptyGroups"]))
        self.assertLessEqual(sparse["queueScroll"], sparse["queueClient"] + 1)

        self.render_cards([["project-a", [
            self.issue("KIAN-1", title="One short issue"),
            self.issue("KIAN-2"), self.issue("KIAN-3"),
        ]]])
        short_cards = self.page.evaluate("""() => {
          const pane = document.getElementById("backlog");
          return {
            heights: [...pane.querySelectorAll(".issue")]
              .map((card) => card.getBoundingClientRect().height),
            scroll: pane.scrollHeight,
            client: pane.clientHeight,
          };
        }""")
        self.assertTrue(all(height < 160 for height in short_cards["heights"]))
        self.assertLessEqual(short_cards["scroll"], short_cards["client"] + 1)

        self.render_cards([["project-a", [
            self.issue("KIAN-1", title="One short issue"),
            self.issue("KIAN-T1", state="Todo"),
            self.issue("KIAN-T2", state="Todo"),
        ]]])
        queue = self.page.evaluate("""() => {
          const pane = document.getElementById("status-issues");
          const groups = [...pane.querySelectorAll(".issue-group")];
          return {
            heights: groups.map((group) => group.getBoundingClientRect().height),
            cards: [...pane.querySelectorAll(".issue")]
              .map((card) => card.getBoundingClientRect().height),
            scroll: pane.scrollHeight,
            client: pane.clientHeight,
          };
        }""")
        self.assertLess(queue["heights"][0], 420)
        self.assertTrue(all(height < 120 for height in queue["heights"][1:]))
        self.assertTrue(all(height < 160 for height in queue["cards"]))
        self.assertLessEqual(queue["scroll"], queue["client"] + 1)

        self.render_cards([["project-a", [
            self.issue("KIAN-" + str(index)) for index in range(1, 41)
        ]]])
        overflow = self.page.evaluate("""() => {
          const pane = document.getElementById("backlog");
          const before = {scroll: pane.scrollHeight, client: pane.clientHeight};
          pane.scrollTop = 200;
          return {
            before,
            after: pane.scrollTop,
            cardHeight: pane.querySelector(".issue").getBoundingClientRect().height,
          };
        }""")
        self.assertGreater(overflow["before"]["scroll"], overflow["before"]["client"])
        self.assertEqual(overflow["after"], 200)
        self.assertLess(overflow["cardHeight"], 160)

        long_title = "Long issue title " + ("that keeps wrapping " * 60)
        self.render_cards([["project-a", [
            self.issue("KIAN-LONG", title=long_title),
        ]]])
        wrapped = self.page.evaluate("""() => {
          const pane = document.getElementById("backlog");
          const card = pane.querySelector(".issue");
          pane.scrollTop = 100;
          return {
            cardHeight: card.getBoundingClientRect().height,
            titleHeight: card.querySelector(".issue-title").getBoundingClientRect().height,
            horizontalOverflow: pane.scrollWidth > pane.clientWidth + 1,
            scroll: pane.scrollHeight,
            client: pane.clientHeight,
            scrollTop: pane.scrollTop,
          };
        }""")
        self.assertGreater(wrapped["cardHeight"], 300)
        self.assertGreater(wrapped["titleHeight"], 100)
        self.assertFalse(wrapped["horizontalOverflow"])
        self.assertGreater(wrapped["scroll"], wrapped["client"])
        self.assertGreater(wrapped["scrollTop"], 0)

    def test_panel_collapse_controls_fit_coarse_narrow_layout(self):
        coarse = self.browser.new_page(
            viewport={"width": 390, "height": 844}, has_touch=True)
        try:
            self.load_app(coarse)
            coarse.evaluate("""({rows}) => {
              tasks = [];
              selected = "project-a";
              issueCache = new Map([["project-a", rows], ["", rows]]);
              renderPanes();
            }""", {"rows": [
                self.issue("KIAN-1"), self.issue("KIAN-2"),
                self.issue("KIAN-3", state="Todo"),
            ]})
            before = coarse.evaluate("""() => ({
              chat: document.querySelector(".chat").getBoundingClientRect().height,
              row: document.querySelector(".backlog-pane").getBoundingClientRect().height,
            })""")
            coarse.locator("#backlog-toggle").tap()
            coarse.locator("#queue-toggle").tap()
            narrow = coarse.evaluate("""() => {
              const toggle = (id) => {
                const node = document.getElementById(id);
                const rect = node.getBoundingClientRect();
                return {visible: node.checkVisibility(), width: rect.width,
                        height: rect.height, label: node.getAttribute("aria-label")};
              };
              return {
                chat: document.querySelector(".chat").getBoundingClientRect().height,
                rows: [document.querySelector(".backlog-pane").getBoundingClientRect().height,
                       document.querySelector(".status-pane").getBoundingClientRect().height],
                hidden: [backlog.hidden, statusIssues.hidden],
                toggles: [toggle("backlog-toggle"), toggle("queue-toggle")],
                composer: document.querySelector("#composer")
                  .getBoundingClientRect().bottom,
                overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
              };
            }""")
            self.assertTrue(all(
                toggle["visible"] and toggle["width"] >= 44 and toggle["height"] >= 44
                for toggle in narrow["toggles"]))
            self.assertEqual(narrow["hidden"], [True, True])
            self.assertLessEqual(max(narrow["rows"]), 70)
            self.assertGreater(narrow["chat"], before["chat"] + 150)
            self.assertLess(before["row"], 180)
            self.assertLessEqual(narrow["composer"], 845)
            self.assertFalse(narrow["overflow"])
            self.assertEqual([toggle["label"] for toggle in narrow["toggles"]],
                             ["Expand Backlog panel", "Expand Queue panel"])
        finally:
            coarse.close()

    def test_composer_remains_reachable_at_narrow_and_desktop_widths(self):
        for width, height in ((390, 844), (1200, 800)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.evaluate("""() => {
                  for (let id = 1; id <= 12; id += 1) {
                    queueMessage({id, role: "assistant", text: "short",
                      html: "<p>" + "long response ".repeat(20) + "</p>"});
                  }
                  flushChat();
                }""")
                box = self.page.locator("#composer").bounding_box()
                self.assertIsNotNone(box)
                self.assertLessEqual(box["x"] + box["width"], width + 1)
                self.assertLessEqual(box["y"] + box["height"], height + 1)
                self.assertTrue(self.page.locator("#message").is_visible())

    def test_config_dialog_viewer_matches_the_file_and_preserves_drafts(self):
        content = ("# Global AGENTS.md\n\n<script>alert('literal')</script>\n"
                   "- *not emphasis*\n" + "default line\n" * 90)
        self.stub_config_fetch(self.page, content)
        self.page.fill("#message", "Unsent composer draft")
        self.page.evaluate("""() => {
          attachImage(new File([new Uint8Array([1, 2, 3])], "draft.png", {type: "image/png"}));
        }""")
        self.page.locator("#config-open").click()
        self.page.wait_for_function(
            "document.getElementById('config-agents-content').textContent.includes('default line')")
        opened = self.page.evaluate("""() => {
          const dialog = document.getElementById("config-dialog");
          const pre = document.getElementById("config-agents-content");
          return {
            open: dialog.open,
            title: document.getElementById("config-dialog-title").textContent,
            content: pre.textContent,
            script: pre.querySelector("script") !== null,
            emphasis: pre.querySelector("em") !== null,
            selectable: getComputedStyle(pre).userSelect,
            overflow: getComputedStyle(pre).overflowY,
            scrollable: pre.scrollHeight > pre.clientHeight,
          };
        }""")
        self.assertEqual(opened["title"], "Config")
        self.assertTrue(opened["open"])
        self.assertEqual(opened["content"], content)
        self.assertFalse(opened["script"])
        self.assertFalse(opened["emphasis"])
        self.assertEqual(opened["selectable"], "text")
        self.assertEqual(opened["overflow"], "auto")
        self.assertTrue(opened["scrollable"])

        before = self.page.evaluate("""() => ({
          draft: document.getElementById("message").value,
          images: pendingImages.length,
        })""")
        self.page.evaluate("async () => { await poll(); }")
        live = self.page.evaluate("""() => ({
          open: document.getElementById("config-dialog").open,
          content: document.getElementById("config-agents-content").textContent,
          draft: document.getElementById("message").value,
          images: pendingImages.length,
        })""")
        self.assertEqual(live, {**before, "open": True, "content": content})

        self.page.keyboard.press("Escape")
        closed = self.page.evaluate("""() => ({
          open: document.getElementById("config-dialog").open,
          focus: document.activeElement.id,
          draft: document.getElementById("message").value,
          images: pendingImages.length,
        })""")
        self.assertEqual(closed, {"open": False, "focus": "config-open",
                                  "draft": "Unsent composer draft", "images": 1})

        self.page.locator("#config-open").focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("document.getElementById('config-dialog').open")
        keyboard = self.page.evaluate("""() => ({
          focus: document.activeElement.id,
          expanded: document.getElementById("config-open").getAttribute("aria-expanded"),
        })""")
        self.assertEqual(keyboard, {"focus": "config-close", "expanded": "true"})
        self.page.keyboard.press("Tab")
        focus_ring = self.page.evaluate("""() => ({
          focus: document.activeElement.id,
          outline: getComputedStyle(document.activeElement).outlineStyle,
        })""")
        self.assertEqual(focus_ring, {"focus": "config-opencode-key", "outline": "solid"})
        self.page.locator("#config-agents-content").focus()
        pre_ring = self.page.evaluate("""() => ({
          focus: document.activeElement.id,
          outline: getComputedStyle(document.activeElement).outlineStyle,
        })""")
        self.assertEqual(pre_ring, {"focus": "config-agents-content", "outline": "solid"})
        self.page.locator("#config-close").click()
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "config-open")

        self.stub_config_fetch(self.page, error="Fixture could not read the file.")
        self.page.locator("#config-open").click()
        self.page.wait_for_function(
            "document.getElementById('config-agents-status').textContent.includes('Fixture could not read')")
        failed = self.page.evaluate("""() => ({
          status: document.getElementById("config-agents-status").textContent,
          error: document.getElementById("config-agents-status").dataset.error,
          content: document.getElementById("config-agents-content").textContent,
        })""")
        self.assertIn("Could not load global AGENTS.md: Fixture could not read the file.",
                      failed["status"])
        self.assertEqual(failed["error"], "true")
        self.assertEqual(failed["content"], "")
        self.page.keyboard.press("Escape")

        self.stub_config_fetch(self.page, "")
        self.page.locator("#config-open").click()
        self.page.wait_for_function(
            "document.getElementById('config-agents-status').textContent.includes('No global defaults')")
        recovered = self.page.evaluate("""() => ({
          content: document.getElementById("config-agents-content").textContent,
          status: document.getElementById("config-agents-status").textContent,
          error: document.getElementById("config-agents-status").dataset.error,
          draft: document.getElementById("message").value,
          images: pendingImages.length,
        })""")
        self.assertEqual(recovered, {
          "content": "", "status": "No global defaults are currently defined.",
          "error": "", "draft": "Unsent composer draft", "images": 1,
        })

    def test_config_dialog_reopen_ignores_stale_close_event(self):
        self.page.evaluate("""() => {
          window.__agentRequests = 0;
          window.__agentResolvers = [];
          window.fetch = (path) => {
            if (path === "/api/agents") {
              window.__agentRequests += 1;
              return new Promise((resolve) => window.__agentResolvers.push(resolve));
            }
            return Promise.resolve({ok: true, json: async () => ({
              messages: [], busy: false, canInterrupt: false, stream: "", streamHtml: "",
              tasks: [], signedIn: true, login: null, paused: "", view: "",
              codex: {}, issueError: "", updateAvailable: false, draining: false,
            })});
          };
        }""")
        self.page.evaluate("""() => {
          openConfig();
          closeConfig();
          openConfig();
        }""")
        self.assertEqual(self.page.evaluate("() => window.__agentRequests"), 2)
        self.page.evaluate("""() => new Promise((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })""")
        stale = self.page.evaluate("""() => ({
          open: document.getElementById("config-dialog").open,
          expanded: document.getElementById("config-open").getAttribute("aria-expanded"),
          focus: document.activeElement.id,
          requests: window.__agentRequests,
        })""")
        self.assertEqual(stale, {"open": True, "expanded": "true",
                                 "focus": "config-close", "requests": 2})
        self.page.evaluate("""() => {
          window.__agentResolvers[0]({ok: true, json: async () => ({content: "stale"})});
          window.__agentResolvers[1]({ok: true, json: async () => ({content: "current"})});
        }""")
        self.page.wait_for_function(
            "() => document.getElementById('config-agents-content').textContent === 'current'")
        loaded = self.page.evaluate("""() => ({
          open: document.getElementById("config-dialog").open,
          expanded: document.getElementById("config-open").getAttribute("aria-expanded"),
          focus: document.activeElement.id,
          content: document.getElementById("config-agents-content").textContent,
          requests: window.__agentRequests,
        })""")
        self.assertEqual(loaded, {"open": True, "expanded": "true",
                                  "focus": "config-close", "content": "current",
                                  "requests": 2})
        self.page.keyboard.press("Escape")
        self.page.wait_for_function("""() => {
          return !document.getElementById("config-dialog").open
            && document.getElementById("config-open").getAttribute("aria-expanded") === "false"
            && document.activeElement.id === "config-open";
        }""")
        closed = self.page.evaluate("""() => ({
          open: document.getElementById("config-dialog").open,
          expanded: document.getElementById("config-open").getAttribute("aria-expanded"),
          focus: document.activeElement.id,
        })""")
        self.assertEqual(closed, {"open": False, "expanded": "false",
                                  "focus": "config-open"})

    def test_config_dialog_fits_light_dark_and_coarse_narrow_layouts(self):
        content = "# Global AGENTS.md\n\n" + "default line\n" * 90
        desktop = {}
        for scheme in ("light", "dark"):
            self.page.emulate_media(color_scheme=scheme)
            self.stub_config_fetch(self.page, content)
            self.page.locator("#config-open").click()
            self.page.wait_for_function(
                "document.getElementById('config-agents-content').textContent.includes('default line')")
            desktop[scheme] = self.page.evaluate("""() => {
              const dialog = document.getElementById("config-dialog");
              const button = document.getElementById("config-open");
              const pre = document.getElementById("config-agents-content");
              const box = dialog.getBoundingClientRect();
              const target = button.getBoundingClientRect();
              return {
                inside: box.left >= 0 && box.top >= 0
                  && box.right <= window.innerWidth + 1 && box.bottom <= window.innerHeight + 1,
                overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
                buttonSize: [target.width, target.height],
              };
            }""")
            self.page.keyboard.press("Escape")
        for scheme in ("light", "dark"):
            self.assertTrue(desktop[scheme]["inside"])
            self.assertFalse(desktop[scheme]["overflow"])

        coarse = self.browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        try:
            self.load_app(coarse)
            self.stub_config_fetch(coarse, content)
            coarse.locator("#config-open").tap()
            coarse.wait_for_function(
                "document.getElementById('config-agents-content').textContent.includes('default line')")
            narrow = coarse.evaluate("""() => {
              const dialog = document.getElementById("config-dialog");
              const button = document.getElementById("config-open");
              const pre = document.getElementById("config-agents-content");
              const box = dialog.getBoundingClientRect();
              const target = button.getBoundingClientRect();
              return {
                inside: box.left >= 0 && box.top >= 0
                  && box.right <= window.innerWidth + 1 && box.bottom <= window.innerHeight + 1,
                overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
                buttonSize: [target.width, target.height],
                scrollable: pre.scrollHeight > pre.clientHeight,
                closeSize: (() => {
                  const close = document.getElementById("config-close").getBoundingClientRect();
                  return [close.width, close.height];
                })(),
              };
            }""")
            self.assertTrue(narrow["inside"])
            self.assertFalse(narrow["overflow"])
            self.assertGreaterEqual(narrow["buttonSize"][1], 44)
            self.assertGreaterEqual(narrow["closeSize"][1], 44)
            self.assertTrue(narrow["scrollable"])
            coarse.keyboard.press("Escape")
            self.assertEqual(coarse.evaluate("document.activeElement.id"), "config-open")
        finally:
            coarse.close()

    def test_served_app_uses_production_headers_and_fixed_agents_endpoint(self):
        from symphony.__main__ import CONTENT_SECURITY_POLICY

        content = (Path(__file__).resolve().parents[1] / "agents/AGENTS.md").read_text()
        static = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "application/javascript; charset=utf-8"),
            "/issue.js": ("issue.js", "application/javascript; charset=utf-8"),
            "/panes.js": ("panes.js", "application/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/favicon.ico": ("favicon.ico", "image/vnd.microsoft.icon"),
        }

        class Handler(BaseHTTPRequestHandler):
            mode = "ok"
            state_requests = 0

            def log_message(self, *_):
                pass

            def respond(self, body, content_type, status=200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path in static:
                    name, content_type = static[path]
                    self.respond((ROOT / name).read_bytes(), content_type)
                elif path == "/api/config":
                    self.respond(json.dumps({
                        "opencodeGo": {"configured": False, "malformed": False, "busy": False},
                        "codex": {"signedIn": True, "account": {"type": "chatgpt"}, "login": None, "busy": False},
                    }).encode(), "application/json; charset=utf-8")
                elif path == "/api/agents":
                    if type(self).mode == "error":
                        self.respond(json.dumps({"error": "Fixture read failed."}).encode(),
                                     "application/json; charset=utf-8", 503)
                    else:
                        body = "" if type(self).mode == "empty" else content
                        self.respond(json.dumps({"content": body}).encode(),
                                     "application/json; charset=utf-8")
                elif path == "/api/state":
                    type(self).state_requests += 1
                    self.respond(json.dumps({
                        "messages": [], "busy": False, "canInterrupt": False,
                        "stream": "", "streamHtml": "", "tasks": [], "signedIn": True,
                        "login": None, "paused": "", "view": "", "codex": {},
                        "issueError": "", "updateAvailable": False, "draining": False,
                    }).encode(), "application/json; charset=utf-8")
                else:
                    self.respond(b'{"error":"Not found."}',
                                 "application/json; charset=utf-8", 404)

        def wait_until(predicate, timeout=5):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if predicate():
                    return True
                time.sleep(.05)
            return False

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/"
        page = self.browser.new_page(viewport={"width": 1200, "height": 800})
        self.addCleanup(page.close)
        page.goto(url, wait_until="load")
        page.wait_for_selector("#config-open")
        self.assertTrue(wait_until(lambda: Handler.state_requests > 0))
        page.fill("#message", "Unsent served draft")
        page.evaluate("""() => {
          attachImage(new File([new Uint8Array([1, 2, 3])], "draft.png",
            {type: "image/png"}));
        }""")

        page.locator("#config-open").focus()
        page.keyboard.press("Enter")
        self.assertTrue(wait_until(lambda: page.evaluate(
            "() => document.getElementById('config-dialog').open")))
        self.assertTrue(wait_until(lambda: page.evaluate(
            "() => document.getElementById('config-agents-content').textContent")
            == content))
        before = Handler.state_requests
        self.assertTrue(wait_until(lambda: Handler.state_requests > before, timeout=4))
        state = page.evaluate("""() => {
          const dialog = document.getElementById("config-dialog");
          const pre = document.getElementById("config-agents-content");
          const box = dialog.getBoundingClientRect();
          return {
            open: dialog.open,
            script: pre.querySelector("script") !== null,
            emphasis: pre.querySelector("em") !== null,
            selectable: getComputedStyle(pre).userSelect,
            overflow: getComputedStyle(pre).overflowY,
            inside: box.left >= 0 && box.top >= 0
              && box.right <= window.innerWidth + 1
              && box.bottom <= window.innerHeight + 1,
            horizontalOverflow: document.documentElement.scrollWidth
              > window.innerWidth + 1,
            draft: document.getElementById("message").value,
            images: pendingImages.length,
          };
        }""")
        self.assertEqual(state, {
            "open": True, "script": False, "emphasis": False,
            "selectable": "text", "overflow": "auto", "inside": True,
            "horizontalOverflow": False, "draft": "Unsent served draft", "images": 1,
        })
        page.keyboard.press("Escape")
        self.assertTrue(wait_until(lambda: not page.evaluate(
            "() => document.getElementById('config-dialog').open")))
        self.assertEqual(page.evaluate("() => document.activeElement.id"), "config-open")

        Handler.mode = "error"
        page.locator("#config-open").click()
        self.assertTrue(wait_until(lambda: "Fixture read failed." in page.evaluate(
            "() => document.getElementById('config-agents-status').textContent")))
        error = page.evaluate("""() => ({
          content: document.getElementById("config-agents-content").textContent,
          draft: document.getElementById("message").value,
        })""")
        self.assertEqual(error, {"content": "", "draft": "Unsent served draft"})
        page.keyboard.press("Escape")

        Handler.mode = "empty"
        page.locator("#config-open").click()
        self.assertTrue(wait_until(lambda: "No global defaults" in page.evaluate(
            "() => document.getElementById('config-agents-status').textContent")))
        self.assertEqual(page.evaluate(
            "() => document.getElementById('config-agents-content').textContent"), "")
        page.keyboard.press("Escape")

        Handler.mode = "ok"
        fixed = page.evaluate("""async () => {
          const same = await fetch("/api/agents?path=/etc/passwd");
          const body = await same.json();
          const rejected = await fetch("/api/agents/../../etc/passwd");
          return {sameStatus: same.status, sameContent: body.content,
                  rejectedStatus: rejected.status};
        }""")
        self.assertEqual(fixed, {"sameStatus": 200, "sameContent": content,
                                 "rejectedStatus": 404})

        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.locator("#config-open").click()
            self.assertTrue(wait_until(lambda: page.evaluate(
                "() => document.getElementById('config-dialog').open")))
            self.assertTrue(page.evaluate("""() => {
              const box = document.getElementById("config-dialog").getBoundingClientRect();
              return box.left >= 0 && box.top >= 0
                && box.right <= window.innerWidth + 1
                && box.bottom <= window.innerHeight + 1;
            }"""))
            page.keyboard.press("Escape")

        coarse = self.browser.new_page(
            viewport={"width": 390, "height": 844}, has_touch=True)
        try:
            coarse.emulate_media(color_scheme="dark")
            coarse.goto(url, wait_until="load")
            coarse.wait_for_selector("#config-open")
            coarse.locator("#config-open").tap()
            self.assertTrue(wait_until(lambda: coarse.evaluate(
                "() => document.getElementById('config-dialog').open")))
            narrow = coarse.evaluate("""() => {
              const box = document.getElementById("config-dialog").getBoundingClientRect();
              return {
                inside: box.left >= 0 && box.top >= 0
                  && box.right <= window.innerWidth + 1
                  && box.bottom <= window.innerHeight + 1,
                overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
                buttonHeight: document.getElementById("config-open")
                  .getBoundingClientRect().height,
                closeHeight: document.getElementById("config-close")
                  .getBoundingClientRect().height,
              };
            }""")
            self.assertTrue(narrow["inside"])
            self.assertFalse(narrow["overflow"])
            self.assertGreaterEqual(narrow["buttonHeight"], 44)
            self.assertGreaterEqual(narrow["closeHeight"], 44)
        finally:
            coarse.close()

    def test_config_dialog_manages_opencode_go_and_codex_accounts(self):
        self.page.fill("#message", "Keep composer draft")
        self.page.evaluate("""() => {
          window.__cfg = {
            opencodeGo: {configured: false, malformed: false, busy: false},
            codex: {signedIn: false, account: {}, login: null, busy: false},
          };
          window.__cfgPosts = [];
          window.__failNextKey = true;
          window.fetch = async (path, options = {}) => {
            if (path === "/api/agents") return {ok: true, json: async () => ({content: "global rules"})};
            if (path === "/api/config") {
              if ((options.method || "GET") === "POST") {
                const body = JSON.parse(options.body);
                window.__cfgPosts.push(body);
                if (body.action === "set_opencode_key") {
                  if (window.__failNextKey) {
                    window.__failNextKey = false;
                    return {ok: false, json: async () => ({error: "Rejected key."})};
                  }
                  window.__cfg.opencodeGo.configured = true;
                } else if (body.action === "clear_opencode_key") {
                  window.__cfg.opencodeGo.configured = false;
                } else if (body.action === "codex_login") {
                  window.__cfg.codex.login = {url: "https://example.test/code", code: "DEV-1234"};
                } else if (body.action === "codex_cancel_login") {
                  window.__cfg.codex.login = null;
                } else if (body.action === "codex_logout") {
                  window.__cfg.codex.signedIn = false;
                  window.__cfg.codex.account = {};
                }
                return {ok: true, json: async () => window.__cfg};
              }
              return {ok: true, json: async () => window.__cfg};
            }
            return {ok: true, json: async () => ({
              messages: [], busy: false, canInterrupt: false, stream: "", streamHtml: "",
              tasks: [], signedIn: window.__cfg.codex.signedIn, login: window.__cfg.codex.login,
              paused: "", view: "", codex: {}, issueError: "", updateAvailable: false, draining: false,
            })};
          };
        }""")
        self.page.locator("#config-open").click()
        self.page.wait_for_function(
            "document.getElementById('config-opencode-status').textContent.includes('Not configured.')")
        self.page.fill("#config-opencode-key", "canary-bad")
        self.page.locator("#config-opencode-save").click()
        self.page.wait_for_function(
            "document.getElementById('config-opencode-feedback').textContent.includes('Rejected key.')")
        self.assertEqual(self.page.input_value("#config-opencode-key"), "canary-bad")
        self.page.fill("#config-opencode-key", "canary-good")
        self.page.locator("#config-opencode-save").click()
        self.page.wait_for_function(
            "document.getElementById('config-opencode-status').textContent.includes('API key configured on PVC.')")
        self.assertEqual(self.page.input_value("#config-opencode-key"), "")
        self.page.locator("#config-codex-signin").click()
        self.page.wait_for_function(
            "document.getElementById('config-codex-login').textContent.includes('DEV-1234')")
        self.page.locator("#config-codex-cancel").click()
        self.page.wait_for_function(
            "document.getElementById('config-codex-login').hidden === true")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.input_value("#message"), "Keep composer draft")


    def test_per_repo_chat_isolation_scroll_and_unread_chip_dots(self):
        self.page.evaluate("""() => {
          const projects = [
            {id: "proj-a", name: "Repo A", icon: "", repo: "owner/a", issues: []},
            {id: "proj-b", name: "Repo B", icon: "", repo: "owner/b", issues: []},
            {id: "proj-b2", name: "Repo B Two", icon: "", repo: "owner/b", issues: []},
          ];
          window.__repoChats = {
            "owner/a": Array.from({length: 30}, (_, i) => ({
              id: i + 1, role: "user", text: "Message A" + (i + 1) + "\\nline\\nline", images: [], repo: "owner/a",
            })),
            "owner/b": [{id: 100, role: "assistant", text: "Hello B", html: "<p>Hello B</p>", images: [], repo: "owner/b"}],
          };
          window.__repoActivity = {"owner/a": 30, "owner/b": 100};
          window.fetch = async (path) => {
            const url = new URL(path, "https://example.test");
            const proj = url.searchParams.get("project") || "proj-a";
            const after = Number(url.searchParams.get("after") || "0");
            const repo = proj === "proj-a" ? "owner/a" : "owner/b";
            const msgs = (window.__repoChats[repo] || []).filter((m) => m.id > after);
            return {ok: true, json: async () => ({
              messages: msgs, busy: false, canInterrupt: false, stream: "", streamHtml: "",
              tasks: [], signedIn: true, login: null, paused: "", view: "proj-a",
              repo, repoActivity: {...window.__repoActivity}, codex: {}, issueError: "",
              updateAvailable: false, draining: false, projects, issueRevision: "rev1",
            })};
          };
        }""")
        self.page.evaluate("async () => { await poll(); flushChat(); }")
        self.assertEqual(self.page.evaluate("""() => ({
          selected, selectedRepo,
          msgs: document.querySelectorAll("#messages .message").length,
          unreadA: document.querySelector('[data-project="proj-a"]').hasAttribute("data-unread"),
          unreadB: document.querySelector('[data-project="proj-b"]').hasAttribute("data-unread"),
        })"""), {"selected": "proj-a", "selectedRepo": "owner/a", "msgs": 30, "unreadA": False, "unreadB": False})

        # Scroll repo A partway up, switch to repo B, verify repo B chat + /new command suggestion.
        self.page.evaluate("() => { chatLog.scrollTop = 120; }")
        self.page.locator('[data-project="proj-b"]').click()
        self.page.wait_for_function("document.querySelectorAll('#messages .message').length === 1")
        self.assertEqual(self.page.evaluate("""() => ({
          selected, selectedRepo,
          firstText: document.querySelector("#messages .message-text").textContent,
        })"""), {"selected": "proj-b", "selectedRepo": "owner/b", "firstText": "Hello B"})

        # Switching to proj-b2 (same repo owner/b) keeps chat intact.
        self.page.locator('[data-project="proj-b2"]').click()
        self.assertEqual(self.page.evaluate("() => document.querySelectorAll('#messages .message').length"), 1)

        # New activity in owner/a marks proj-a chip unread while viewing owner/b.
        self.page.evaluate("""async () => {
          window.__repoChats["owner/a"].push({id: 101, role: "worker", text: "KIAN-1 · Review", images: [], repo: "owner/a"});
          window.__repoActivity["owner/a"] = 101;
          await poll();
        }""")
        self.assertTrue(self.page.locator('[data-project="proj-a"]').evaluate("n => n.hasAttribute('data-unread')"))

        # Selecting proj-a clears unread dot, fetches owner/a messages, and restores scrollTop.
        self.page.locator('[data-project="proj-a"]').click()
        self.page.wait_for_function("document.querySelectorAll('#messages .message').length === 31")
        self.assertEqual(self.page.evaluate("""() => ({
          unreadA: document.querySelector('[data-project="proj-a"]').hasAttribute("data-unread"),
          scrollTop: chatLog.scrollTop,
          commands: (messageField.value = "/", matchingCommands().map(c => c[0])),
        })"""), {
            "unreadA": False, "scrollTop": 120,
            "commands": ["/model ", "/effort ", "/new", "/compact", "/status", "/mcp",
                         "/interrupt", "/login", "/continue", "/retry "],
        })

    def test_plan_proposal_accept_button_in_chat_and_card_details(self):
        hash1 = "1" * 64
        hash2 = "2" * 64
        self.page.evaluate("""({hash1, hash2}) => {
          window.__approveCalls = [];
          window.__failApproveOnce = true;
          const projects = [{
            id: "proj-a", name: "Repo A", icon: "", repo: "owner/a",
            issues: [{
              identifier: "KIAN-50", title: "Accept card", url: "https://example.test/50",
              state: {name: "Backlog", type: "backlog", color: "#999"},
              priority: 2, priorityLabel: "High", estimate: 2, dueDate: null,
              labels: [], plan: "### Plan v2", planHtml: "<p>Plan v2</p>", planHash: hash2,
            }],
          }];
          window.fetch = async (path, options = {}) => {
            if (path === "/api/approve") {
              const body = JSON.parse(options.body);
              window.__approveCalls.push(body);
              if (window.__failApproveOnce) {
                window.__failApproveOnce = false;
                return {ok: false, json: async () => ({error: "Plan hash changed."})};
              }
              return {ok: true, json: async () => ({approved: body.issue, repo: "owner/a"})};
            }
            return {ok: true, json: async () => ({
              messages: [
                {id: 1, role: "system", text: "Proposed plan v1", images: [], repo: "owner/a",
                 meta: {kind: "plan_proposal", issue: "KIAN-50", title: "Accept card", planHash: hash1}},
                {id: 2, role: "system", text: "Proposed plan v2", images: [], repo: "owner/a",
                 meta: {kind: "plan_proposal", issue: "KIAN-50", title: "Accept card", planHash: hash2}},
              ],
              busy: false, canInterrupt: false, stream: "", streamHtml: "", tasks: [],
              signedIn: true, login: null, paused: "", view: "proj-a", repo: "owner/a",
              repoActivity: {"owner/a": 2}, codex: {}, issueError: "",
              updateAvailable: false, draining: false, projects, issueRevision: "rev-approve",
            })};
          };
        }""", {"hash1": hash1, "hash2": hash2})
        self.page.evaluate("async () => { await poll(); flushChat(); }")
        self.page.locator("#backlog summary + *").wait_for(state="attached")
        # Inline error retains active Accept button; clicking again succeeds and marks Approved.
        latest_btn = self.page.locator("#messages .plan-accept-button").nth(1)
        latest_btn.click()
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .plan-accept-error')[1].textContent.includes('Plan hash changed.')")
        self.assertFalse(latest_btn.is_disabled())
        latest_btn.click()
        self.page.wait_for_function(
            "document.querySelectorAll('#messages .plan-accept-button')[1].textContent === 'Approved'")
        self.assertEqual(self.page.locator("#messages .plan-accept-button").nth(0).text_content(), "Approved")
        self.assertEqual(self.page.locator("#backlog .plan-accept-button").text_content(), "Approved")
        self.assertTrue(self.page.locator("#backlog .plan-accept-button").is_disabled())

    def test_repo_prompt_buttons_render_per_repo_and_prepend_on_submit(self):
        self.page.evaluate("""() => {
          window.__postedTexts = [];
          const projects = [
            {id: "proj-a", name: "Repo A", icon: "", repo: "owner/a", issues: []},
            {id: "proj-b", name: "Repo B", icon: "", repo: "owner/b", issues: []},
          ];
          const buttonsByRepo = {
            "owner/a": [
              {repo: "owner/a", label: "Idle note", prompt: "[Keep idle]", mode: "attach"},
              {repo: "owner/a", label: "Quick ping", prompt: "Ping", mode: "send"},
            ],
            "owner/b": [{repo: "owner/b", label: "Only B", prompt: "B prompt", mode: "attach"}],
          };
          window.fetch = async (path, options = {}) => {
            if (path === "/api/chat") {
              const body = JSON.parse(options.body);
              window.__postedTexts.push(body.text);
              return {ok: true, json: async () => ({message: {id: window.__postedTexts.length, role: "user", text: body.text, images: []}})};
            }
            const url = new URL(path, "https://example.test");
            const proj = url.searchParams.get("project") || "proj-a";
            const repo = proj === "proj-a" ? "owner/a" : "owner/b";
            return {ok: true, json: async () => ({
              messages: [], busy: false, canInterrupt: false, stream: "", streamHtml: "",
              tasks: [], signedIn: true, login: null, paused: "", view: "proj-a",
              repo, repoActivity: {}, buttons: buttonsByRepo[repo] || [],
              codex: {}, issueError: "", updateAvailable: false, draining: false,
              projects, issueRevision: "rev-btn",
            })};
          };
        }""")
        self.page.evaluate("async () => { await poll(); }")
        self.page.locator(".repo-prompt-button").first.wait_for(state="visible")
        self.assertEqual(
            [btn.text_content() for btn in self.page.locator(".repo-prompt-button").all()],
            ["Idle note", "Quick ping"],
        )
        attach_btn = self.page.locator(".repo-prompt-button").first
        attach_btn.click()
        self.assertEqual(attach_btn.get_attribute("aria-pressed"), "true")
        self.page.fill("#message", "Go ahead")
        self.page.locator("#message").press("Enter")
        self.page.wait_for_function("window.__postedTexts.length === 1")
        self.assertEqual(self.page.evaluate("() => window.__postedTexts"), ["[Keep idle]\n\nGo ahead"])
        self.assertEqual(attach_btn.get_attribute("aria-pressed"), "false")


if __name__ == "__main__":
    unittest.main()
