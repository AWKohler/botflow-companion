# Build the distributable Botflow Companion for Windows.
#
# Produces a self-contained app folder (PyInstaller --onedir) containing the tray
# UI + engine + bundled libimobiledevice/zsign + pymobiledevice3, then (if Inno
# Setup is present) compiles the one-click installer that also fetches Apple's
# Mobile Device Support driver at install time.
#
# Must run ON Windows. Prereqs: Python 3.12, and engine\tools\ pre-populated with
# the libimobiledevice + zsign binaries and their DLLs (gather-tools step).
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\package-companion-win.ps1
$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$engine = Join-Path $root "engine"
$win    = Join-Path $root "windows"
Set-Location $engine

Write-Host "==> 1/4  venv + deps"
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -q
.\.venv\Scripts\python.exe -m pip install pyinstaller -q

if (-not (Test-Path "tools\idevice_id.exe")) {
  Write-Warning "engine\tools is missing libimobiledevice/zsign binaries. Run the gather-tools step (MSYS2) first."
}

Write-Host "==> 2/4  freeze app (PyInstaller --onedir, windowed, tray)"
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onedir --windowed `
  --name BotflowCompanion `
  --icon "$win\BotflowCompanion.ico" `
  --paths signer --paths . `
  --add-data "tools;tools" `
  --add-data "assets;assets" `
  --collect-all pymobiledevice3 `
  --collect-all anisette `
  --collect-all srp `
  --collect-all lief `
  --collect-all unicorn `
  --collect-all pystray `
  --collect-all webview `
  --collect-all pythonnet `
  --hidden-import apple_account `
  --hidden-import device_backend `
  --hidden-import ui `
  --hidden-import companion `
  --hidden-import clr `
  app.py
$appdir = Join-Path $engine "dist\BotflowCompanion"
if (-not (Test-Path (Join-Path $appdir "BotflowCompanion.exe"))) { throw "freeze failed" }
Write-Host "    Built: $appdir"

Write-Host "==> 3/4  installer (Inno Setup)"
$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
if (Test-Path $iscc) {
  & $iscc "$win\installer.iss"
  Write-Host "==> 4/4  done -> $win\Output\BotflowCompanionSetup.exe"
} else {
  Write-Warning "Inno Setup (ISCC) not found — skipping installer. App folder is at $appdir."
}
