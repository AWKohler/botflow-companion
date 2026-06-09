#!/usr/bin/env python3
"""Botflow Companion — Windows tray application (single multi-mode binary).

The classic pystray + pywebview conflict (both want the main thread) is avoided
by making each concern its OWN process. One frozen exe, three modes:

  app                 → TRAY  : system-tray icon + menu; spawns + supervises the
                               engine; opens the window on demand.
  app --engine        → ENGINE: the local HTTP daemon (companion.main()).
  app --window        → WINDOW: a pywebview window rendering the engine's UI.

This mirrors the macOS split (SwiftUI app + engine over the loopback API).
"""
import os
import sys
import subprocess
import threading
import time
import urllib.request

HEALTH = "http://127.0.0.1:17321/botflow/v1/health"
UI_URL = "http://127.0.0.1:17321/"


def _setup_logging(name):
    """Always redirect stdout/stderr to a per-mode log file. Critical under
    pythonw (windowless), where sys.stdout is None and the engine's print() calls
    would otherwise raise and kill the process before it binds. Also gives us real
    logs to debug with. (Tail the file during dev instead of the console.)"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    logdir = os.path.join(base, "BotflowCompanion", "logs")
    try:
        os.makedirs(logdir, exist_ok=True)
        f = open(os.path.join(logdir, f"{name}.log"), "a",
                 buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = f
        sys.stderr = f
    except Exception:
        import io
        sys.stdout = sys.stderr = io.StringIO()  # last resort: never crash on print


def _self_cmd(mode):
    """Command to relaunch THIS binary in another mode (frozen or from source)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, os.path.abspath(__file__), mode]


def _engine_up(timeout=1.5):
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_engine(seconds=25):
    end = time.time() + seconds
    while time.time() < end:
        if _engine_up():
            return True
        time.sleep(0.5)
    return False


# ── ENGINE mode ──────────────────────────────────────────────────────────────
def run_engine():
    import companion
    companion.main()


# ── WINDOW mode ──────────────────────────────────────────────────────────────
def run_window():
    import webview
    _wait_engine(25)  # don't show a blank window before the engine answers
    webview.create_window(
        "Botflow Companion", UI_URL,
        width=560, height=760, min_size=(460, 560),
    )
    webview.start()


# ── TRAY mode ────────────────────────────────────────────────────────────────
def _tray_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=(30, 36, 64, 255),
                        outline=(108, 140, 255, 255), width=3)
    d.rounded_rectangle([24, 16, 40, 48], radius=4, outline=(238, 240, 245, 255), width=3)
    return img


def _single_instance_or_exit():
    """Ensure only ONE tray runs (else multiple supervisors churn engines).
    Uses a Windows named mutex; on other OSes a localhost lock socket."""
    if os.name == "nt":
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "BotflowCompanionTray")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            sys.exit(0)
        return mutex  # keep handle alive for process lifetime
    else:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 17320))  # adjacent to the engine port
        except OSError:
            sys.exit(0)
        return s


def run_tray():
    import pystray

    _lock = _single_instance_or_exit()  # noqa: F841 (held for process lifetime)
    engine_proc = {"p": None}

    def ensure_engine():
        if _engine_up():
            return
        if engine_proc["p"] and engine_proc["p"].poll() is None:
            return
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        engine_proc["p"] = subprocess.Popen(
            _self_cmd("--engine"),
            creationflags=flags if os.name == "nt" else 0,
        )

    def open_window(icon=None, item=None):
        ensure_engine()
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen(_self_cmd("--window"),
                         creationflags=flags if os.name == "nt" else 0)

    def quit_app(icon, item):
        try:
            if engine_proc["p"]:
                engine_proc["p"].terminate()
        except Exception:
            pass
        icon.stop()

    # Bring the engine up immediately + keep it alive.
    ensure_engine()

    def supervisor():
        while True:
            time.sleep(5)
            ensure_engine()
    threading.Thread(target=supervisor, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Open Botflow Companion", open_window, default=True),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("BotflowCompanion", _tray_icon_image(),
                        "Botflow Companion", menu)
    # Open the window once on first launch so the user sees the sign-in.
    threading.Thread(target=lambda: (_wait_engine(25), open_window()),
                     daemon=True).start()
    icon.run()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--tray"
    if mode == "--engine":
        _setup_logging("engine")
        run_engine()
    elif mode == "--window":
        _setup_logging("window")
        run_window()
    else:
        _setup_logging("tray")
        run_tray()


if __name__ == "__main__":
    main()
