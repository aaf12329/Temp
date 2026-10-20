@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m ruff check advisor_bot tests
if errorlevel 1 goto end
".venv\Scripts\python.exe" -m pytest -q
:end
pause
