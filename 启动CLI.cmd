@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\Setup.ps1 with Python 3.12 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.pipeline_cli
set "CLI_EXIT=%ERRORLEVEL%"
pause
exit /b %CLI_EXIT%
