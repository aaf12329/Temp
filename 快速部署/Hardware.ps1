<#
结构：Get-ComputerHardware → Show-ComputerHardware；Select-DeploymentBackend → 菜单选择。
调用：Start.ps1 显式聚合硬件读取与菜单；本文件被加载时不执行检测、安装或写文件。
数据流：本机 CIM / nvidia-smi → 硬件对象 → 展示文本、用户选定的 GAN 后端标识。
约束：显卡名称不等于 CUDA 可用，只有成功读取 NVIDIA 驱动才开放自动 GPU 候选。
#>

function Get-ComputerHardware {
    <# 尽力读取电脑配置；检测失败返回未知字段，不阻止 CPU 部署。 #>
    $result = [ordered]@{
        OS = [Environment]::OSVersion.VersionString
        CPU = '未能读取'
        MemoryGB = $null
        Graphics = @()
        Nvidia = @()
        NvidiaReady = $false
    }
    try {
        $cpu = @(Get-CimInstance Win32_Processor -OperationTimeoutSec 5 -ErrorAction Stop)
        $result.CPU = ($cpu | ForEach-Object { $_.Name.Trim() }) -join ' / '
        $computer = Get-CimInstance Win32_ComputerSystem -OperationTimeoutSec 5 -ErrorAction Stop
        $result.MemoryGB = [Math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
    } catch { Write-Host '部分 CPU / 内存信息未能读取，仍可选择 CPU 部署。' }
    try {
        $result.Graphics = @(Get-CimInstance Win32_VideoController -OperationTimeoutSec 5 -ErrorAction Stop |
            ForEach-Object { $_.Name })
    } catch { Write-Host '系统显卡列表未能读取，将继续检查 NVIDIA 驱动。' }
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($smi) {
        try {
            $rows = & $smi.Source '--query-gpu=name,memory.total,driver_version' '--format=csv,noheader,nounits' 2>$null
            if ($LASTEXITCODE -eq 0) {
                foreach ($line in $rows) {
                    $parts = $line -split ',\s*'
                    $memory = 0.0
                    if ($parts.Count -ge 3 -and [double]::TryParse($parts[1], [ref]$memory)) {
                        $result.Nvidia += [pscustomobject]@{
                            Name = $parts[0].Trim(); MemoryGB = [Math]::Round($memory / 1024, 1)
                            Driver = $parts[2].Trim()
                        }
                    }
                }
                $result.NvidiaReady = $result.Nvidia.Count -gt 0
            }
        } catch { Write-Host 'NVIDIA 驱动检测失败，GPU 安装选项暂不可用。' }
    }
    return [pscustomobject]$result
}

function Show-ComputerHardware {
    <# 显示本机配置与 GPU 初步检测结果；供 Start 入口调用。 #>
    param($Hardware)
    Write-Host "`n========== 当前电脑配置 =========="
    Write-Host "系统：$($Hardware.OS)"
    Write-Host "CPU ：$($Hardware.CPU)"
    $memory = if ($null -ne $Hardware.MemoryGB) { "$($Hardware.MemoryGB) GB" } else { '未能读取' }
    Write-Host "内存：$memory"
    $graphics = if ($Hardware.Graphics.Count) { $Hardware.Graphics -join ' / ' } else { '未能读取系统列表' }
    Write-Host "显卡：$graphics"
    foreach ($gpu in $Hardware.Nvidia) {
        Write-Host "NVIDIA：$($gpu.Name)；显存 $($gpu.MemoryGB) GB；驱动 $($gpu.Driver)"
    }
    if ($Hardware.NvidiaReady) {
        Write-Host 'GPU 状态：已检测到 NVIDIA 驱动；安装后还会验证实际 CUDA 运算。'
    } else { Write-Host 'GPU 状态：未确认可用 NVIDIA 驱动；当前可用 CPU 方案。' }
    Write-Host '=================================='
}

function Select-DeploymentBackend {
    <# 接受终端选择并返回后端；无驱动拒绝 GPU，原 JSON 训练参数不改变。 #>
    param($Hardware, [string]$Current = 'auto')
    $labels = @{
        auto = '保留已有框架（缺少则安装 PyTorch CPU）'
        pytorch = 'PyTorch CPU'
        'pytorch-cuda' = 'PyTorch NVIDIA GPU / CUDA'
        tensorflow = 'TensorFlow CPU'
        none = '暂不安装或修改 GAN 后端'
    }
    while ($true) {
        Write-Host "`n请选择本次部署的运行版本："
        Write-Host 'CPU/GPU 选择用于 WGAN 的 PyTorch 后端；检索和 XGBoost 仍按现有 CPU 配置运行。'
        Write-Host '  1. CPU 版本（PyTorch，通用）'
        $gpuStatus = if ($Hardware.NvidiaReady) { '驱动已检测到' } else { '不可用：未确认 NVIDIA 驱动' }
        Write-Host "  2. NVIDIA GPU 版本（PyTorch CUDA；$gpuStatus）"
        Write-Host '  3. 按本机硬件自动选择（有 NVIDIA 驱动选 GPU，否则 CPU）'
        Write-Host '  4. 高级选项（TensorFlow / 保留环境 / 暂不安装 GAN）'
        Write-Host '  0. 退出'
        Write-Host "回车沿用配置：$($labels[$Current])"
        $choice = Read-Host '输入编号'
        if ([string]::IsNullOrWhiteSpace($choice)) { $selected = $Current }
        elseif ($choice -eq '0') { return 'cancel' }
        elseif ($choice -eq '1') { $selected = 'pytorch' }
        elseif ($choice -eq '2') { $selected = 'pytorch-cuda' }
        elseif ($choice -eq '3') {
            $selected = if ($Hardware.NvidiaReady) { 'pytorch-cuda' } else { 'pytorch' }
        } elseif ($choice -eq '4') {
            Write-Host '  1. TensorFlow CPU（另一套训练框架）'
            Write-Host '  2. 保留已有框架，缺少时安装 PyTorch CPU'
            Write-Host '  3. 暂不安装或修改 GAN 后端（仍可采集、整理和使用已有后端）'
            Write-Host '  0. 返回'
            $advanced = Read-Host '高级选项编号'
            if ($advanced -eq '1') { $selected = 'tensorflow' }
            elseif ($advanced -eq '2') { $selected = 'auto' }
            elseif ($advanced -eq '3') { $selected = 'none' }
            else { continue }
        } else { Write-Host '请输入有效编号。'; continue }
        if ($selected -eq 'pytorch-cuda' -and -not $Hardware.NvidiaReady) {
            Write-Host '无法选择 CUDA：未确认可用 NVIDIA 驱动，请选择 CPU 或检查显卡驱动。'
            continue
        }
        Write-Host "已选择：$($labels[$selected])"
        Write-Host '仅选择安装后端；5000 轮等训练与绘图参数仍按原 JSON。'
        return $selected
    }
}
