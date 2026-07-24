# 异常检测系统 Windows 启动器内部实现。
# 普通用户请双击项目根目录的“异常检测系统.bat”，无需单独运行本文件。
# 本启动器负责：选择空闲端口、启动前后端、等待就绪、打开浏览器及退出清理。

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectDir
$BackendDir = Join-Path $ProjectDir "fastapi-app"
$FrontendDir = Join-Path $ProjectDir "vue"
$LogDir = Join-Path $ProjectDir "logs"
$BackendStartPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 9090 }
$FrontendStartPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5173 }
$StartupTimeout = if ($env:STARTUP_TIMEOUT) { [int]$env:STARTUP_TIMEOUT } else { 90 }
$BackendProcess = $null
$FrontendProcess = $null

function Get-FreeTcpPort {
    param([Parameter(Mandatory = $true)][int]$StartPort)

    for ($port = $StartPort; $port -le 65535; $port++) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new(
                [System.Net.IPAddress]::Loopback,
                $port
            )
            $listener.Start()
            return $port
        }
        catch {
            continue
        }
        finally {
            if ($null -ne $listener) {
                $listener.Stop()
            }
        }
    }
    throw "没有可用的 TCP 端口。"
}

function Get-CommandPath {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }
    return $null
}

function Restore-EnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][string]$Value
    )

    if ($null -eq $Value) {
        Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item "Env:$Name" $Value
    }
}

function Wait-ServiceReady {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string[]]$LogFiles
    )

    $deadline = (Get-Date).AddSeconds($StartupTimeout)
    while ((Get-Date) -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            Write-Host ""
            Write-Host "$Name 进程意外退出，最近的日志：" -ForegroundColor Red
            foreach ($logFile in $LogFiles) {
                if (Test-Path -LiteralPath $logFile) {
                    Get-Content -LiteralPath $logFile -Tail 30
                }
            }
            throw "$Name 未能正常启动。"
        }

        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 |
                Out-Null
            Write-Host "$Name 已就绪。" -ForegroundColor Green
            return
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    Write-Host ""
    Write-Host "等待 $Name 就绪超时，最近的日志：" -ForegroundColor Red
    foreach ($logFile in $LogFiles) {
        if (Test-Path -LiteralPath $logFile) {
            Get-Content -LiteralPath $logFile -Tail 30
        }
    }
    throw "等待 $Name 就绪超时。"
}

