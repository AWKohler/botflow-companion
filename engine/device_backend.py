"""Cross-platform iOS device operations for the Botflow Companion engine.

The engine talks to a physically/network connected iPhone through whatever the
host OS provides:

  • macOS   → Apple's ``xcrun devicectl`` (ships with the Xcode command-line
              tools). This is the proven path used by the shipping mac build.
  • Windows → ``libimobiledevice`` (``idevice_id``, ``ideviceinfo``,
              ``ideviceinstaller``, ``idevicedebug``) plus Apple's "Apple Mobile
              Device Support" USB driver (installed with iTunes). Bundled
              binaries are resolved next to the frozen engine first, then PATH.

Every function returns the SAME normalized shape regardless of backend so
``companion.py`` stays platform-agnostic.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

# Where bundled tools live (PyInstaller unpacks to sys._MEIPASS; from source it's
# a ``tools/`` dir next to this file). Windows ships libimobiledevice here.
_BUNDLE = Path(getattr(sys, "_MEIPASS", str(Path(__file__).parent)))
_TOOLS = _BUNDLE / "tools"

# Extra directories to search for libimobiledevice on Windows when the tools
# aren't bundled. Running them from their install dir lets Windows load their
# co-located DLLs without polluting PATH. Override with BOTFLOW_IMOBILE_DIR.
_WIN_TOOL_DIRS = []
if IS_WIN:
    _env_dir = os.environ.get("BOTFLOW_IMOBILE_DIR")
    if _env_dir:
        _WIN_TOOL_DIRS.append(Path(_env_dir))
    _WIN_TOOL_DIRS += [
        Path(r"C:\msys64\mingw64\bin"),
        Path(r"C:\msys64\ucrt64\bin"),
        Path(r"C:\Program Files\libimobiledevice"),
        Path(r"C:\libimobiledevice"),
    ]


class ToolMissing(Exception):
    """A required CLI (devicectl / libimobiledevice) isn't installed."""


def _tool(name):
    """Resolve a CLI: bundled tools dir, then known install dirs, then PATH.

    Returns the full path (so the OS loads the tool's co-located DLLs) or None.
    """
    exe = name + (".exe" if IS_WIN else "")
    cand = _TOOLS / exe
    if cand.exists():
        return str(cand)
    for d in _WIN_TOOL_DIRS:
        c = d / exe
        if c.exists():
            return str(c)
    return shutil.which(name) or shutil.which(exe)


# Suppress the console window each child process would otherwise flash on
# Windows (the engine runs windowless under pythonw / the tray app).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd, timeout=120):
    # Force UTF-8 decoding: libimobiledevice emits UTF-8, but Windows' default
    # locale (cp1252) would mangle non-ASCII device names (e.g. the ’ in a name).
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=_NO_WINDOW)
    except FileNotFoundError as e:
        raise ToolMissing(cmd[0]) from e


# ──────────────────────────────────────────────────────────────────────────────
# Readiness — what the UI/health needs to tell the user how to get set up.
# ──────────────────────────────────────────────────────────────────────────────
def readiness():
    """Return {ok, backend, missing:[...], hint}. Never raises."""
    if IS_MAC:
        ok = subprocess.run(["xcode-select", "-p"],
                            capture_output=True, text=True).returncode == 0 \
            if shutil.which("xcode-select") else False
        return {
            "ok": ok,
            "backend": "devicectl",
            "missing": [] if ok else ["xcode-clt"],
            "hint": "" if ok else "Install Xcode command-line tools: xcode-select --install",
        }
    if IS_WIN:
        missing = [t for t in ("idevice_id", "ideviceinstaller") if not _tool(t)]
        # Apple Mobile Device Support driver presence (best-effort).
        amds = Path(r"C:\Program Files\Common Files\Apple\Mobile Device Support").exists()
        if not amds:
            missing.append("apple-mobile-device-support")
        hint = ""
        if "apple-mobile-device-support" in missing:
            hint = "Install Apple Mobile Device Support (comes with iTunes) so Windows can see your iPhone over USB."
        elif missing:
            hint = "libimobiledevice tools are missing from this build."
        return {"ok": not missing, "backend": "libimobiledevice",
                "missing": missing, "hint": hint}
    return {"ok": False, "backend": "none", "missing": ["unsupported-os"],
            "hint": "This OS isn't supported yet."}


