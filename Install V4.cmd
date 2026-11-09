@echo off
if exist "%ProgramFiles%\Shadow Enclave V4\service.json" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-V4.ps1" -Repair %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-V4.ps1" %*
)
pause
