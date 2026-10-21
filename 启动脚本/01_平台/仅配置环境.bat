@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\快速部署\Start.ps1" -SetupOnly -ChooseBackend %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
pause
exit /b %LAUNCH_EXIT%
