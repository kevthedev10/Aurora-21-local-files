@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
set "LAUNCHER=%SCRIPT_DIR%Launch-LocalFUT.ps1"

if not exist "%LAUNCHER%" (
    echo ERROR: Launch-LocalFUT.ps1 was not found beside this batch file.
    exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Local FUT launch failed with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
