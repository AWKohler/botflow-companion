# Build a self-contained Botflow Companion engine for Windows (botflow-engine.exe).
#
# Must run ON Windows (PyInstaller does not cross-compile). Produces a single
# .exe that serves the local API on 127.0.0.1:17321 — the same contract Botflow
# already speaks to the macOS engine.
#
# Prerequisites:
#   - Python 3.12 (winget install Python.Python.3.12)
#   - For device install/detection: libimobiledevice binaries in engine\tools\
#     (idevice_id.exe, ideviceinfo.exe, ideviceinstaller.exe, idevicedebug.exe)
#     AND Apple Mobile Device Support (installed with iTunes from apple.com).
#   - For Apple-ID sign-in: an anisette server; point at it with
#     $env:BOTFLOW_ANISETTE_URL before launching the engine.
#   - For signing IPAs: zsign.exe in engine\tools\.
#
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File scripts\package-companion-win.ps1
$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$engine = Join-Path $root "engine"
Set-Location $engine

Write-Host "==> 1/3  venv + deps"
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -q
.\.venv\Scripts\python.exe -m pip install pyinstaller -q

Write-Host "==> 2/3  freeze engine (PyInstaller onefile)"
$addBin = @()
if (Test-Path "tools") {
  # Bundle libimobiledevice/zsign binaries next to the engine (resolved via _MEIPASS\tools).
  $addBin = @("--add-data", "tools;tools")
}
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile `
  --name botflow-engine `
  --paths signer --paths . `
  --collect-all anisette --collect-all srp --collect-all lief --collect-all unicorn `
  --hidden-import apple_account --hidden-import device_backend `
  @addBin `
  companion.py

Write-Host "==> 3/3  done"
Write-Host "    Built: $engine\dist\botflow-engine.exe"
Write-Host "    Run it, then open Botflow — it will detect the companion on 127.0.0.1:17321."
