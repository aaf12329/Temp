<#
结构：展示硬件 → 读取配置/选择后端 → Resolve-Python → 安装依赖 → 本地服务入口。
调用：启动脚本/01_平台/一键启动.bat → 本脚本 → scripts/Setup.ps1 / npm / app.local_service。
数据流：本地JSON与依赖指纹 → 单个Python环境、npm依赖、API + Worker + 标准Next前端。
路径集中在开头；用户的 Conda 解释器必须明确指定，不随 PATH 悄悄切换环境。
#>
param(
    [string]$ConfigPath = '',
    [switch]$SetupOnly,
    [switch]$Reinstall,
    [switch]$Plan,
    [switch]$ChooseBackend,
    [switch]$NonInteractive
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRootPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRootPath
$DeployPaths = @{
    Template = Join-Path $PSScriptRoot 'config.example.json'
    Config = Join-Path $PSScriptRoot 'config.local.json'
    Python = Join-Path $ProjectRootPath '.venv\Scripts\python.exe'
    Venv = Join-Path $ProjectRootPath '.venv'
    State = Join-Path $ProjectRootPath 'work\quick-deploy'
    PythonStamp = Join-Path $ProjectRootPath 'work\quick-deploy\python.sha256'
    NpmStamp = Join-Path $ProjectRootPath 'work\quick-deploy\npm.sha256'
    SetupLock = Join-Path $ProjectRootPath 'work\quick-deploy\setup.lock'
    Setup = Join-Path $ProjectRootPath 'scripts\Setup.ps1'
    Init = Join-Path $ProjectRootPath 'scripts\init_config.py'
    Hardware = Join-Path $PSScriptRoot 'Hardware.ps1'
    PlatformFrontend = Join-Path $ProjectRootPath 'frontend'
    FingerprintInputs = @('requirement.txt', 'requirements.lock.txt', 'pyproject.toml',
        '产业研究建议助手/pyproject.toml', 'scripts/Setup.ps1', '快速部署/Start.ps1', '快速部署/Hardware.ps1',
        'requirements/gan-pytorch.txt', 'requirements/gan-pytorch-cuda.txt',
        'requirements/gan-tensorflow.txt')
}
. $DeployPaths.Hardware

function Invoke-Checked {
    <# 运行本机工具并把非零退出码转为失败；供安装与初始化步骤调用。 #>
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): $Executable" }
}

function Read-DeployConfig {
    <# 合并模板与用户配置并校验字段；供主入口调用，不输出密钥。 #>
    $settings = Get-Content -LiteralPath $DeployPaths.Template -Raw -Encoding UTF8 | ConvertFrom-Json
    $selected = if ($ConfigPath) { $ConfigPath } else { $DeployPaths.Config }
    if (Test-Path -LiteralPath $selected) {
        $custom = Get-Content -LiteralPath $selected -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in $custom.PSObject.Properties) {
            if (-not $settings.PSObject.Properties[$property.Name]) {
                throw "Unknown deployment setting: $($property.Name)"
            }
            $settings.($property.Name) = $property.Value
        }
    } elseif ($ConfigPath) { throw 'Deployment config does not exist.' }
    elseif (-not $Plan) { Copy-Item -LiteralPath $DeployPaths.Template -Destination $selected }
    if ($settings.environment_mode -notin @('venv', 'existing')) { throw 'environment_mode: venv or existing.' }
    if ($settings.gan_backend -notin @('auto', 'none', 'pytorch', 'pytorch-cuda', 'tensorflow')) {
        throw 'Unsupported gan_backend.'
    }
    if ($settings.port -isnot [int] -or $settings.port -lt 1 -or $settings.port -gt 65535) {
        throw 'port must be an integer between 1 and 65535.'
    }
    foreach ($name in @('install_npm', 'prepare_vector_model', 'choose_backend')) {
        if ($settings.$name -isnot [bool]) { throw "$name must be true or false." }
    }
    foreach ($name in @('python_path', 'frontend_dir')) {
        if ($settings.$name -isnot [string]) { throw "$name must be a string." }
    }
    return $settings
}

