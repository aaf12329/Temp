@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\快速部署\Start.ps1" %*
set "DEPLOY_EXIT=%ERRORLEVEL%"
if not "%DEPLOY_EXIT%"=="0" echo Deployment or startup failed. Read the message above.
pause
exit /b %DEPLOY_EXIT%
