param([switch]$Once)
$ErrorActionPreference = 'Stop'
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
& $PythonExecutablePath -m app.cli init
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$WorkerArguments = @('-m', 'app.cli', 'worker')
if ($Once) { $WorkerArguments += '--once' }
& $PythonExecutablePath @WorkerArguments
exit $LASTEXITCODE
