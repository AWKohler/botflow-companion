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

import base64
import json
import os
import platform
import plistlib
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
import device_backend as dev  # noqa: E402  (cross-platform device ops)
import ui  # noqa: E402  (the companion window HTML)

HOST = "127.0.0.1"
PORT = 17321
APP_NAME = "Botflow Companion"

# Persist the Apple auth session so the user doesn't have to re-sign-in on every
# daemon/app restart. Tokens still expire on Apple's side; restore is best-effort
# and falls back to a normal login if they're stale.
_SESSION_PATH = (Path.home() / "Library" / "Application Support"
                 / "BotflowCompanion" / "session.json")

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
# Device enumeration is delegated to the platform backend (devicectl on macOS,
# libimobiledevice on Windows) so this module stays OS-agnostic.
def list_devices():
    return dev.list_devices()


def xcode_present():
    """Back-compat: whether the device backend's tooling is ready."""
    return dev.readiness().get("ok", False)


# ──────────────────────────────────────────────────────────────────────────────
# Apple ID auth (drives the vendored AppleSigner; 2FA over two requests)
# ──────────────────────────────────────────────────────────────────────────────
def _is_free_team(team):
    """Free (Xcode personal-team) vs paid Developer Program.

    The team `type` is "Individual" for BOTH a free personal team and a paid
    individual membership, so it can't distinguish them. The reliable markers
    are the membership product and the member role:
      • a free team's membership is the "Xcode Free Provisioning Program"
        (membershipProductId == "fp22"),
      • and currentTeamMember.roles contains "XCODE_FREE_USER".
    A paid membership has a different product id and admin/agent roles.
    """
    roles = ((team.get("currentTeamMember") or {}).get("roles")) or []
    if any("FREE" in str(r).upper() for r in roles):
        return True
    for m in (team.get("memberships") or []):
        pid = (m.get("membershipProductId") or "").lower()
        nm = (m.get("name") or "").lower()
        if pid == "fp22" or "free provisioning" in nm:
            return True
    # If there's an explicit non-free membership, treat as paid; otherwise,
    # absent any membership info, default to free (safer — assumes limits apply).
    return not (team.get("memberships"))


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
            account_type = "free" if _is_free_team(team) else "paid"
    except Exception:
        # Fall back to whatever the portal exposes for a name.
        for attr in ("team", "team_id", "selected_team"):
            v = getattr(signer.portal, attr, None)
            if isinstance(v, dict):
                name = v.get("name") or v.get("teamId"); break
            if isinstance(v, str):
                name = v; break
    return name, account_type


def _b64(b):
    return base64.b64encode(b).decode() if isinstance(b, (bytes, bytearray)) else b


def _save_session(signer, apple_id, team, account_type):
    """Persist the auth tokens so we can restore the session after a restart."""
    try:
        a = signer.auth
        _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_PATH.write_text(json.dumps({
            "appleId": apple_id, "team": team, "accountType": account_type,
            "adsid": a.adsid, "idms_token": a.idms_token,
            "session_key": _b64(a.session_key), "cookie": _b64(a.cookie),
        }))
        os.chmod(_SESSION_PATH, 0o600)
    except Exception as e:
        log(f"session save failed (non-fatal): {e}")


def _restore_session():
    """Best-effort: rebuild a logged-in signer from the saved tokens."""
    if not _SESSION_PATH.exists():
        return
    try:
        d = json.loads(_SESSION_PATH.read_text())
        signer = apple_account.AppleSigner()
        auth = signer.auth
        auth.adsid = d.get("adsid")
        auth.idms_token = d.get("idms_token")
        sk = d.get("session_key"); ck = d.get("cookie")
        auth.session_key = base64.b64decode(sk) if sk else None
        auth.cookie = base64.b64decode(ck) if ck else None
        if not (auth.adsid and auth.idms_token and auth.session_key and auth.cookie):
            return
        auth.get_xcode_token()  # validates the tokens are still good
        signer.portal = apple_account.DeveloperPortal(auth)
        team, account_type = _extract_team(signer)
        with _lock:
            SESSION["signer"] = signer
            SESSION["appleId"] = d.get("appleId")
            SESSION["team"] = team or d.get("team")
            SESSION["accountType"] = account_type or d.get("accountType")
        # Heal a stale/incorrect accountType saved by an older build.
        if account_type and account_type != d.get("accountType"):
            try:
                _save_session(signer, d.get("appleId"),
                              SESSION["team"], account_type)
            except Exception:
                pass
        log(f"restored Apple session for {d.get('appleId')} (account={SESSION['accountType']})")
    except Exception as e:
        log(f"session restore failed (will need re-login): {e}")


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
    _save_session(signer, apple_id, team, account_type)
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


def _installed_apps(device_id):
    """Return [{bundleIdentifier, name}] for apps installed on the device."""
    return dev.installed_apps(device_id)


def _uninstall_app(device_id, bundle_id):
    try:
        return dev.uninstall(device_id, bundle_id).returncode == 0
    except Exception:
        return False


# Apple's free-developer-profile install limit (max 3 sideloaded apps).
_FREE_LIMIT_MARKERS = (
    "maximum number of installed apps using a free developer profile",
    "ApplicationVerificationFailed",
)


def _is_free_limit_error(text):
    t = (text or "")
    return any(m in t for m in _FREE_LIMIT_MARKERS)


