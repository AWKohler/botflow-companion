#!/usr/bin/env python3
"""
Botflow Companion engine.

Local daemon on 127.0.0.1:17321 that bridges Botflow (browser) to a
USB-connected iPhone. It owns the things that must happen on the user's own
machine: Apple ID auth (no Xcode), code-signing for the device, and install via
`devicectl`. The browser drives it through the contract in the web UI
(iphone-device-runner.tsx); the native SwiftUI app drives Apple ID login.

macOS-first; cross-platform in mind:
  - signing core = vendored apple_account.py (Apple-ID SRP + native AOSKit
    anisette + free cert/profile + re-sign). Windows later: swap anisette to a
    server and install via libimobiledevice instead of devicectl.

Endpoints:
  GET  /botflow/v1/health
  GET  /botflow/v1/devices
  GET  /botflow/v1/auth/status
  POST /botflow/v1/auth/login     { appleId, password }   -> { ok } | { needs2fa, type }
  POST /botflow/v1/auth/2fa       { code }                 -> { ok, team }
  POST /botflow/v1/auth/logout
  POST /botflow/v1/install        { deviceId, ipaUrl? , ipaPath? } -> { jobId }
  GET  /botflow/v1/install/:jobId
"""

import json
import os
import sys
import threading
import traceback
import subprocess
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Vendored signer (apple_account.py + native anisette helper live next to us).
sys.path.insert(0, str(Path(__file__).parent / "signer"))
import apple_account  # noqa: E402  (AppleSigner, DeveloperPortal, ...)

HOST = "127.0.0.1"
PORT = 17321
APP_NAME = "Botflow Companion"

# ──────────────────────────────────────────────────────────────────────────────
# Session + job state (in-memory; re-login on restart for now)
# ──────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
SESSION = {
    "signer": None,      # logged-in AppleSigner
    "appleId": None,
    "team": None,
    "accountType": None,  # "free" | "paid" — drives the free-vs-paid guidance UI
    # transient, only during the 2FA window:
    "_pending_signer": None,
    "_pending_password": None,
    "_pending_2fa_type": None,
}
JOBS = {}  # jobId -> { jobId, state, message, error, logs[] }

# Lifecycle events the native app polls (GET /events) to raise macOS
# notifications + show an activity log. kind ∈ info|progress|success|warning|error.
EVENTS = []
_event_seq = 0


def log(*a):
    print(f"[companion {time.strftime('%H:%M:%S')}]", *a, flush=True)


def emit_event(kind, title, message, job_id=None):
    """Append a lifecycle event for the native app to surface."""
    global _event_seq
    with _lock:
        _event_seq += 1
        EVENTS.append({
            "seq": _event_seq, "at": int(time.time() * 1000),
            "kind": kind, "title": title, "message": message, "jobId": job_id,
        })
        if len(EVENTS) > 200:
            del EVENTS[: len(EVENTS) - 200]
    log(f"event[{kind}] {title}: {message}")


# ──────────────────────────────────────────────────────────────────────────────
# Devices (xcrun devicectl)
# ──────────────────────────────────────────────────────────────────────────────
def _device_type(platform_, dev_type):
    p = (platform_ or "").lower()
    t = (dev_type or "").lower()
    if p == "ios" or t == "iphone":
        return "iphone"
    if t == "ipad":
        return "ipad"
    if p == "tvos" or "tv" in t:
        return "apple_tv"
    return "unknown"