# ──────────────────────────────────────────────────────────────────────────────
# Device listing
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


def _list_devices_mac():
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
        # transportType is the live presence signal: "wired" (USB now) or
        # "localNetwork" (reachable wirelessly — OTA). tunnelState reads
        # "disconnected" for both until an op opens a tunnel, so don't gate on it.
        transport = cp.get("transportType")
        reachable = (cp.get("tunnelState") in ("connected", "connecting"))
        if transport not in ("wired", "localNetwork") and not reachable:
            continue
        devices.append({
            "id": udid,
            "name": dp.get("name") or "iPhone",
            "osVersion": dp.get("osVersionNumber") or "",
            "type": t,
            "connected": True,
            "developerMode": dp.get("developerModeStatus") or "unknown",
            "ddiReady": bool(dp.get("ddiServicesAvailable")),
            "transport": transport or "",
        })
    return devices


def _ideviceinfo(udid, key, domain=None):
    cmd = [_tool("ideviceinfo"), "-u", udid]
    if domain:
        cmd += ["-q", domain]
    cmd += ["-k", key]
    r = _run(cmd, timeout=15)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def _list_devices_win():
    idid = _tool("idevice_id")
    if not idid:
        return []
    # `idevice_id -l` prints one UDID per line for USB; `-n` adds network devices.
    r = _run([idid, "-l"], timeout=15)
    udids = [u.strip() for u in (r.stdout or "").splitlines() if u.strip()]
    # network-paired devices (OTA)
    rn = _run([idid, "-n"], timeout=15)
    net = {u.strip() for u in (rn.stdout or "").splitlines() if u.strip()}
    udids = list(dict.fromkeys(udids + list(net)))  # de-dup, preserve order
    devices = []
    for udid in udids:
        name = _ideviceinfo(udid, "DeviceName") or "iPhone"
        ver = _ideviceinfo(udid, "ProductVersion")
        ptype = _ideviceinfo(udid, "ProductType")  # e.g. iPhone15,2
        t = "ipad" if ptype.lower().startswith("ipad") else "iphone"
        # Developer Mode (iOS 16+): exposed via the amfi domain on newer libimobiledevice.
        dev_mode = _ideviceinfo(
            udid, "DeveloperModeStatus",
            domain="com.apple.security.mac.amfi") or "unknown"
        dev_mode = {"1": "enabled", "true": "enabled", "0": "disabled",
                    "false": "disabled"}.get(dev_mode.lower(), dev_mode or "unknown")
        devices.append({
            "id": udid,
            "name": name,
            "osVersion": ver,
            "type": t,
            "connected": True,
            "developerMode": dev_mode,
            "ddiReady": True,  # libimobiledevice mounts DDI on demand
            "transport": "localNetwork" if udid in net else "wired",
        })
    return devices


def list_devices():
    """Normalized device list. Returns [] (never raises) if tools are missing."""
    try:
        if IS_MAC:
            return _list_devices_mac()
        if IS_WIN:
            return _list_devices_win()
    except ToolMissing:
        return []
    except Exception:
        return []
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Install / uninstall / launch / list-apps — return CompletedProcess-like objects
# (.returncode/.stdout/.stderr) so companion.py's orchestration is unchanged.
# ──────────────────────────────────────────────────────────────────────────────
def install(udid, ipa_path, timeout=300):
    if IS_MAC:
        return _run(["xcrun", "devicectl", "device", "install", "app",
                     "--device", udid, ipa_path], timeout=timeout)
    if IS_WIN:
        return _run([_tool("ideviceinstaller"), "-u", udid, "-i", ipa_path],
                    timeout=timeout)
    raise ToolMissing("device-backend")


