#!/usr/bin/env node
// Capture a screenshot of the active browser tab.
// Usage: browser-screenshot [--port PORT] [--full] [--output PATH]
//
// --full         Capture the full scrollable page, not just the viewport
// --output PATH  Write to this path instead of a temp file
// --port PORT    Debugging port (default 9222)
//
// Outputs: JSON with { path, width, height }

const puppeteer = require("puppeteer-core");
const os = require("os");
const path = require("path");

const args = process.argv.slice(2);
let fullPage = false;
let outputPath = null;
let port = 9222;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--full") fullPage = true;
  else if (args[i] === "--output" && args[i + 1]) outputPath = args[++i];
  else if (args[i] === "--port" && args[i + 1]) port = parseInt(args[++i], 10);
}

async function main() {
  let browser;
  try {
    browser = await puppeteer.connect({
      browserURL: `http://127.0.0.1:${port}`,
    });
  } catch {
    console.error(`ERROR: Cannot connect to browser on port ${port}. Run browser-start first.`);
    process.exit(1);
  }

  const pages = await browser.pages();
  const page = pages[pages.length - 1];
  if (!page) {
    console.error("ERROR: No open tabs found.");
    browser.disconnect();
    process.exit(1);
  }

  if (!outputPath) {
    const ts = Date.now();
    outputPath = path.join(os.tmpdir(), `browser-screenshot-${ts}.png`);
  }

  await page.screenshot({
    path: outputPath,
    fullPage,
  });

  const viewport = page.viewport() || { width: 0, height: 0 };
  console.log(JSON.stringify({
    path: outputPath,
    width: viewport.width,
    height: viewport.height,
    fullPage,
    url: page.url(),
  }, null, 2));

  browser.disconnect();
}

main().catch((err) => {
  console.error("ERROR:", err.message);
  process.exit(1);
});
