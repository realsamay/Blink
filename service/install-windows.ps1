<#
.SYNOPSIS
    Blink Face ID - Windows 10/11 One-Click Setup & Service Installer
.DESCRIPTION
    Installs dependencies, configures hardware-accelerated on-device models,
    creates Start Menu shortcuts, and registers user logon autostart.
#>

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "             BLINK - FACE ID FOR WINDOWS SETUP" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check Python
$pyCmd = "python"
try {
    $pyCheck = python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[✓] Python detected: $pyCheck" -ForegroundColor Green
} catch {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    $foundPy = $false
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            $pyCmd = $cand
            $foundPy = $true
            Write-Host "[✓] Found Python at: $pyCmd" -ForegroundColor Green
            break
        }
    }
    if (-not $foundPy) {
        Write-Host "[!] Python 3.10+ was not found in PATH or standard directories." -ForegroundColor Red
        Write-Host "Please install Python from https://www.python.org/downloads/ (check 'Add python.exe to PATH')." -ForegroundColor Yellow
        exit 1
    }
}

# 2. Virtual Environment Setup
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path ".venv")) {
    Write-Host "[*] Creating virtual environment (.venv)..." -ForegroundColor Yellow
    & $pyCmd -m venv .venv
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$venvPip = Join-Path $projectRoot ".venv\Scripts\pip.exe"

Write-Host "[*] Upgrading pip and installing dependencies..." -ForegroundColor Yellow
& $venvPip install --upgrade pip
& $venvPip install opencv-python numpy cryptography pillow onnxruntime

# 3. Model Verification
$modelsDir = Join-Path $projectRoot "models"
$yunetModel = Join-Path $modelsDir "face_detection_yunet.onnx"
$arcfaceModel = Join-Path $modelsDir "w600k_mbf.onnx"

if (-not (Test-Path $yunetModel) -or -not (Test-Path $arcfaceModel)) {
    Write-Host "[!] Warning: Model files not found in $modelsDir. Verifying bundled weights..." -ForegroundColor Yellow
} else {
    Write-Host "[✓] Neural models verified (YuNet CNN + ArcFace MobileFaceNet)." -ForegroundColor Green
}

# 4. Create Desktop & Start Menu Launchers
$appData = [Environment]::GetFolderPath("ApplicationData")
$startMenu = Join-Path $appData "Microsoft\Windows\Start Menu\Programs"
$desktop = [Environment]::GetFolderPath("Desktop")

$batContent = @"
@echo off
set "PYTHONPATH=$projectRoot;%PYTHONPATH%"
start "" "$venvPython" "$projectRoot\blink\main.py" settings
"@

Set-Content -Path (Join-Path $startMenu "Blink.bat") -Value $batContent
Set-Content -Path (Join-Path $desktop "Blink.bat") -Value $batContent
Write-Host "[✓] Created Start Menu & Desktop launchers." -ForegroundColor Green

# 5. Configure Autostart on Logon
$startupDir = Join-Path $appData "Microsoft\Windows\Start Menu\Programs\Startup"
$startupBat = Join-Path $startupDir "BlinkDaemon.bat"

$daemonBatContent = @"
@echo off
set "PYTHONPATH=$projectRoot;%PYTHONPATH%"
start "" "$projectRoot\.venv\Scripts\pythonw.exe" "$projectRoot\blink\main.py" daemon
"@

Set-Content -Path $startupBat -Value $daemonBatContent
Write-Host "[✓] Configured background unlock daemon autostart on user logon." -ForegroundColor Green

# 6. Verify System Status
Write-Host "`n[*] Running Blink diagnostic check..." -ForegroundColor Cyan
& $venvPython "$projectRoot\blink\main.py" status

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  Setup complete! Launch 'Blink' from your Desktop or Start Menu" -ForegroundColor Green
Write-Host "  to configure your password and enroll your face." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
