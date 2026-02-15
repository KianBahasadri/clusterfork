---
name: browser-tools
description: Control a local Chromium browser — start it, navigate to URLs, execute JavaScript in pages, and take screenshots. Useful for testing web apps, scraping page content, and inspecting rendered output.
---

## Tools

All tools require Chromium to be running with remote debugging. Start it first with `browser-start`.

### browser-start

Launch Chromium with remote debugging enabled. Safe to call if already running.

```bash
browser-start [--port PORT]
```

Returns JSON: `{ status, port, pid, browser }`

### browser-nav

Navigate the active tab (or open a new one) to a URL.

```bash
browser-nav <url> [--new] [--reload] [--wait load|domcontentloaded|networkidle0] [--port PORT]
```

- `--new` — open in a new tab instead of reusing the active one
- `--reload` — reload the current page (ignores the url argument)
- `--wait` — when to consider navigation complete (default: `load`)

Returns JSON: `{ url, title }`

### browser-eval

Execute a JavaScript expression in the active tab's page context.

```bash
browser-eval '<expression>' [--port PORT]
echo '<expression>' | browser-eval --stdin [--port PORT]
```

The expression runs inside an async IIFE, so `await` works. Return primitive values, arrays, or plain objects — DOM nodes are not serializable.

Returns the JSON-stringified result.

**Examples:**

```bash
# Get the page title
browser-eval 'document.title'

# Get all links on the page
browser-eval 'Array.from(document.querySelectorAll("a")).map(a => ({text: a.textContent.trim(), href: a.href})).filter(a => a.text)'

# Get the text content of the page body
browser-eval 'document.body.innerText'

# Wait for an element and read it
browser-eval 'await new Promise(r => { const i = setInterval(() => { const el = document.querySelector("#result"); if (el) { clearInterval(i); r(el.textContent); } }, 200); })'
```

### browser-screenshot

Capture a screenshot of the active tab's viewport.

```bash
browser-screenshot [--full] [--output /path/to/file.png] [--port PORT]
```

- `--full` — capture the full scrollable page, not just the viewport
- `--output` — write to this path instead of a temp file

Returns JSON: `{ path, width, height, fullPage, url }`

## Typical workflow

1. `browser-start` — launches Chromium (or confirms it's already running)
2. `browser-nav https://example.com` — navigate to a page
3. `browser-eval 'document.body.innerText'` — read page content
4. `browser-screenshot` — capture what the page looks like
5. `browser-nav https://other.com --new` — open another page in a new tab

## Notes

- All tools connect to `127.0.0.1:9222` by default. Use `--port` to change.
- Chromium runs detached — closing your terminal won't kill it.
- User data persists in `~/.cache/opencode-browser/` across sessions.
- To stop Chromium: `pkill -f 'chromium.*remote-debugging-port'`
