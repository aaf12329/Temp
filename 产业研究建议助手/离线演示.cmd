@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m app.advisor_cli --project terminal --demo chat
pause
