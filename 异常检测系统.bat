@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ================================================================
REM 异常检测系统 Windows 一键启动脚本
REM
REM 使用方法：
REM   1. 直接双击“异常检测系统.bat”。
REM   2. 或在 CMD / PowerShell 中执行：异常检测系统.bat
REM   3. 停止服务：在本窗口按 Ctrl+C，然后按提示确认。
REM
REM 可选端口：
REM   set BACKEND_PORT=9000
REM   set FRONTEND_PORT=5200
REM   异常检测系统.bat
REM
REM 若指定端口已被占用，脚本会自动向后寻找空闲端口，
REM 不会强制结束原来占用端口的程序。
REM ================================================================

REM 无论从桌面、资源管理器还是其他目录启动，都进入脚本所在目录。
cd /d "%~dp0"
if errorlevel 1 (
    echo [错误] 无法进入项目目录：%~dp0
    pause
    exit /b 1
)

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Windows PowerShell。
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1"
set "SYSTEM_EXIT_CODE=%ERRORLEVEL%"

if not "%SYSTEM_EXIT_CODE%"=="0" (
    echo.
    echo 启动器异常结束，错误代码：%SYSTEM_EXIT_CODE%
    echo 请查看项目 logs 目录中的日志。
    pause
)

exit /b %SYSTEM_EXIT_CODE%