function Resolve-Python {
    <# 复用根环境或明确指定的已有环境；缺少根环境时用 Python 3.12 创建。 #>
    param($Settings)
    if ($Settings.environment_mode -eq 'existing') {
        if (-not $Settings.python_path) { throw 'Set python_path to your Conda environment python.exe.' }
        $candidate = $Settings.python_path
    } else {
        $candidate = $DeployPaths.Python
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            if ($Plan) { Write-Host '[PLAN] Create root .venv with Python 3.12.'; return $candidate }
            if (Test-Path -LiteralPath $DeployPaths.Venv) {
                throw 'Root .venv exists but python.exe is missing; inspect it before recreating.'
            }
            if ($Settings.python_path) {
                Invoke-Checked $Settings.python_path @('-c', 'import sys; assert sys.version_info[:2] == (3,12)')
                Invoke-Checked $Settings.python_path @('-m', 'venv', $DeployPaths.Venv)
            } else {
                $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
                if (-not $launcher) { throw 'Install Python 3.12 with its launcher, or set python_path.' }
                Invoke-Checked $launcher.Source @('-3.12', '-m', 'venv', $DeployPaths.Venv)
            }
        }
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'Configured Python executable not found.' }
    $candidate = (Resolve-Path -LiteralPath $candidate).Path
    $environmentRoot = Split-Path -Parent $candidate
    if ($Settings.environment_mode -eq 'existing' -and
        (Test-Path -LiteralPath (Join-Path $environmentRoot 'conda-meta'))) {
        # 直接使用 Conda python.exe 时补齐 DLL 搜索路径，不改用户级配置或激活脚本。
        $nativePaths = @('', 'Scripts', 'Library/bin', 'Library/usr/bin', 'Library/mingw-w64/bin') |
            ForEach-Object { if ($_ -eq '') { $environmentRoot } else { Join-Path $environmentRoot $_ } } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Container }
        $env:PATH = ($nativePaths -join ';') + ';' + $env:PATH
        $env:CONDA_PREFIX = $environmentRoot
    }
    Invoke-Checked $candidate @('-c', "import sys; assert sys.version_info[:2] == (3,12), 'Python 3.12 is required'")
    Write-Host "Python: $candidate"
    return $candidate
}

function Get-Fingerprint {
    <# 对输入文件和运行时标识计算指纹；供依赖缓存调用，只持久化哈希。 #>
    param([string[]]$Files, [string]$Identity)
    $parts = @($Identity)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $Files) {
            if (Test-Path -LiteralPath $file) {
                $stream = [IO.File]::OpenRead($file)
                try { $parts += [BitConverter]::ToString($sha.ComputeHash($stream)) }
                finally { $stream.Dispose() }
            } else { $parts += '-' }
        }
        return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))))).Replace('-', '')
    } finally { $sha.Dispose() }
}

function Get-PythonIdentity {
    <# 读取版本和可选框架元数据，不导入大型框架；供安装指纹调用。 #>
    param([string]$Python)
    $identity = & $Python -c "import importlib.metadata as m, sys; print(sys.version, sys.prefix, [(d.metadata['Name'], d.version) for d in m.distributions() if d.metadata['Name'].lower().replace('_', '-') in ('torch', 'tensorflow', 'tensorflow-cpu')])"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Python environment.' }
    return $identity
}

function Install-Python {
    <# 依赖变更或显式重装才安装；初始化每次运行，已有 .env 不覆盖。 #>
    param($Settings, [string]$Python)
    $identity = Get-PythonIdentity $Python
    $files = @($DeployPaths.FingerprintInputs | ForEach-Object { Join-Path $ProjectRootPath $_ })
    $fingerprint = Get-Fingerprint $files "$ProjectRootPath|$Python|$identity|$($Settings.gan_backend)"
    $cached = (Test-Path -LiteralPath $DeployPaths.PythonStamp) -and
        ((Get-Content -LiteralPath $DeployPaths.PythonStamp -Raw).Trim() -eq $fingerprint)
    if ($Reinstall -or -not $cached) {
        Write-Host 'Installing Python dependencies...'
        if (Test-Path -LiteralPath $DeployPaths.PythonStamp) { Remove-Item -LiteralPath $DeployPaths.PythonStamp }
        $arguments = @{ PythonPath = $Python; GANBackend = $Settings.gan_backend; UseExistingEnvironment = $true }
        & $DeployPaths.Setup @arguments
        if ($LASTEXITCODE -ne 0) { throw 'Python setup failed; retry after correcting the error above.' }
        Confirm-GPUBackend $Settings $Python
        # 首次安装可能新增 GAN，记录安装后的身份，避免下次无意义重装。
        $identity = Get-PythonIdentity $Python
        $fingerprint = Get-Fingerprint $files "$ProjectRootPath|$Python|$identity|$($Settings.gan_backend)"
        [IO.File]::WriteAllText($DeployPaths.PythonStamp, $fingerprint)
    } else {
        Write-Host 'Python dependencies unchanged; reusing environment.'
        Invoke-Checked $Python @('-m', 'pip', 'check')
        Invoke-Checked $Python @($DeployPaths.Init)
        Invoke-Checked $Python @('-m', 'app.cli', 'init')
        Confirm-GPUBackend $Settings $Python
    }
    if ($Settings.prepare_vector_model) {
        Write-Host 'Preparing local vector model (existing verified cache is reused)...'
        Invoke-Checked $Python @('-m', 'app.cli', 'model-setup')
    }
}

function Confirm-GPUBackend {
    <# 显式 GPU 版本安装后执行一次真实 CUDA 小运算；供依赖安装/复用调用。 #>
    param($Settings, [string]$Python)
    if ($Settings.gan_backend -eq 'pytorch-cuda') {
        Write-Host '正在验证 PyTorch CUDA 实际运算...'
        Invoke-Checked $Python @('-c', "import torch; assert torch.cuda.is_available(), 'CUDA unavailable: check NVIDIA driver or select CPU'; x=torch.ones(1, device='cuda'); torch.cuda.synchronize(); print('CUDA OK:', torch.cuda.get_device_name(0), x.item())")
    }
}

