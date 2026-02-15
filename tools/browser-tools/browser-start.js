#!/usr/bin/env node
// Launch Chromium with remote debugging enabled.
// Usage: browser-start [--port PORT]
//
// Starts Chromium on the given debugging port (default 9222).
// If a Chromium process is already listening on that port, prints its info and exits.

const { execSync, spawn } = require("child_process");
const http = require("http");
const path = require("path");

const args = process.argv.slice(2);
let port = 9222;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--port" && args[i + 1]) port = parseInt(args[++i], 10);
}

// Detect Chromium binary
function findChromium() {
  const candidates = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
  ];
  for (const bin of candidates) {
    try {
      const p = execSync(`which ${bin}`, { encoding: "utf8" }).trim();
      if (p) return p;
    } catch {}
  }
  return null;
}

// Check if debugger is already listening
function checkDebugger(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/json/version`, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(null);
        }
      });
    });
    req.on("error", () => resolve(null));
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(null);
    });
  });
}

async function main() {
  // Check if already running
  const existing = await checkDebugger(port);
  if (existing) {
    console.log(JSON.stringify({ status: "already_running", port, browser: existing.Browser || existing.product }, null, 2));
    process.exit(0);
  }

  const chromiumPath = findChromium();
  if (!chromiumPath) {
    console.error("ERROR: Could not find Chromium or Chrome. Install chromium or google-chrome.");
    process.exit(1);
  }

  // Use a persistent user-data-dir so sessions survive restarts
  const userDataDir = path.join(
    process.env.HOME || "/tmp",
    ".cache",
    "opencode-browser"
  );

  const chromiumArgs = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
    "--no-first-run",
    "--no-default-browser-check",
  ];

  const child = spawn(chromiumPath, chromiumArgs, {
    detached: true,
    stdio: "ignore",
  });
  child.unref();

  // Wait for debugger to become available
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const info = await checkDebugger(port);
    if (info) {
      console.log(JSON.stringify({ status: "started", pid: child.pid, port, browser: info.Browser || info.product }, null, 2));
      process.exit(0);
    }
  }

  console.error("ERROR: Chromium started but debugger did not respond within 15 seconds.");
  process.exit(1);
}

main();