def uninstall(udid, bundle_id, timeout=60):
    if IS_MAC:
        return _run(["xcrun", "devicectl", "device", "uninstall", "app",
                     "--device", udid, bundle_id], timeout=timeout)
    if IS_WIN:
        return _run([_tool("ideviceinstaller"), "-u", udid, "-U", bundle_id],
                    timeout=timeout)
    raise ToolMissing("device-backend")


def launch(udid, bundle_id, timeout=60):
    if IS_MAC:
        return _run(["xcrun", "devicectl", "device", "process", "launch",
                     "--device", udid, bundle_id], timeout=timeout)
    if IS_WIN:
        # idevicedebug needs the DDI mounted + Developer Mode on.
        return _run([_tool("idevicedebug"), "-u", udid, "run", bundle_id],
                    timeout=timeout)
    raise ToolMissing("device-backend")


def enable_devmode(udid):
    """Trigger Developer Mode on the device. The user still confirms on-device +
    reboots (Apple requirement). Returns (ok: bool, message: str).

    Uses pymobiledevice3 (pure-Python, talks to usbmux/AMDS) — this is what makes
    the Developer Mode toggle appear in Settings AND arms it. libimobiledevice's
    older builds ship no devmode tool, so this is the cross-platform path.
    """
    if IS_MAC:
        # On macOS the toggle is reached through Settings after a dev connection.
        _run(["xcrun", "devicectl", "device", "info", "details", "--device", udid], timeout=30)
        return (True, "On your iPhone: Settings → Privacy & Security → Developer Mode → On, then reboot.")
    try:
        import asyncio
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.amfi import AmfiService
    except Exception:
        return (False, "Developer Mode requires the pymobiledevice3 component (missing from this build).")

    # pymobiledevice3 4.x is async — create_using_usbmux and the amfi methods are
    # coroutines and MUST be awaited (calling them synchronously silently returns
    # an un-run coroutine, which is why the reveal did nothing). Run in an event
    # loop on this handler thread. REVEAL is the reliable action (what Xcode/
    # 3uTools send) — it makes the Developer Mode row appear in Settings. We don't
    # auto-call enable_developer_mode() (it force-reboots immediately); the user
    # toggles it from Settings (a passcode-confirmed reboot they expect).
    async def _reveal():
        lockdown = await create_using_usbmux(serial=udid)
        amfi = AmfiService(lockdown)
        await amfi.reveal_developer_mode_option_in_ui()

    try:
        asyncio.run(_reveal())
        return (True, "Developer Mode now appears on your device under "
                      "Settings → Privacy & Security → Developer Mode. Turn it on there — "
                      "the device will restart to confirm.")
    except Exception as e:
        msg = str(e)
        if "passcode" in msg.lower() or "locked" in msg.lower():
            return (False, "Unlock your device, then click Enable Developer Mode again.")
        return (False, msg or "Could not reveal Developer Mode. Make sure the device is unlocked.")


def installed_apps(udid):
    """Return [{bundleIdentifier, name}] for user apps. [] if unavailable."""
    try:
        if IS_MAC:
            out = Path(tempfile.mkdtemp(prefix="botflow-apps-")) / "apps.json"
            _run(["xcrun", "devicectl", "device", "info", "apps",
                  "--device", udid, "--json-output", str(out)], timeout=60)
            if not out.exists():
                return []
            data = json.loads(out.read_text())
            return [{"bundleIdentifier": a.get("bundleIdentifier", ""),
                     "name": a.get("name") or a.get("bundleName") or ""}
                    for a in data.get("result", {}).get("apps", [])]
        if IS_WIN:
            r = _run([_tool("ideviceinstaller"), "-u", udid, "list",
                      "-o", "list_user", "-o", "json"], timeout=60)
            try:
                data = json.loads(r.stdout or "[]")
                return [{"bundleIdentifier": a.get("CFBundleIdentifier", ""),
                         "name": a.get("CFBundleDisplayName")
                                 or a.get("CFBundleName") or ""}
                        for a in data]
            except Exception:
                return []
    except ToolMissing:
        return []
    except Exception:
        return []
    return []
