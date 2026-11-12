@echo off
setlocal enabledelayedexpansion
title Blink Face ID Installer

echo =======================================================
echo          Blink Face ID - Windows Setup
echo =======================================================
echo.

set "TARGET_DIR=%LOCALAPPDATA%\Programs\Blink"
echo [1/4] Closing any running Blink background processes...
taskkill /f /im blink.exe >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1

echo [2/4] Installing Blink to: %TARGET_DIR%...
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

xcopy /E /I /Y /Q "%~dp0*" "%TARGET_DIR%\" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy files to %TARGET_DIR%.
    pause
    exit /b 1
)

echo [3/4] Creating Desktop and Start Menu shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$desktop = [Environment]::GetFolderPath('Desktop'); " ^
  "$s1 = $ws.CreateShortcut(\"$desktop\Blink.lnk\"); " ^
  "$s1.TargetPath = '%TARGET_DIR%\blink.exe'; " ^
  "$s1.Arguments = 'settings'; " ^
  "$s1.WorkingDirectory = '%TARGET_DIR%'; " ^
  "$s1.IconLocation = '%TARGET_DIR%\appicon.ico'; " ^
  "$s1.Description = 'Blink Face ID Biometric Authentication'; " ^
  "$s1.Save(); " ^
  "$startMenu = [Environment]::GetFolderPath('Programs'); " ^
  "$s2 = $ws.CreateShortcut(\"$startMenu\Blink.lnk\"); " ^
  "$s2.TargetPath = '%TARGET_DIR%\blink.exe'; " ^
  "$s2.Arguments = 'settings'; " ^
  "$s2.WorkingDirectory = '%TARGET_DIR%'; " ^
  "$s2.IconLocation = '%TARGET_DIR%\appicon.ico'; " ^
  "$s2.Description = 'Blink Face ID Biometric Authentication'; " ^
  "$s2.Save(); " ^
  "$startup = [Environment]::GetFolderPath('Startup'); " ^
  "$s3 = $ws.CreateShortcut(\"$startup\Blink Daemon.lnk\"); " ^
  "$s3.TargetPath = '%TARGET_DIR%\blink.exe'; " ^
  "$s3.Arguments = 'daemon'; " ^
  "$s3.WorkingDirectory = '%TARGET_DIR%'; " ^
  "$s3.IconLocation = '%TARGET_DIR%\appicon.ico'; " ^
  "$s3.Description = 'Blink Face ID Background Service'; " ^
  "$s3.WindowStyle = 7; " ^
  "$s3.Save();"

echo [4/4] Installation Complete! Launching Blink...
start "" "%TARGET_DIR%\blink.exe" settings
exit /b 0
