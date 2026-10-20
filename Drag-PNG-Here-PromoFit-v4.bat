@echo off
setlocal

if "%~1"=="" (
    echo Drag a transparent PNG file onto this BAT file.
    echo.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0PNG-to-FIFA21-DDS-PromoFit-v4.ps1" "%~1"

endlocal
