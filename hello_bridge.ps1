param([Parameter(Mandatory=$true)][string]$Name, [switch]$Create, [long]$OwnerHwnd = 0)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$manager = [Windows.Security.Credentials.KeyCredentialManager, Windows.Security.Credentials, ContentType=WindowsRuntime]
$options = [Windows.Security.Credentials.KeyCredentialCreationOption, Windows.Security.Credentials, ContentType=WindowsRuntime]
$retrieval = [Windows.Security.Credentials.KeyCredentialRetrievalResult, Windows.Security.Credentials, ContentType=WindowsRuntime]
$resultType = [Windows.Security.Credentials.KeyCredentialOperationResult, Windows.Security.Credentials, ContentType=WindowsRuntime]
$adapter = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($operation, [type]$kind) {
    $adapter.MakeGenericMethod($kind).Invoke($null, @($operation)).GetAwaiter().GetResult()
}
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class LabWinRtBuffer
{
    [DllImport("Windows.UI.dll", ExactSpelling = true)]
    private static extern int GetWindowIdFromWindow(IntPtr hwnd, out ulong id);

    public static ulong WindowId(long hwnd)
    {
        try
        {
            ulong id;
            return GetWindowIdFromWindow(new IntPtr(hwnd), out id) == 0 ? id : 0;
        }
        catch (EntryPointNotFoundException) { return 0; }
        catch (DllNotFoundException) { return 0; }
    }

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int ReadLength(IntPtr instance, out uint length);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int ReadPointer(IntPtr instance, out IntPtr data);

    private static IntPtr Slot(IntPtr instance, int index)
    {
        return Marshal.ReadIntPtr(Marshal.ReadIntPtr(instance), index * IntPtr.Size);
    }

    public static string ToBase64(object value)
    {
        IntPtr identity = Marshal.GetIUnknownForObject(value);
        IntPtr buffer = IntPtr.Zero;
        IntPtr access = IntPtr.Zero;
        try
        {
            var bufferId = new Guid("905a0fe0-bc53-11df-8c49-001e4fc686da");
            var accessId = new Guid("905a0fef-bc53-11df-8c49-001e4fc686da");
            Marshal.ThrowExceptionForHR(Marshal.QueryInterface(identity, ref bufferId, out buffer));
            Marshal.ThrowExceptionForHR(Marshal.QueryInterface(identity, ref accessId, out access));
            var getLength = (ReadLength)Marshal.GetDelegateForFunctionPointer(Slot(buffer, 7), typeof(ReadLength));
            var getData = (ReadPointer)Marshal.GetDelegateForFunctionPointer(Slot(access, 3), typeof(ReadPointer));
            uint length;
            IntPtr data;
            Marshal.ThrowExceptionForHR(getLength(buffer, out length));
            Marshal.ThrowExceptionForHR(getData(access, out data));
            if (length == 0 || length > 4096 || data == IntPtr.Zero)
                throw new InvalidOperationException("Unexpected public-key buffer size.");
            byte[] bytes = new byte[(int)length];
            Marshal.Copy(data, bytes, 0, bytes.Length);
            return Convert.ToBase64String(bytes);
        }
        finally
        {
            if (access != IntPtr.Zero) Marshal.Release(access);
            if (buffer != IntPtr.Zero) Marshal.Release(buffer);
            Marshal.Release(identity);
        }
    }
}
'@
$created = $false
$committed = $false
try {
    if (-not $Name.StartsWith('ShadowEnclave.V4.', [StringComparison]::Ordinal)) { throw 'Invalid V4 credential name.' }
    $window = $null
    if ($OwnerHwnd) {
        $id = [LabWinRtBuffer]::WindowId($OwnerHwnd)
        if ($id) {
            $window = [Activator]::CreateInstance([Windows.UI.WindowId, Windows.UI, ContentType=WindowsRuntime])
            $window.Value = $id
        }
    }
    if ($Create) {
        if ($null -ne $window -and $manager.GetMethod('RequestCreateForWindowAsync')) {
            $key = Await ($manager::RequestCreateForWindowAsync($window, $Name, $options::FailIfExists)) $retrieval
        } else {
            $key = Await ($manager::RequestCreateAsync($Name, $options::FailIfExists)) $retrieval
        }
        $created = $key.Status.ToString() -eq 'Success'
    } else {
        $key = Await ($manager::OpenAsync($Name)) $retrieval
    }
    if ($key.Status.ToString() -ne 'Success') { throw ('Windows Hello: ' + $key.Status) }
    @{ public_der = [LabWinRtBuffer]::ToBase64($key.Credential.RetrievePublicKey()) } | ConvertTo-Json -Compress
    $request = [Console]::ReadLine() | ConvertFrom-Json
    $bytes = [Convert]::FromBase64String($request.challenge)
    if ($bytes.Length -gt 2048) { throw 'Invalid challenge.' }
    $buffer = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::AsBuffer($bytes)
    if ($null -ne $window -and $key.Credential.PSObject.Methods['RequestSignForWindowAsync']) {
        $proof = Await ($key.Credential.RequestSignForWindowAsync($window, $buffer)) $resultType
    } else {
        $proof = Await ($key.Credential.RequestSignAsync($buffer)) $resultType
    }
    if ($proof.Status.ToString() -ne 'Success') { throw ('Windows Hello: ' + $proof.Status) }
    @{ signature = [LabWinRtBuffer]::ToBase64($proof.Result) } | ConvertTo-Json -Compress
    # On ambiguous broker acknowledgement preserve a newly created key. Do not strand enrolled data.
    $ack = [Console]::ReadLine()
    $committed = $ack -ne 'abort'
} catch {
    @{ error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($created -and -not $committed) {
        $action = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
        } | Select-Object -First 1
        $action.Invoke($null, @($manager::DeleteAsync($Name))).GetAwaiter().GetResult()
    }
}
