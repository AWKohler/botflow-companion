#!/usr/bin/env python3
"""Interactive Apple ID sign-in for the Botflow Companion engine.

For platforms without the native menu-bar app (Windows/Linux): prompts for your
Apple ID locally and signs the *running* engine in via its loopback API. Your
credentials are sent only to the engine on 127.0.0.1 — never anywhere else.

Usage:  python signin.py        (engine must already be running)
"""
import getpass
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:17321/botflow/v1"


def _post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except urllib.error.URLError:
        print("ERROR: can't reach the companion on 127.0.0.1:17321 — is it running?")
        sys.exit(1)


def main():
    # Confirm the engine is up.
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=10) as r:
            h = json.loads(r.read())
        if h.get("loggedIn"):
            print(f"Already signed in as {h.get('appleId')} (team {h.get('team')}).")
            return
    except Exception:
        print("ERROR: companion not reachable on 127.0.0.1:17321 — start it first.")
        sys.exit(1)

    print("Botflow Companion — Apple ID sign-in")
    print("(a free Apple ID works; credentials go only to the local companion)\n")
    apple_id = input("Apple ID (email): ").strip()
    password = getpass.getpass("Password: ")

    print("Signing in...")
    res = _post("/auth/login", {"appleId": apple_id, "password": password})
    if res.get("needs2fa"):
        kind = res.get("type", "")
        code = input(f"Two-factor code{f' ({kind})' if kind else ''}: ").strip()
        res = _post("/auth/2fa", {"code": code})

    if res.get("ok"):
        print(f"\n✅ Signed in! Team: {res.get('team')}")
        print("You can now use Run on iPhone in Botflow.")
    else:
        print(f"\n❌ Sign-in failed: {res.get('error') or res}")
        sys.exit(1)


if __name__ == "__main__":
    main()
