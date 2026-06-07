#!/usr/bin/env bash
#
# Build a self-contained, distributable Botflow Companion.app and zip it for
# hosting on Vercel's CDN (webcontainer-ide/public/downloads).
#
# What it does:
#   1. Freeze the Python engine into a single binary with PyInstaller, including
#      the signer package + the compiled anisette_helper. → engine/dist/botflow-engine
#   2. Build the macOS app (Release) via XcodeGen + xcodebuild.
#   3. Drop the frozen engine into the .app at Contents/Resources/engine/ so the
#      app is self-contained (EngineProcess.swift prefers the bundled engine).
#   4. Code-sign + (optionally) notarize, then zip.
#
# Requirements:
#   - Xcode + xcodegen
#   - python3 with pyinstaller in the engine venv  (pip install pyinstaller)
#   - For a download that clears Gatekeeper on OTHER Macs you MUST notarize:
#       export DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
#       export NOTARY_PROFILE="your-notarytool-keychain-profile"
#     Without these the build is ad-hoc signed (works locally; users must
#     right-click → Open the first time).
#
# Usage:  scripts/package-companion.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="$ROOT/engine"
MACOS="$ROOT/macos"
DIST="$ROOT/dist"
APP_NAME="BotflowCompanion"
# Where the web app serves the download from (override with WEB_PUBLIC).
WEB_PUBLIC="${WEB_PUBLIC:-$HOME/Documents/webcontainer-project/webcontainer-ide/public/downloads}"

echo "==> 1/4  Freeze Python engine (PyInstaller)"
PY="$ENGINE/.venv/bin/python"
"$PY" -m PyInstaller --noconfirm --clean --onefile \
  --name botflow-engine \
  --distpath "$ENGINE/dist" --workpath "$ENGINE/build" --specpath "$ENGINE/build" \
  --paths "$ENGINE/signer" \
  --add-data "$ENGINE/signer/anisette_helper:signer" \
  --collect-all anisette --collect-all srp --collect-all lief --collect-all unicorn \
  --hidden-import apple_account \
  "$ENGINE/companion.py"
test -x "$ENGINE/dist/botflow-engine" || { echo "engine freeze failed"; exit 1; }

echo "==> 2/4  Build macOS app (Release)"
( cd "$MACOS" && xcodegen generate >/dev/null )
DERIVED="$DIST/DerivedData"
xcodebuild -project "$MACOS/$APP_NAME.xcodeproj" -scheme "$APP_NAME" \
  -configuration Release -derivedDataPath "$DERIVED" build >/dev/null
APP="$DERIVED/Build/Products/Release/$APP_NAME.app"
test -d "$APP" || { echo "app build failed"; exit 1; }

echo "==> 3/4  Bundle engine into the app"
mkdir -p "$APP/Contents/Resources/engine"
cp "$ENGINE/dist/botflow-engine" "$APP/Contents/Resources/engine/botflow-engine"
chmod +x "$APP/Contents/Resources/engine/botflow-engine"

echo "==> 4/4  Sign + zip"
if [[ -n "${DEVELOPER_ID:-}" ]]; then
  # Sign nested binaries first, then the app (hardened runtime for notarization).
  codesign --force --options runtime --timestamp \
    --sign "$DEVELOPER_ID" "$APP/Contents/Resources/engine/botflow-engine"
  codesign --force --options runtime --timestamp --deep \
    --sign "$DEVELOPER_ID" "$APP"
else
  echo "    (no DEVELOPER_ID — ad-hoc signing; users must right-click → Open)"
  codesign --force --deep --sign - "$APP"
fi

mkdir -p "$DIST"
ZIP="$DIST/$APP_NAME.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  echo "==> Notarize"
  xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"; ditto -c -k --keepParent "$APP" "$ZIP"
fi

mkdir -p "$WEB_PUBLIC"
cp "$ZIP" "$WEB_PUBLIC/$APP_NAME.zip"
echo "==> Done: $ZIP"
echo "    Published to: $WEB_PUBLIC/$APP_NAME.zip"
