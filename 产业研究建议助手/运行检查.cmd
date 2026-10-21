@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m pytest -q "产业研究建议助手\tests" --import-mode=importlib
pause
