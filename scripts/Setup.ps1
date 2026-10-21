param([string]$PythonPath = '', [ValidateSet('pytorch', 'pytorch-cuda', 'tensorflow', 'none')][string]$GANBackend = 'pytorch')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExecutablePath)) {
    if ($PythonPath) { & $PythonPath -m venv (Join-Path $ProjectRootPath '.venv') }
    else { & py -3.12 -m venv (Join-Path $ProjectRootPath '.venv') }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
& $PythonExecutablePath -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 for the unified environment"'
if ($LASTEXITCODE -ne 0) { throw 'Pass -PythonPath pointing to Python 3.12.' }
if (Test-Path -LiteralPath (Join-Path $ProjectRootPath 'requirements.lock.txt')) {
    & $PythonExecutablePath -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $PythonExecutablePath -m pip install --no-deps -e .
} else {
    & $PythonExecutablePath -m pip install -e '.[dev]'
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m pip install --no-deps -e (Join-Path $ProjectRootPath '产业研究建议助手')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($GANBackend -ne 'none') {
    & $PythonExecutablePath -m pip install -r (Join-Path $ProjectRootPath "requirements/gan-$GANBackend.txt")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $PythonExecutablePath -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath scripts\init_config.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExecutablePath -m app.cli init
exit $LASTEXITCODE
