@echo off
title Blink Face ID Launcher
cd /d "%~dp0"
echo Starting Blink Face ID Settings...
start "" "%~dp0blink.exe" settings
exit /b 0
