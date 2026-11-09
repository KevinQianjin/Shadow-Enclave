param(
    [string]$DataDirectory = (Join-Path $env:ProgramData 'ShadowEnclave\V4'),
    [switch]$Repair
)
$ErrorActionPreference = 'Stop'
$package = Join-Path $PSScriptRoot 'release\ShadowEnclaveV4-package.zip'
if (-not (Test-Path -LiteralPath $package)) { throw 'Build the V4 release package first.' }
$package = [IO.Path]::GetFullPath($package)
$checksum = Join-Path $PSScriptRoot 'release\SHA256.txt'
if (-not (Test-Path -LiteralPath $checksum -PathType Leaf)) { throw 'The package checksum file is missing.' }
$expected = (Get-Content -LiteralPath $checksum -Raw).Trim()
$actual = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash
if ($expected -notmatch '^([a-f0-9]{64})  ShadowEnclaveV4-package\.zip$' -or $Matches[1] -ine $actual) {
    throw 'Package checksum mismatch. Extract a fresh copy of the release bundle.'
}
if ($Repair -and -not $PSBoundParameters.ContainsKey('DataDirectory')) {
    $config = Join-Path $env:ProgramFiles 'Shadow Enclave V4\service.json'
    if (Test-Path -LiteralPath $config -PathType Leaf) {
        $DataDirectory = (Get-Content -LiteralPath $config -Raw | ConvertFrom-Json).data_directory
    }
}
$dataRoot = [IO.Path]::GetFullPath($DataDirectory)
if ($dataRoot.StartsWith('\\') -or $dataRoot.Length -le 4) { throw 'Choose a dedicated local data directory.' }
$plan = @{ package = $package; data = $dataRoot; repair = [bool]$Repair; sha256 = $actual } | ConvertTo-Json -Compress
$encodedPlan = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($plan))
$setup = @'
$ErrorActionPreference = 'Stop'
$plan = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($planText)) | ConvertFrom-Json
$target = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Shadow Enclave V4'
$dataRoot = [IO.Path]::GetFullPath($plan.data)
$serviceName = 'ShadowEnclaveV4'
$configPath = Join-Path $target 'service.json'
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing -and -not $plan.repair) { throw 'V4 is already installed. Use -Repair to replace binaries while retaining data.' }
if ($existing) {
    $previous = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($dataRoot -cne $previous.data_directory) { throw 'Repair cannot relocate protected service data.' }
}
elseif ((Test-Path -LiteralPath $target) -or ((Test-Path -LiteralPath $dataRoot) -and @(Get-ChildItem -LiteralPath $dataRoot -Force).Count)) {
    throw 'The installation or data directory is not empty. Choose a dedicated location.'
}
foreach ($path in @($target, $dataRoot, $plan.package)) {
    $current = [IO.Path]::GetFullPath($path)
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Reparse points are not allowed in installation paths.'
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}
$admins = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$users = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')
$localService = [Security.Principal.SecurityIdentifier]::new('S-1-5-19')
function ProtectFolder($path, $reader, $writer) {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($admins)
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    foreach ($identity in @($admins, $system)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', $inherit, 'None', 'Allow'))
    }
    foreach ($identity in $reader) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'ReadAndExecute', $inherit, 'None', 'Allow'))
    }
    foreach ($identity in $writer) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'Modify', $inherit, 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $path -AclObject $acl
}
if ($existing -and (Get-Process -Name ShadowEnclaveV4 -ErrorAction SilentlyContinue)) {
    throw 'Close Shadow Enclave V4 before updating it.'
}
if ($existing) { Stop-Service -Name $serviceName -ErrorAction Stop }
New-Item -ItemType Directory -Path $target -Force | Out-Null
ProtectFolder $target @($users, $localService) @()
$archivePath = Join-Path $target 'installation-package.zip'
Copy-Item -LiteralPath $plan.package -Destination $archivePath -Force
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -cne $plan.sha256) { throw 'Package integrity check failed.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    foreach ($entry in $archive.Entries) {
        $relative = $entry.FullName.Replace('/', '\')
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Split('\') -contains '..' -or $relative.Contains(':')) {
            throw 'Invalid package path.'
        }
        $destination = [IO.Path]::GetFullPath((Join-Path $target $relative))
        if (-not $destination.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid package path.' }
        if (-not $entry.Name) { New-Item -ItemType Directory -Path $destination -Force | Out-Null; continue }
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
    }
}
finally {
    $archive.Dispose()
    Remove-Item -LiteralPath $archivePath
}
if (-not $existing) {
    New-Service -Name $serviceName -DisplayName 'Shadow Enclave V4 Broker' -BinaryPathName ('"' + (Join-Path $target 'BrokerService.exe') + '"') -StartupType Manual | Out-Null
}
$sc = Join-Path $env:WINDIR 'System32\sc.exe'
& $sc config $serviceName obj= 'NT AUTHORITY\LocalService' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not set the service account.' }
& $sc sidtype $serviceName unrestricted | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not enable the service SID.' }
& $sc privs $serviceName SeChangeNotifyPrivilege/SeImpersonatePrivilege | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict service privileges.' }
$serviceSid = ([Security.Principal.NTAccount]::new('NT SERVICE', $serviceName)).Translate([Security.Principal.SecurityIdentifier])
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
ProtectFolder $dataRoot @() @($serviceSid)
@{ data_directory = $dataRoot; version = 4 } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
Start-Service -Name $serviceName
(Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
& $sc config $serviceName start= delayed-auto | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not configure automatic startup.' }
'V4 service installed. Open the desktop and enroll Windows Hello.'
'@
$bootstrap = '$planText = ''' + $encodedPlan + '''' + [Environment]::NewLine + $setup
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
Write-Output 'Windows administrator approval is required to install the V4 service and protect its storage.'
$process = Start-Process -FilePath $powershell -ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand',$encoded -Verb RunAs -WindowStyle Hidden -PassThru
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "Installation did not complete (exit $($process.ExitCode))." }
Write-Output 'V4 installed. V3 files and data were not changed.'