function Find-Frontend {
    <# 仅识别项目内 package.json，避免把 Python 组件或外部目录当成前端。 #>
    param($Settings)
    $candidates = if ($Settings.frontend_dir -eq 'auto') { @('.', 'frontend', 'web', 'apps/web') }
        else { @($Settings.frontend_dir) }
    foreach ($relative in $candidates) {
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Split('/\') -contains '..') {
            throw 'frontend_dir must be a project-relative path without ..'
        }
        $directory = [IO.Path]::GetFullPath((Join-Path $ProjectRootPath $relative))
        if (Test-Path -LiteralPath (Join-Path $directory 'package.json') -PathType Leaf) { return $directory }
    }
    if ($Settings.frontend_dir -ne 'auto') { throw 'No package.json in configured frontend_dir.' }
    return ''
}

function Install-Frontend {
    <# 有 npm 项目才安装，锁文件优先 npm ci；无前端时清楚提示跳过。 #>
    param([string]$Directory)
    if (-not $Directory) { Write-Host 'No frontend package.json yet; skipping npm dependencies.'; return }
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $npm -or -not $node) { throw 'Frontend found: install Node.js (including npm) and reopen the launcher.' }
    $nodeVersion = & $node.Source --version
    if ($LASTEXITCODE -ne 0) { throw 'Node.js version check failed.' }
    $npmVersion = & $npm.Source --version
    if ($LASTEXITCODE -ne 0) { throw 'npm version check failed.' }
    $files = @('package.json', 'package-lock.json', 'npm-shrinkwrap.json', '.npmrc') | ForEach-Object { Join-Path $Directory $_ }
    $fingerprint = Get-Fingerprint $files "$Directory|$nodeVersion|$npmVersion"
    if (-not $Reinstall -and (Test-Path -LiteralPath (Join-Path $Directory 'node_modules')) -and
        (Test-Path -LiteralPath $DeployPaths.NpmStamp) -and
        (Get-Content -LiteralPath $DeployPaths.NpmStamp -Raw).Trim() -eq $fingerprint) {
        Write-Host 'npm dependencies unchanged; reusing node_modules.'; return
    }
    Push-Location -LiteralPath $Directory
    try {
        if (Test-Path -LiteralPath $DeployPaths.NpmStamp) { Remove-Item -LiteralPath $DeployPaths.NpmStamp }
        $action = if ((Test-Path -LiteralPath 'package-lock.json') -or (Test-Path -LiteralPath 'npm-shrinkwrap.json')) { 'ci' } else { 'install' }
        Invoke-Checked $npm.Source @($action, '--no-audit', '--no-fund')
        # npm install 首轮可能生成 lock，使用执行后的指纹。
        $fingerprint = Get-Fingerprint $files "$Directory|$nodeVersion|$npmVersion"
        [IO.File]::WriteAllText($DeployPaths.NpmStamp, $fingerprint)
    } finally { Pop-Location }
}

try {
    if ($ChooseBackend -and $NonInteractive) { throw 'ChooseBackend and NonInteractive cannot be combined.' }
    $hardware = Get-ComputerHardware
    Show-ComputerHardware $hardware
    $settings = Read-DeployConfig
    if (-not $NonInteractive -and ($ChooseBackend -or ($settings.choose_backend -and -not $SetupOnly -and -not $Plan))) {
        $backend = Select-DeploymentBackend $hardware $settings.gan_backend
        if ($backend -eq 'cancel') { Write-Host '已取消部署。'; exit 0 }
        $settings.gan_backend = $backend
        if (-not $Plan) {
            $selectedConfig = if ($ConfigPath) { $ConfigPath } else { $DeployPaths.Config }
            [IO.File]::WriteAllText($selectedConfig, ($settings | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        }
    }
    $frontend = if ($settings.install_npm) { Find-Frontend $settings } else { '' }
    $python = Resolve-Python $settings
    if ($Plan) {
        Write-Host "[PLAN] Environment=$($settings.environment_mode); GAN=$($settings.gan_backend); port=$($settings.port)"
        Write-Host "[PLAN] Frontend=$frontend; npm=$($settings.install_npm); vector=$($settings.prepare_vector_model)"
        exit 0
    }
    New-Item -ItemType Directory -Path $DeployPaths.State -Force | Out-Null
    try { $setupLock = [IO.File]::Open($DeployPaths.SetupLock, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another deployment is running; wait for it to finish.' }
    try {
        Install-Python $settings $python
        if ($settings.install_npm) { Install-Frontend $frontend }
        else { Write-Host 'npm installation disabled by local config.' }
    } finally { $setupLock.Dispose() }
    if ($SetupOnly) { Write-Host 'Environment setup completed.'; exit 0 }
    Write-Host 'Starting platform. Keep this window open; press Ctrl+C to stop services.'
    $serviceArguments = @('-m', 'app.local_service', '--port', [string]$settings.port)
    if ($frontend -and [IO.Path]::GetFullPath($frontend) -eq [IO.Path]::GetFullPath($DeployPaths.PlatformFrontend)) {
        $serviceArguments += '--with-frontend'
    }
    Invoke-Checked $python $serviceArguments
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
