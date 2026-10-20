param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExecutablePath)) {
    if ($PythonPath) { & $PythonPath -m venv (Join-Path $ProjectRootPath '.venv') }
    else { & py -3 -m venv (Join-Path $ProjectRootPath '.venv') }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
}
if (Test-Path -LiteralPath (Join-Path $ProjectRootPath 'requirements.lock.txt')) {
    & $PythonExecutablePath -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $PythonExecutablePath -m pip install --no-deps -e .
} else {
    & $PythonExecutablePath -m pip install -e '.[dev]'
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath scripts\init_config.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m app.cli init
exit $LASTEXITCODE
