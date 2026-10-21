param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
if ($PythonPath) { $PythonExecutablePath = $PythonPath }
& $PythonExecutablePath -m ruff check app tests migrations scripts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m pytest -q tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m pytest -q '产业研究建议助手\tests' --import-mode=importlib
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m pytest -q 'components\crawler\tests' --import-mode=importlib
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m pip check
exit $LASTEXITCODE
