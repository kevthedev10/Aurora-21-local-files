@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "LAUNCHER=%SCRIPT_DIR%Project21-LocalFUT-Launcher.exe"
set "BUILDER=%SCRIPT_DIR%Build-FIFA21-Launcher.ps1"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%BUILDER%" -IfNeeded
if errorlevel 1 (
    echo.
    echo ERROR: The launcher could not be built.
    pause
    exit /b 1
)

start "" "%LAUNCHER%"
exit /b 0
