$ErrorActionPreference = 'Stop'
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
& $PythonExecutablePath -m ruff check app tests scripts\init_config.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m pytest
exit $LASTEXITCODE
