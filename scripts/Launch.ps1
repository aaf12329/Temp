<#
结构：入口参数 → 路径地址簿 → 解释器选择 → 单个命令。
调用：启动脚本中的BAT → 本脚本 → 既有Python模块/维护脚本/Next。
数据流：用途+本机解释器配置 → 同一环境中的交互任务；不读取或输出密钥。
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Cli', 'Advisor', 'AdvisorDemo', 'Api', 'Worker', 'Checks', 'AdvisorChecks', 'Frontend')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
$LaunchPaths = @{
    Python = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
    LocalConfig = Join-Path $ProjectRootPath '快速部署\config.local.json'
    Frontend = Join-Path $ProjectRootPath 'frontend'
    Next = Join-Path $ProjectRootPath 'frontend\node_modules\next\dist\bin\next'
    AdvisorTests = Join-Path $ProjectRootPath '产业研究建议助手\tests'
    Checks = Join-Path $PSScriptRoot 'Test.ps1'
}
Set-Location -LiteralPath $ProjectRootPath

function Resolve-LaunchPython {
    <# 共用一键部署所选的解释器；供终端、服务与检查入口调用。 #>
    $candidate = $LaunchPaths.Python
    if (Test-Path -LiteralPath $LaunchPaths.LocalConfig) {
        $settings = Get-Content -LiteralPath $LaunchPaths.LocalConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($settings.environment_mode -eq 'existing') {
            if (-not $settings.python_path) { throw 'Set python_path in the local deployment config.' }
            $candidate = if ([IO.Path]::IsPathRooted($settings.python_path)) { $settings.python_path }
                         else { [IO.Path]::GetFullPath((Join-Path $ProjectRootPath $settings.python_path)) }
        }
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw 'Python environment not found. Run the platform startup or environment setup BAT first.'
    }
    return $candidate
}

try {
    if ($Action -eq 'Frontend') {
        if (-not (Test-Path -LiteralPath $LaunchPaths.Next -PathType Leaf)) {
            throw 'Frontend dependencies missing. Run the environment setup BAT first.'
        }
        $node = Get-Command node -ErrorAction Stop
        Set-Location -LiteralPath $LaunchPaths.Frontend
        Write-Host 'Frontend only: choose Design Preview on the page. Real research also needs API and Worker.'
        & $node.Source $LaunchPaths.Next dev --hostname 127.0.0.1 --port 3000
        exit $LASTEXITCODE
    }
    $python = Resolve-LaunchPython
    if ($Action -eq 'Checks') {
        & $LaunchPaths.Checks -PythonPath $python
        exit $LASTEXITCODE
    }
    if ($Action -in @('Api', 'Worker')) {
        & $python -m app.cli init
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $arguments = switch ($Action) {
        'Cli' { @('-m', 'app.pipeline_cli') }
        'Advisor' { @('-m', 'app.advisor_cli', '--project', 'terminal', 'chat') }
        'AdvisorDemo' { @('-m', 'app.advisor_cli', '--project', 'terminal', '--demo', 'chat') }
        'Api' { @('-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', '8000') }
        'Worker' { @('-m', 'app.cli', 'worker') }
        'AdvisorChecks' { @('-m', 'pytest', '-q', $LaunchPaths.AdvisorTests, '--import-mode=importlib') }
    }
    & $python @arguments
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
