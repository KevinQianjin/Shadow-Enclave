@echo off
if not exist "%ProgramFiles%\Shadow Enclave V4\desktop\ShadowEnclaveV4.exe" (
    echo Shadow Enclave V4 is not installed. Run Install V4.cmd first.
    pause
    exit /b 1
)
start "" "%ProgramFiles%\Shadow Enclave V4\desktop\ShadowEnclaveV4.exe"