def _prunable_botflow_apps(device_id, keep_bundle_id=None):
    """Candidate apps we may remove ONLY when at the free-install limit: our own
    com.botflow.* dev installs (never the user's other apps), excluding the one
    we're installing. The caller removes these one at a time, retrying after
    each, so we free the MINIMUM number of slots needed — not all of them."""
    out = []
    for a in _installed_apps(device_id):
        bid = a.get("bundleIdentifier", "") or ""
        if bid and bid != keep_bundle_id and "com.botflow" in bid.lower():
            out.append(bid)
    return out


def _account_type_from_ipa(signed_ipa):
    """Authoritative free-vs-paid: read the embedded profile from the freshly
    signed IPA. Personal-team (free) profiles are `LocalProvision` and expire in
    ~7 days; paid profiles last ~1 year. Returns "free" | "paid" | None."""
    try:
        import zipfile
        with zipfile.ZipFile(signed_ipa, "r") as z:
            name = next((n for n in z.namelist()
                         if n.endswith(".app/embedded.mobileprovision")), None)
            if not name:
                return None
            raw = z.read(name)
        start = raw.index(b"<?xml")
        end = raw.index(b"</plist>") + len(b"</plist>")
        mp = plistlib.loads(raw[start:end])
        if mp.get("LocalProvision"):
            return "free"
        created = mp.get("CreationDate")
        expires = mp.get("ExpirationDate")
        if created and expires:
            days = (expires - created).days
            return "free" if days <= 31 else "paid"
    except Exception:
        pass
    return None


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

        # The embedded profile tells us authoritatively whether this is a free
        # (personal-team, 7-day, 3-app-limit) or paid account — more reliable
        # than listTeams. Correct the badge if it disagrees.
        detected = _account_type_from_ipa(signed)
        if detected and detected != SESSION.get("accountType"):
            SESSION["accountType"] = detected
            try:
                _save_session(signer, SESSION.get("appleId"),
                              SESSION.get("team"), detected)
            except Exception:
                pass
            _job_log(job_id, f"account type detected from profile: {detected}")

        bundle_id = _bundle_id_of(signed)

        def _do_install():
            return dev.install(device_id, signed)

        _set_job(job_id, state="running", message="Installing on device…")
        emit_event("progress", "Installing", "Installing on your iPhone…", job_id)
        r = _do_install()
        _job_log(job_id, (r.stdout or "").strip()[-500:])

        # Free accounts cap sideloaded apps at 3. ONLY when we actually hit that
        # limit do we free space — and then we remove our OWN com.botflow.* builds
        # ONE AT A TIME, retrying after each, so we free the minimum number of
        # slots needed rather than wiping every Botflow app on the device.
        if r.returncode != 0 and _is_free_limit_error((r.stderr or "") + (r.stdout or "")):
            candidates = _prunable_botflow_apps(device_id, keep_bundle_id=bundle_id)
            for bid in candidates:
                if not (r.returncode != 0 and _is_free_limit_error((r.stderr or "") + (r.stdout or ""))):
                    break  # we're under the limit now — stop removing.
                emit_event("progress", "Freeing space",
                           "Free Apple account is at its 3-app limit — removing one old Botflow build…", job_id)
                if _uninstall_app(device_id, bid):
                    _job_log(job_id, f"removed one botflow app to free a slot: {bid}")
                    r = _do_install()
                    _job_log(job_id, (r.stdout or "").strip()[-500:])
            if r.returncode != 0 and _is_free_limit_error((r.stderr or "") + (r.stdout or "")):
                msg = ("Your iPhone has reached the free Apple account limit of 3 "
                       "installed apps. Delete an app you installed via Botflow/Xcode "
                       "from your Home Screen, then try again.")
                _set_job(job_id, state="failed", error=msg, freeLimit=True)
                emit_event("error", "iPhone app limit reached", msg, job_id)
                return

        if r.returncode != 0:
            err = f"devicectl install failed: {(r.stderr or r.stdout).strip()[-400:]}"
            _set_job(job_id, state="failed", error=err)
            emit_event("error", "Install failed", err[-180:], job_id)
            return

        # Try to launch; first launch needs the user to Trust the dev cert.
        launched = False
        if bundle_id:
            lr = dev.launch(device_id, bundle_id)
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

    def _html(self, body):
        payload = body.encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            # The companion window UI (rendered by pywebview; also usable in a browser).
            if p == "/" or p == "/ui":
                return self._html(ui.get_html())
            if p == "/botflow/v1/health":
                rd = dev.readiness()
                return self._json(200, {
                    "ok": True, "app": APP_NAME,
                    "source": ("mac-engine" if dev.IS_MAC
                               else "win-engine" if dev.IS_WIN else "engine"),
                    "platform": platform.system().lower(),
                    "xcode": rd.get("ok", False),     # back-compat: tooling ready?
                    "toolingReady": rd.get("ok", False),
                    "backend": rd.get("backend"),
                    "missing": rd.get("missing", []),
                    "setupHint": rd.get("hint", ""),
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
                try:
                    _SESSION_PATH.unlink(missing_ok=True)
                except Exception:
                    pass
                return self._json(200, {"ok": True})
            if p == "/botflow/v1/devmode/enable":
                if not body.get("deviceId"):
                    return self._json(400, {"error": "deviceId required"})
                ok, msg = dev.enable_devmode(body["deviceId"])
                return self._json(200 if ok else 500, {"ok": ok, "message": msg, "error": None if ok else msg})
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
    _restore_session()  # bring back a prior login if the tokens are still valid
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"{APP_NAME} engine on http://{HOST}:{PORT}")
    log("health/devices ready; auth+install live (sign-in required for install)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
