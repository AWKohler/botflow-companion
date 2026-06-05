# Botflow Companion

Local macOS agent that bridges **Botflow** (botflow.io, in the browser) to a
**physically-connected iPhone**. The cloud Mac can build apps but can't install
onto your phone — the phone is paired/trusted with *your* Mac — so this agent
does the device-side install/launch.

This is the Botflow equivalent of Rork's Companion app.

## Run (MVP)

```bash
npm start         # node server.mjs — listens on http://127.0.0.1:17321
```

The Botflow web UI's **Run on iPhone** control talks to it on `127.0.0.1:17321`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/botflow/v1/health` | `{ ok, app, source }` |
| GET | `/botflow/v1/devices` | `{ devices: [{ id, name, osVersion, type }] }` via `devicectl` |
| POST | `/botflow/v1/install` | `{ deviceId, ipaUrl, projectId }` → `{ jobId }` (download → sign → install → launch) |
| GET | `/botflow/v1/install/:jobId` | install job status |

## Status

- ✅ health + device discovery (`xcrun devicectl list devices`), CORS + Private Network Access headers so an HTTPS page can reach loopback.
- 🚧 install pipeline: scaffolded. The cloud IPA is **unsigned**; it must be
  code-signed for the target device locally before `devicectl device install`.
  Requires an Apple ID/team in Xcode (Settings → Accounts) so a signing identity
  exists. Wiring next.

## Requirements

- macOS 26+, Xcode (full) with command line tools.
- iPhone: connected, unlocked, **Trusted**, **Developer Mode** on.
  (The companion can reveal Developer Mode via a `devicectl` developer-services
  request; the user still toggles it on-device + restarts.)

## Roadmap

- Local sign + `devicectl` install/launch (the install pipeline).
- Reveal/arm Developer Mode + guided enable.
- Preflight checks (Xcode, CLT, signing team, device trust/dev-mode).
- Package as a signed/notarized menu-bar app with auto-update + Botflow login.
