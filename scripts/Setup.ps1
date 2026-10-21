<#
结构/调用：Setup → 选择解释器 → 安装统一依赖 → 初始化配置与数据库。
数据流：可选 Python/Conda 解释器 → 根 .venv 或明确指定环境；不隐式激活其他环境。
#>
param(
    [string]$PythonPath = '',
    [ValidateSet('auto', 'pytorch', 'pytorch-cuda', 'tensorflow', 'none')]
    [string]$GANBackend = 'pytorch',
    [switch]$UseExistingEnvironment
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$PythonExecutablePath = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
if ($UseExistingEnvironment) {
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw 'Existing environment mode requires -PythonPath pointing to its python.exe.'
    }
    $PythonExecutablePath = (Resolve-Path -LiteralPath $PythonPath).Path
} elseif (-not (Test-Path -LiteralPath $PythonExecutablePath)) {
    if ($PythonPath) { & $PythonPath -m venv (Join-Path $ProjectRootPath '.venv') }
    else { & py -3.12 -m venv (Join-Path $ProjectRootPath '.venv') }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
& $PythonExecutablePath -c "import sys; assert sys.version_info[:2] == (3, 12), 'Use Python 3.12 for the unified environment'"
if ($LASTEXITCODE -ne 0) { throw 'Pass -PythonPath pointing to Python 3.12.' }
& $PythonExecutablePath -m pip install -r requirement.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($GANBackend -eq 'auto') {
    # 已安装的 CPU/CUDA/TF 原样保留；新环境才默认安装 PyTorch CPU。
    & $PythonExecutablePath -c "import importlib.util, sys; sys.exit(0 if any(importlib.util.find_spec(n) for n in ('torch', 'tensorflow')) else 1)"
    if ($LASTEXITCODE -eq 0) { $GANBackend = 'none' }
    else { $GANBackend = 'pytorch' }
}
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