def list_devices():
    out = Path(tempfile.mkdtemp(prefix="botflow-companion-")) / "devs.json"
    subprocess.run(
        ["xcrun", "devicectl", "list", "devices", "--json-output", str(out)],
        capture_output=True, timeout=20, check=False,
    )
    if not out.exists():
        return []
    data = json.loads(out.read_text())
    devices = []
    for d in data.get("result", {}).get("devices", []):
        dp = d.get("deviceProperties", {})
        hp = d.get("hardwareProperties", {})
        cp = d.get("connectionProperties", {})
        udid = hp.get("udid")
        if not udid or cp.get("pairingState") != "paired":
            continue
        t = _device_type(hp.get("platform"), hp.get("deviceType"))
        if t not in ("iphone", "ipad"):
            continue
        # `devicectl` lists every PAIRED device forever, even ones unplugged for
        # days — pairing persists. The live signal is connectionProperties
        # .tunnelState ("connected"/"connecting" when actually reachable, else
        # "disconnected"/"unavailable"). Only surface reachable devices so the
        # dropdown doesn't show a phone that's been unplugged for hours.
        if cp.get("tunnelState") not in ("connected", "connecting"):
            continue
        # developerModeStatus ∈ enabled | disabled | restricted | <unknown>.
        # ddiServicesAvailable = the Developer Disk Image is mounted (needed to
        # actually launch/debug). Both come straight from devicectl.
        devices.append({
            "id": udid,
            "name": dp.get("name") or "iPhone",
            "osVersion": dp.get("osVersionNumber") or "",
            "type": t,
            "connected": True,
            "developerMode": dp.get("developerModeStatus") or "unknown",
            "ddiReady": bool(dp.get("ddiServicesAvailable")),
            "transport": cp.get("transportType") or "",
        })
    return devices


def xcode_present():
    r = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


# ──────────────────────────────────────────────────────────────────────────────
# Apple ID auth (drives the vendored AppleSigner; 2FA over two requests)
# ──────────────────────────────────────────────────────────────────────────────
def _extract_team(signer):
    """Best-effort (team name, account type) after login.

    Apple's listTeams returns each team with a `type`: a FREE Apple ID has a
    personal team of type "Free" (7-day signing, 3-app limit, no push/widgets/
    etc.); a PAID membership is "Company" or "Individual". We use that to drive
    the free-vs-paid guidance in the UI.
    """
    try:
        signer.portal.select_team()
    except Exception:
        pass
    team_id = getattr(signer.portal, "team_id", None)
    name, account_type = None, None
    try:
        teams = signer.portal.list_teams()
        team = next((t for t in teams if t.get("teamId") == team_id), None)
        if team is None and teams:
            team = teams[0]
        if team:
            name = team.get("name") or team.get("teamId")
            account_type = "free" if (team.get("type") or "").lower() == "free" else "paid"
    except Exception:
        # Fall back to whatever the portal exposes for a name.
        for attr in ("team", "team_id", "selected_team"):
            v = getattr(signer.portal, attr, None)
            if isinstance(v, dict):
                name = v.get("name") or v.get("teamId"); break
            if isinstance(v, str):
                name = v; break
    return name, account_type


def _finalize_login(signer, apple_id):
    signer.auth.get_xcode_token()
    signer.portal = apple_account.DeveloperPortal(signer.auth)
    team, account_type = _extract_team(signer)
    with _lock:
        SESSION["signer"] = signer
        SESSION["appleId"] = apple_id
        SESSION["team"] = team
        SESSION["accountType"] = account_type
        SESSION["_pending_signer"] = None
        SESSION["_pending_password"] = None
        SESSION["_pending_2fa_type"] = None
    log(f"logged in as {apple_id} (team={team}, account={account_type})")
    emit_event("success", "Signed in to Apple", f"{apple_id}" + (f" · team {team}" if team else ""))
    return team


def auth_login(apple_id, password):
    signer = apple_account.AppleSigner()
    result = signer.auth.authenticate(apple_id, password)
    status = result["status"]
    if signer.auth.needs_2fa(status):
        fa_type = signer.auth.get_2fa_type(status)
        if fa_type == "trusted_device":
            signer.auth.send_2fa_trusted_device()
        else:
            signer.auth.send_2fa_sms()
        with _lock:
            SESSION["_pending_signer"] = signer
            SESSION["_pending_password"] = password
            SESSION["_pending_2fa_type"] = fa_type
            SESSION["appleId"] = apple_id
        return {"needs2fa": True, "type": fa_type}
    team = _finalize_login(signer, apple_id)
    return {"ok": True, "team": team}


