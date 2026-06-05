// Botflow Companion — local macOS agent.
//
// Runs on the USER's Mac and bridges Botflow (botflow.io, in the browser) to a
// physically-connected iPhone: lists devices and installs/launches device builds
// that the cloud can't reach (the iPhone is paired/trusted with THIS Mac).
//
// Listens loopback-only on :17321 and speaks the contract the web UI expects
// (src/components/persistent-workspace/iphone-device-runner.tsx):
//   GET  /botflow/v1/health        -> { ok, app, source }
//   GET  /botflow/v1/devices       -> { devices: [{ id, name, osVersion, type }] }
//   POST /botflow/v1/install       -> { jobId }     (download ipa -> sign -> install -> launch)
//   GET  /botflow/v1/install/:id   -> { jobId, state, ... }
//
// v0: health + devices are real (devicectl); install is scaffolded and reports a
// clear "signing pipeline not wired yet" until the local sign/install lane lands.

import { createServer } from "node:http";
import { exec } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import path from "node:path";

const execAsync = promisify(exec);
const PORT = 17321;
const HOST = "127.0.0.1";
const APP_NAME = "Botflow Companion";

// In-memory install jobs (jobId -> { state, message, error, logs }).
const jobs = new Map();

function log(...args) {
  // eslint-disable-next-line no-console
  console.log(`[companion ${new Date().toISOString()}]`, ...args);
}

// ──────────────────────────────────────────────────────────────────────────────
// Device discovery (xcrun devicectl)
// ──────────────────────────────────────────────────────────────────────────────
function deviceType(platform, devType) {
  const p = (platform || "").toLowerCase();
  const t = (devType || "").toLowerCase();
  if (p === "ios" || t === "iphone") return "iphone";
  if (t === "ipad") return "ipad";
  if (p === "tvos" || t.includes("tv")) return "apple_tv";
  return "unknown";
}

async function listDevices() {
  const out = path.join(mkdtempSync(path.join(tmpdir(), "botflow-companion-")), "devs.json");
  await execAsync(`xcrun devicectl list devices --json-output "${out}"`, { timeout: 15000 });
  const { readFileSync } = await import("node:fs");
  const data = JSON.parse(readFileSync(out, "utf8"));
  const raw = data?.result?.devices ?? [];
  return raw
    .map((d) => {
      const dp = d.deviceProperties ?? {};
      const hp = d.hardwareProperties ?? {};
      const cp = d.connectionProperties ?? {};
      return {
        id: hp.udid,
        name: dp.name ?? "iPhone",
        osVersion: dp.osVersionNumber ?? "",
        type: deviceType(hp.platform, hp.deviceType),
        _paired: cp.pairingState === "paired",
        _tunnel: cp.tunnelState,
      };
    })
    // Only surface real, paired iOS-family devices the user can install onto.
    .filter((d) => d.id && d._paired && (d.type === "iphone" || d.type === "ipad"))
    .map(({ _paired, _tunnel, ...rest }) => rest);
}

// ──────────────────────────────────────────────────────────────────────────────
// Install pipeline (scaffold — sign/install lane lands next)
// ──────────────────────────────────────────────────────────────────────────────
async function startInstall({ deviceId, ipaUrl }) {
  const jobId = randomUUID();
  jobs.set(jobId, { jobId, state: "queued", message: "Queued", logs: [] });
  // Run async; the UI polls /install/:id.
  void runInstall(jobId, { deviceId, ipaUrl }).catch((e) => {
    const job = jobs.get(jobId);
    if (job) {
      job.state = "failed";
      job.error = e instanceof Error ? e.message : String(e);
    }
  });
  return jobId;
}

async function runInstall(jobId, { deviceId, ipaUrl }) {
  const job = jobs.get(jobId);
  if (!job) return;
  job.state = "running";
  job.message = "Preparing install…";
  log(`install job ${jobId}: device=${deviceId} ipa=${ipaUrl}`);

  // TODO(next): download ipaUrl -> code-sign for this device (Apple Development
  // identity + device-registered provisioning profile) -> `xcrun devicectl
  // device install app` -> `... process launch`. Requires an Apple ID/team set
  // up in Xcode (Settings > Accounts) so a signing identity exists.
  job.state = "failed";
  job.error =
    "Install pipeline not wired yet: the cloud IPA is unsigned and must be " +
    "code-signed locally for this device before install. Add your Apple ID in " +
    "Xcode > Settings > Accounts, then we'll enable signing + devicectl install.";
}

// ──────────────────────────────────────────────────────────────────────────────
// HTTP plumbing
// ──────────────────────────────────────────────────────────────────────────────
function setCors(req, res) {
  const origin = req.headers.origin ?? "*";
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Vary", "Origin");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "content-type, accept");
  // Chrome Private Network Access: HTTPS page -> http://127.0.0.1 needs this on
  // the preflight or the request is blocked even after the user allows it.
  res.setHeader("Access-Control-Allow-Private-Network", "true");
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json" });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve) => {
    let buf = "";
    req.on("data", (c) => (buf += c));
    req.on("end", () => {
      try {
        resolve(buf ? JSON.parse(buf) : {});
      } catch {
        resolve({});
      }
    });
  });
}

const server = createServer(async (req, res) => {
  setCors(req, res);
  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = new URL(req.url ?? "/", `http://${HOST}:${PORT}`);
  const p = url.pathname;

  try {
    if (req.method === "GET" && p === "/botflow/v1/health") {
      return sendJson(res, 200, { ok: true, app: APP_NAME, source: "mac-cli" });
    }

    if (req.method === "GET" && p === "/botflow/v1/devices") {
      const devices = await listDevices();
      return sendJson(res, 200, { devices });
    }

    if (req.method === "POST" && p === "/botflow/v1/install") {
      const body = await readBody(req);
      if (!body.deviceId) return sendJson(res, 400, { error: "deviceId required" });
      const jobId = await startInstall(body);
      return sendJson(res, 200, { jobId, state: "queued" });
    }

    const installMatch = /^\/botflow\/v1\/install\/([^/]+)$/.exec(p);
    if (req.method === "GET" && installMatch) {
      const job = jobs.get(decodeURIComponent(installMatch[1]));
      if (!job) return sendJson(res, 404, { error: "job not found" });
      return sendJson(res, 200, job);
    }

    return sendJson(res, 404, { error: "not found" });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    log("request error:", message);
    return sendJson(res, 500, { error: message });
  }
});

server.listen(PORT, HOST, () => {
  log(`${APP_NAME} listening on http://${HOST}:${PORT}`);
  log("endpoints: /botflow/v1/health, /botflow/v1/devices, /botflow/v1/install");
});
