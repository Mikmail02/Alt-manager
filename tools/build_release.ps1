# Build a full release: icon -> PyInstaller -> Inno Setup.
# Usage (from project root):   powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
# Requires: Python 3.11+, PyInstaller, Inno Setup 6 in PATH (iscc.exe).

$ErrorActionPreference = "Stop"

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    Write-Host "==> Installing Python deps" -ForegroundColor Cyan
    python -m pip install --upgrade pip | Out-Null
    python -m pip install -r requirements.txt pyinstaller | Out-Null

    Write-Host "==> Generating icon" -ForegroundColor Cyan
    python tools\make_icon.py

    Write-Host "==> Running PyInstaller" -ForegroundColor Cyan
    if (Test-Path build) { Remove-Item -Recurse -Force build }
    if (Test-Path dist)  { Remove-Item -Recurse -Force dist  }
    python -m PyInstaller build.spec --noconfirm --clean

    if (-not (Test-Path "dist\CCHub.exe")) {
        throw "PyInstaller did not produce dist\CCHub.exe"
    }

    Write-Host "==> Running Inno Setup" -ForegroundColor Cyan
    $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
        )
        $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) { throw "ISCC.exe not found. Install Inno Setup 6." }
        $iscc = Get-Command $iscc
    }
    & $iscc.Path installer\CCHub.iss

    Write-Host "==> Done." -ForegroundColor Green
    Get-ChildItem installer\Output\*.exe | Format-Table Name, Length, LastWriteTime
} finally {
    Pop-Location
}
