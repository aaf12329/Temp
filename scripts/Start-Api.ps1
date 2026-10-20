param([int]$Port = 8000, [switch]$Reload)
$ErrorActionPreference = 'Stop'
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExecutablePath)) { throw 'Run scripts\Setup.ps1 first.' }
& $PythonExecutablePath -m app.cli init
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$ServerArguments = @('-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', "$Port")
if ($Reload) { $ServerArguments += '--reload' }
& $PythonExecutablePath @ServerArguments
exit $LASTEXITCODE
