@echo off
cd /d "%~dp0\.."
echo Launching Blink Face ID Windows Setup...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
pause
