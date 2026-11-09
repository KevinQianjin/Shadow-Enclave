param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

& $Python -I -c "import struct, sys, tkinter; assert sys.platform == 'win32' and sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8 and sys.prefix != sys.base_prefix"
if ($LASTEXITCODE) { throw 'Use a virtual environment created with 64-bit CPython 3.14 with Tcl/Tk on Windows.' }

$build = Join-Path $PSScriptRoot 'build-v4'
New-Item -ItemType Directory -Path $build -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /warnaserror /optimize+ /target:exe /r:System.ServiceProcess.dll /r:System.Web.Extensions.dll "/out:$build\BrokerService.exe" BrokerService.cs
if ($LASTEXITCODE) { throw 'Native broker build failed.' }

$common = @('--noconfirm', '--clean', '--onedir', '--distpath', "$build\dist", '--workpath', "$build\work", '--specpath', $build)
& $Python -I -m PyInstaller @common --name EnclaveBackend --exclude-module enclave_broker --exclude-module enclave_desktop --exclude-module enclave_workflow service_backend.py
if ($LASTEXITCODE) { throw 'Service backend build failed.' }

& $Python -I -m PyInstaller @common --name ShadowEnclaveV4 --windowed --icon "$PSScriptRoot\shadow-enclave-mark.ico" --hidden-import soundfile --hidden-import tkinterdnd2 --collect-data tkinterdnd2 --add-data "$PSScriptRoot\shadow-enclave-mark.png;." --add-data "$PSScriptRoot\COPYRIGHT.txt;." launch_v4.py
if ($LASTEXITCODE) { throw 'Desktop build failed.' }

& $Python -I tools/package_release.py
if ($LASTEXITCODE) { throw 'Release packaging failed.' }
