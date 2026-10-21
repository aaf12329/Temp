@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Launch.ps1" -Action AdvisorDemo
set "LAUNCH_EXIT=%ERRORLEVEL%"
pause
exit /b %LAUNCH_EXIT%