function Stop-ProcessTree {
    param([AllowNull()][System.Diagnostics.Process]$Process)

    if ($null -eq $Process) {
        return
    }
    $Process.Refresh()
    if (-not $Process.HasExited) {
        & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
    }
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $BackendDir "main.py"))) {
        throw "未找到后端入口：fastapi-app\main.py"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "package.json"))) {
        throw "未找到前端入口：vue\package.json"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
        throw "前端依赖尚未安装，请先在 vue 目录运行 npm install。"
    }

    $PythonPrefix = @()
    $PythonExe = $null
    $VirtualEnvPython = @(
        (Join-Path $ProjectDir ".venv\Scripts\python.exe"),
        (Join-Path $ProjectDir "venv\Scripts\python.exe")
    )
    foreach ($candidate in $VirtualEnvPython) {
        if (Test-Path -LiteralPath $candidate) {
            $PythonExe = $candidate
            break
        }
    }
    if ($null -eq $PythonExe) {
        $PythonExe = Get-CommandPath @("python.exe", "python3.exe")
    }
    if ($null -eq $PythonExe) {
        $PythonExe = Get-CommandPath @("py.exe")
        if ($null -ne $PythonExe) {
            $PythonPrefix = @("-3")
        }
    }
    if ($null -eq $PythonExe) {
        throw "未找到 Python 3。"
    }

    $NodeExe = Get-CommandPath @("node.exe")
    if ($null -eq $NodeExe) {
        throw "未找到 Node.js。"
    }
    $ViteScript = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path -LiteralPath $ViteScript)) {
        throw "未找到 Vite，请先在 vue 目录运行 npm install。"
    }

    & $PythonExe @PythonPrefix -c "import fastapi, uvicorn, tortoise" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "后端依赖尚未安装，请运行：python -m pip install -r requirements.txt"
    }

    $BackendPort = Get-FreeTcpPort $BackendStartPort
    $FrontendPort = Get-FreeTcpPort $FrontendStartPort
    if ($FrontendPort -eq $BackendPort) {
        $FrontendPort = Get-FreeTcpPort ($FrontendPort + 1)
    }
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

    $RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $PID
    $BackendLog = Join-Path $LogDir "backend-windows-$RunId.log"
    $BackendErrorLog = Join-Path $LogDir "backend-windows-$RunId.error.log"
    $FrontendLog = Join-Path $LogDir "frontend-windows-$RunId.log"
    $FrontendErrorLog = Join-Path $LogDir "frontend-windows-$RunId.error.log"

    Write-Host "项目目录：$ProjectDir"
    if ($BackendPort -ne $BackendStartPort) {
        Write-Host "后端端口 $BackendStartPort 已占用，自动改用 $BackendPort。"
    }
    if ($FrontendPort -ne $FrontendStartPort) {
        Write-Host "前端端口 $FrontendStartPort 已占用，自动改用 $FrontendPort。"
    }
    Write-Host "正在启动后端：http://127.0.0.1:$BackendPort"
    Write-Host "正在启动前端：http://127.0.0.1:$FrontendPort"

    $OldCors = [Environment]::GetEnvironmentVariable("CORS_ALLOWED_ORIGINS", "Process")
    $env:CORS_ALLOWED_ORIGINS =
        "http://localhost:$FrontendPort,http://127.0.0.1:$FrontendPort"
    try {
        $BackendArguments = @($PythonPrefix) + @(
            "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1",
            "--port", "$BackendPort"
        )
        $BackendProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList $BackendArguments `
            -WorkingDirectory $BackendDir `
            -RedirectStandardOutput $BackendLog `
            -RedirectStandardError $BackendErrorLog `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        Restore-EnvironmentVariable "CORS_ALLOWED_ORIGINS" $OldCors
    }

    $OldBaseUrl = [Environment]::GetEnvironmentVariable("VITE_BASE_URL", "Process")
    $env:VITE_BASE_URL = "http://127.0.0.1:$BackendPort"
    try {
        $FrontendProcess = Start-Process `
            -FilePath $NodeExe `
            -ArgumentList @(
                $ViteScript,
                "--host", "127.0.0.1",
                "--port", "$FrontendPort",
                "--strictPort"
            ) `
            -WorkingDirectory $FrontendDir `
            -RedirectStandardOutput $FrontendLog `
            -RedirectStandardError $FrontendErrorLog `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        Restore-EnvironmentVariable "VITE_BASE_URL" $OldBaseUrl
    }

    Wait-ServiceReady `
        -Name "后端" `
        -Url "http://127.0.0.1:$BackendPort/" `
        -Process $BackendProcess `
        -LogFiles @($BackendLog, $BackendErrorLog)
    Wait-ServiceReady `
        -Name "前端" `
        -Url "http://127.0.0.1:$FrontendPort/" `
        -Process $FrontendProcess `
        -LogFiles @($FrontendLog, $FrontendErrorLog)

    $FrontendUrl = "http://127.0.0.1:$FrontendPort/"
    Write-Host ""
    Write-Host "异常检测系统已启动完成：$FrontendUrl" -ForegroundColor Green
    Write-Host "后端日志：$BackendLog"
    Write-Host "前端日志：$FrontendLog"
    Write-Host "按 Ctrl+C 可同时停止所有服务。"

    # Windows 会用用户配置的默认浏览器打开该 URL。
    Start-Process $FrontendUrl

    while (-not $BackendProcess.HasExited -and -not $FrontendProcess.HasExited) {
        Start-Sleep -Seconds 2
        $BackendProcess.Refresh()
        $FrontendProcess.Refresh()
    }
    throw "检测到服务进程退出，请检查 logs 目录中的日志。"
}
catch {
    Write-Host ""
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($null -ne $FrontendProcess -or $null -ne $BackendProcess) {
        Write-Host ""
        Write-Host "正在停止异常检测系统..."
        Stop-ProcessTree $FrontendProcess
        Stop-ProcessTree $BackendProcess
        Write-Host "前端和后端已停止。"
    }
}
