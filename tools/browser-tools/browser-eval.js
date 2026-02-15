#!/usr/bin/env node
// Execute JavaScript in the active browser tab's page context.
// Usage: browser-eval <js-expression> [--port PORT]
//        echo "document.title" | browser-eval --stdin [--port PORT]
//
// The expression is evaluated via page.evaluate(). The return value is
// JSON-stringified and printed to stdout. DOM nodes are not serializable;
// return primitive values, arrays, or plain objects.

const puppeteer = require("puppeteer-core");

const args = process.argv.slice(2);
let expr = null;
let useStdin = false;
let port = 9222;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--stdin") useStdin = true;
  else if (args[i] === "--port" && args[i + 1]) port = parseInt(args[++i], 10);
  else if (!args[i].startsWith("--") && !expr) expr = args[i];
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8").trim();
}

async function main() {
  if (useStdin) expr = await readStdin();
  if (!expr) {
    console.error("Usage: browser-eval <js-expression> [--port PORT]");
    console.error("       echo 'code' | browser-eval --stdin [--port PORT]");
    process.exit(1);
  }

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

  // Wrap in an async IIFE so `await` works in the expression
  const wrapped = `(async () => { return (${expr}); })()`;
  try {
    const result = await page.evaluate(wrapped);
    if (result !== undefined) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log("undefined");
    }
  } catch (err) {
    console.error("EVAL ERROR:", err.message);
    process.exit(1);
  }

  browser.disconnect();
}

main().catch((err) => {
  console.error("ERROR:", err.message);
  process.exit(1);
});
