# ==============================================================================
# Blink Face ID — Universal One-Click Windows Quick Installer
# Works from PowerShell OR Command Prompt. Does NOT require Git.
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "           BLINK — FACE ID FOR WINDOWS INSTALLER" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$targetDir = Join-Path $env:USERPROFILE "Blink"
Write-Host "[*] Target Installation Directory: $targetDir" -ForegroundColor Gray

# 1. Acquire Blink Codebase (Git or Direct Zip Download)
if (Test-Path (Join-Path $targetDir "blink\main.py")) {
    Write-Host "[✓] Existing Blink installation found at $targetDir." -ForegroundColor Green
} else {
    $hasGit = $false
    try {
        $gitVer = git --version 2>&1
        if ($LASTEXITCODE -eq 0) { $hasGit = $true }
    } catch {
        $hasGit = $false
    }

    if ($hasGit) {
        Write-Host "[*] Git detected. Cloning repository..." -ForegroundColor Yellow
        git clone https://github.com/realsamay/Blink.git "$targetDir"
    } else {
        Write-Host "[*] Git not found. Downloading Blink archive from GitHub..." -ForegroundColor Yellow
        $tempZip = Join-Path $env:TEMP "Blink-main.zip"
        $downloadUrl = "https://github.com/realsamay/Blink/archive/refs/heads/main.zip"
        
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tempZip -UseBasicParsing
        
        Write-Host "[*] Extracting package..." -ForegroundColor Yellow
        $tempExtract = Join-Path $env:TEMP "Blink-extract"
        if (Test-Path $tempExtract) { Remove-Item -Recurse -Force $tempExtract }
        
        Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force
        
        $unzippedRoot = Join-Path $tempExtract "Blink-main"
        if (Test-Path $targetDir) { Remove-Item -Recurse -Force $targetDir }
        Move-Item -Path $unzippedRoot -Destination $targetDir -Force
        
        Remove-Item -Force $tempZip -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
        Write-Host "[✓] Download and extraction complete." -ForegroundColor Green
    }
}

# 2. Locate or Install Python 3.10+
$pythonExe = $null
try {
    $pyCheck = python --version 2>&1
    if ($LASTEXITCODE -eq 0) { $pythonExe = "python" }
} catch {}

if (-not $pythonExe) {
    # Check default Windows AppData locations
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            $pythonExe = $cand
            break
        }
    }
}

if (-not $pythonExe) {
    Write-Host "[!] Python 3.10+ was not found on your system." -ForegroundColor Red
    Write-Host "[*] Attempting automatic installation via winget..." -ForegroundColor Yellow
    try {
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $pythonExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    } catch {
        Write-Host "[!] Could not auto-install Python. Please download Python from https://www.python.org/downloads/" -ForegroundColor Red
        Write-Host "    IMPORTANT: Check the box '[✓] Add python.exe to PATH' during install!" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "[✓] Using Python: $pythonExe" -ForegroundColor Green

# 3. Launch internal setup script
Set-Location $targetDir
$setupScript = Join-Path $targetDir "service\install-windows.ps1"
if (Test-Path $setupScript) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$setupScript"
} else {
    Write-Host "[!] Error: Could not find setup script at $setupScript" -ForegroundColor Red
}
