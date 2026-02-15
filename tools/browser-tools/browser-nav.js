#!/usr/bin/env node
// Navigate the browser to a URL, optionally in a new tab.
// Usage: browser-nav <url> [--new] [--reload] [--port PORT] [--wait STRATEGY]
//
// --new        Open URL in a new tab instead of the active one
// --reload     Reload the current page (url argument ignored)
// --port PORT  Debugging port (default 9222)
// --wait STRATEGY  "load" (default), "domcontentloaded", or "networkidle0"
//
// Outputs: JSON with { url, title, status }

const puppeteer = require("puppeteer-core");

const args = process.argv.slice(2);
let url = null;
let newTab = false;
let reload = false;
let port = 9222;
let waitUntil = "load";

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--new") newTab = true;
  else if (args[i] === "--reload") reload = true;
  else if (args[i] === "--port" && args[i + 1]) port = parseInt(args[++i], 10);
  else if (args[i] === "--wait" && args[i + 1]) waitUntil = args[++i];
  else if (!args[i].startsWith("--") && !url) url = args[i];
}

if (!url && !reload) {
  console.error("Usage: browser-nav <url> [--new] [--reload] [--port PORT] [--wait STRATEGY]");
  process.exit(1);
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

  let page;
  if (newTab) {
    page = await browser.newPage();
  } else {
    const pages = await browser.pages();
    page = pages[pages.length - 1] || (await browser.newPage());
  }

  try {
    if (reload) {
      await page.reload({ waitUntil, timeout: 30000 });
    } else {
      await page.goto(url, { waitUntil, timeout: 30000 });
    }
  } catch (err) {
    // Navigation might time out but still partially load
    if (!err.message.includes("timeout")) throw err;
  }

  const result = {
    url: page.url(),
    title: await page.title(),
  };
  console.log(JSON.stringify(result, null, 2));
  browser.disconnect();
}

main().catch((err) => {
  console.error("ERROR:", err.message);
  process.exit(1);
});
