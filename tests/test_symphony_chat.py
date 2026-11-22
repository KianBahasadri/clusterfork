"""Exercise Symphony chat rendering and scheduling in managed Chromium."""

import os
from pathlib import Path
import re
import shutil
import unittest

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
        html = (ROOT / "index.html").read_text()
        html = re.sub(r'<script src="[^"]+" defer></script>\s*', "", html)
        html = html.replace('<link rel="stylesheet" href="/style.css">', "")
        self.page.set_content(html, wait_until="domcontentloaded")
        self.page.add_style_tag(path=str(ROOT / "style.css"))
        self.page.add_script_tag(path=str(ROOT / "panes.js"))
        self.page.add_script_tag(path=str(ROOT / "app.js"))

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


if __name__ == "__main__":
    unittest.main()