def auth_2fa(code):
    with _lock:
        signer = SESSION["_pending_signer"]
        password = SESSION["_pending_password"]
        apple_id = SESSION["appleId"]
        fa_type = SESSION["_pending_2fa_type"]
    if not signer:
        return {"error": "no pending login"}
    ok = (signer.auth.submit_2fa_code(code) if fa_type == "trusted_device"
          else signer.auth.submit_2fa_sms_code(code))
    if not ok:
        return {"error": "invalid 2FA code"}
    # Re-auth after 2FA (Apple requires it), then finalize.
    signer.auth.authenticate(apple_id, password)
    team = _finalize_login(signer, apple_id)
    return {"ok": True, "team": team}


# ──────────────────────────────────────────────────────────────────────────────
# Install pipeline (sign via signer, install via devicectl)
# ──────────────────────────────────────────────────────────────────────────────
def _set_job(job_id, **kw):
    with _lock:
        job = JOBS.setdefault(job_id, {"jobId": job_id, "logs": []})
        job.update(kw)


def _job_log(job_id, line):
    with _lock:
        JOBS.setdefault(job_id, {"jobId": job_id, "logs": []})["logs"].append(
            {"line": line, "at": int(time.time() * 1000)}
        )
    log(f"job {job_id[:8]}: {line}")


def _download(url, dest):
    import requests
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def run_install(job_id, device_id, ipa_url, ipa_path):
    try:
        emit_event("info", "Run on iPhone", "Starting install…", job_id)
        signer = SESSION["signer"]
        if not signer:
            _set_job(job_id, state="failed", error="Not signed in to Apple ID.")
            emit_event("error", "Not signed in", "Sign in to Apple in Botflow Companion first.", job_id)
            return

        work = Path(tempfile.mkdtemp(prefix="botflow-install-"))
        if ipa_url:
            _set_job(job_id, state="running", message="Downloading build…")
            _job_log(job_id, f"downloading {ipa_url}")
            emit_event("progress", "Downloading build", "Fetching the build from Botflow…", job_id)
            ipa_path = str(work / "app.ipa")
            _download(ipa_url, ipa_path)
        if not ipa_path or not Path(ipa_path).exists():
            _set_job(job_id, state="failed", error="No IPA to install.")
            emit_event("error", "Install failed", "No build to install.", job_id)
            return

        _set_job(job_id, state="running", message="Signing for your device…")
        _job_log(job_id, "signing (provision + re-sign) with Apple ID")
        emit_event("progress", "Signing build", "Signing the app for your iPhone…", job_id)
        signed = str(work / "signed.ipa")
        signer.sign_ipa(ipa_path, output_path=signed, udid=device_id)
        _job_log(job_id, f"signed: {signed}")

        _set_job(job_id, state="running", message="Installing on device…")
        emit_event("progress", "Installing", "Installing on your iPhone…", job_id)
        r = subprocess.run(
            ["xcrun", "devicectl", "device", "install", "app",
             "--device", device_id, signed],
            capture_output=True, text=True, timeout=300,
        )
        _job_log(job_id, (r.stdout or "").strip()[-500:])
        if r.returncode != 0:
            err = f"devicectl install failed: {(r.stderr or r.stdout).strip()[-400:]}"
            _set_job(job_id, state="failed", error=err)
            emit_event("error", "Install failed", err[-180:], job_id)
            return

        # Try to launch; first launch needs the user to Trust the dev cert.
        bundle_id = _bundle_id_of(signed)
        launched = False
        if bundle_id:
            lr = subprocess.run(
                ["xcrun", "devicectl", "device", "process", "launch",
                 "--device", device_id, bundle_id],
                capture_output=True, text=True, timeout=60,
            )
            launched = lr.returncode == 0 and "RequestDenied" not in (lr.stderr or "")
            if not launched and ("not been explicitly trusted" in (lr.stderr or "") or "RequestDenied" in (lr.stderr or "")):
                _set_job(
                    job_id, state="succeeded",
                    message="Installed. Trust the developer on your iPhone to launch: "
                            "Settings → General → VPN & Device Management.",
                    needsTrust=True,
                )
                emit_event("warning", "Installed — trust needed",
                           "On your iPhone: Settings → General → VPN & Device Management → Trust, then open the app.",
                           job_id)
                return
        _set_job(job_id, state="succeeded",
                 message="Installed" + (" and launched." if launched else "."))
        emit_event("success", "Installed on iPhone",
                   "Your app is on your iPhone" + (" and launched." if launched else "."), job_id)
    except Exception as e:
        traceback.print_exc()
        _set_job(job_id, state="failed", error=str(e))
        emit_event("error", "Install failed", str(e)[:180], job_id)


