@echo off
setlocal EnableExtensions
set "LAUNCHER=%~dp0..\Launch-LocalFUT.cmd"

if not exist "%LAUNCHER%" (
    echo ERROR: The unified Local FUT launcher was not found.
    exit /b 2
)

call "%LAUNCHER%" %*
exit /b %ERRORLEVEL%
