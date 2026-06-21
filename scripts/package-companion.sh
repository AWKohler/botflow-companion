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
  --add-binary "$ENGINE/signer/anisette_helper:." \
  --collect-all anisette --collect-all srp --collect-all lief --collect-all unicorn \
  --collect-all pymobiledevice3 \
  --hidden-import apple_account --hidden-import device_backend --hidden-import ui \
  "$ENGINE/companion.py"
test -x "$ENGINE/dist/botflow-engine" || { echo "engine freeze failed"; exit 1; }

echo "==> 2/4  Build macOS app (Release)"
( cd "$MACOS" && xcodegen generate >/dev/null )
DERIVED="$DIST/DerivedData"
xcodebuild -project "$MACOS/$APP_NAME.xcodeproj" -scheme "$APP_NAME" \
  -configuration Release -derivedDataPath "$DERIVED" build >/dev/null
# Find the built .app by globbing the products dir rather than reconstructing
# the path from APP_NAME — the Xcode product name can diverge from the scheme.
PRODUCTS="$DERIVED/Build/Products/Release"
shopt -s nullglob
APPS=("$PRODUCTS"/*.app)
shopt -u nullglob
if [[ ${#APPS[@]} -eq 0 ]]; then
  echo "app build failed: no .app found in $PRODUCTS" >&2; exit 1
elif [[ ${#APPS[@]} -gt 1 ]]; then
  echo "app build ambiguous: multiple .app bundles in $PRODUCTS: ${APPS[*]}" >&2; exit 1
fi
APP="${APPS[0]}"

echo "==> 3/4  Bundle engine into the app"
mkdir -p "$APP/Contents/Resources/engine"
cp "$ENGINE/dist/botflow-engine" "$APP/Contents/Resources/engine/botflow-engine"
chmod +x "$APP/Contents/Resources/engine/botflow-engine"

echo "==> 4/4  Sign + notarize + .dmg"
ENT="$MACOS/Companion.entitlements"
mkdir -p "$DIST"
DMG="$DIST/$APP_NAME.dmg"
rm -f "$DMG"

if [[ -n "${DEVELOPER_ID:-}" ]]; then
  # Hardened runtime + entitlements (CPython/native deps need them). Sign the
  # nested engine first, then the app.
  codesign --force --options runtime --timestamp --entitlements "$ENT" \
    --sign "$DEVELOPER_ID" "$APP/Contents/Resources/engine/botflow-engine"
  codesign --force --options runtime --timestamp --entitlements "$ENT" \
    --sign "$DEVELOPER_ID" "$APP"
  codesign --verify --deep --strict "$APP" || { echo "codesign verify failed"; exit 1; }

  if [[ -n "${NOTARY_PROFILE:-}" ]]; then
    echo "    notarizing app…"
    APPZIP="$DIST/_notarize.zip"; rm -f "$APPZIP"
    ditto -c -k --keepParent "$APP" "$APPZIP"
    xcrun notarytool submit "$APPZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP"      # app now validates offline
    rm -f "$APPZIP"
  else
    echo "    (DEVELOPER_ID set but no NOTARY_PROFILE — signed, NOT notarized)"
  fi
else
  echo "    (no DEVELOPER_ID — ad-hoc signing; users must right-click → Open)"
  codesign --force --deep --sign - "$APP"
fi

# Build the .dmg (drag-to-Applications layout).
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

if [[ -n "${DEVELOPER_ID:-}" ]]; then
  codesign --force --timestamp --sign "$DEVELOPER_ID" "$DMG"
  if [[ -n "${NOTARY_PROFILE:-}" ]]; then
    echo "    notarizing dmg…"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"     # dmg validates offline too
  fi
fi

mkdir -p "$WEB_PUBLIC"
cp "$DMG" "$WEB_PUBLIC/$APP_NAME.dmg"
echo "==> Done: $DMG"
echo "    Published to: $WEB_PUBLIC/$APP_NAME.dmg"