def _bundle_id_of(ipa_path):
    try:
        signer = SESSION["signer"]
        return signer._get_bundle_id_from_ipa(ipa_path)
    except Exception:
        return None


def start_install(device_id, ipa_url, ipa_path):
    job_id = str(uuid.uuid4())
    _set_job(job_id, state="queued", message="Queued")
    threading.Thread(
        target=run_install, args=(job_id, device_id, ipa_url, ipa_path), daemon=True
    ).start()
    return job_id


# ──────────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet default logging
        pass

    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, accept")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        n = int(self.headers.get("content-length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == "/botflow/v1/health":
                return self._json(200, {
                    "ok": True, "app": APP_NAME, "source": "mac-engine",
                    "xcode": xcode_present(),
                    "loggedIn": SESSION["signer"] is not None,
                    "appleId": SESSION["appleId"], "team": SESSION["team"],
                    "accountType": SESSION["accountType"],
                })
            if p == "/botflow/v1/devices":
                return self._json(200, {"devices": list_devices()})
            if p == "/botflow/v1/events":
                since = int((parse_qs(urlparse(self.path).query).get("since", ["0"])[0]) or 0)
                with _lock:
                    evs = [e for e in EVENTS if e["seq"] > since]
                    cursor = _event_seq
                return self._json(200, {"events": evs, "cursor": cursor})
            if p == "/botflow/v1/auth/status":
                return self._json(200, {
                    "loggedIn": SESSION["signer"] is not None,
                    "appleId": SESSION["appleId"], "team": SESSION["team"],
                    "accountType": SESSION["accountType"],
                    "pending2fa": SESSION["_pending_signer"] is not None,
                })
            m = p.rsplit("/", 1)
            if len(m) == 2 and m[0] == "/botflow/v1/install":
                job = JOBS.get(m[1])
                return self._json(200, job) if job else self._json(404, {"error": "job not found"})
            return self._json(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": str(e)})

    def do_POST(self):
        p = urlparse(self.path).path
        body = self._body()
        try:
            if p == "/botflow/v1/auth/login":
                if not body.get("appleId") or not body.get("password"):
                    return self._json(400, {"error": "appleId and password required"})
                return self._json(200, auth_login(body["appleId"], body["password"]))
            if p == "/botflow/v1/auth/2fa":
                if not body.get("code"):
                    return self._json(400, {"error": "code required"})
                res = auth_2fa(str(body["code"]).strip())
                return self._json(200 if res.get("ok") else 400, res)
            if p == "/botflow/v1/auth/logout":
                with _lock:
                    for k in list(SESSION):
                        SESSION[k] = None
                return self._json(200, {"ok": True})
            if p == "/botflow/v1/install":
                if not body.get("deviceId"):
                    return self._json(400, {"error": "deviceId required"})
                if SESSION["signer"] is None:
                    return self._json(401, {"error": "Sign in to your Apple ID in Botflow Companion first."})
                job_id = start_install(body["deviceId"], body.get("ipaUrl"), body.get("ipaPath"))
                return self._json(200, {"jobId": job_id, "state": "queued"})
            return self._json(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": str(e)})


def main():
    # From source, run in signer/ (legacy relative-path assumptions). When frozen
    # by PyInstaller there is no signer/ dir and the helper resolves via an
    # absolute path (sys._MEIPASS), so skip the chdir.
    signer_dir = Path(__file__).parent / "signer"
    if signer_dir.is_dir():
        os.chdir(signer_dir)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"{APP_NAME} engine on http://{HOST}:{PORT}")
    log("health/devices ready; auth+install live (sign-in required for install)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
