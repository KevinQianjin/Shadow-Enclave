"""Native Windows TPM key wrapping and Windows Hello consent."""

import ctypes
import hashlib
import sys
import time
import uuid
from contextlib import contextmanager

_PROVIDER = "Microsoft Platform Crypto Provider"
_KEY_NAME = "Shadow Enclave V4 Device Binding"
_SILENT = 0x40
_OAEP = 0x04
_HARDWARE = 0x01
_NOT_FOUND = (0x80090011, 0x80090016)
_EXISTS = 0x8009000F
_TPM_UNAVAILABLE = "A TPM 2.0 hardware key is unavailable on this PC."
_KEY_UNAVAILABLE = "This carrier requires the original Windows account and TPM key."

_PTR = ctypes.c_void_p
_PTR_OUT = ctypes.POINTER(_PTR)
_U32 = ctypes.c_uint32
_U32_OUT = ctypes.POINTER(_U32)
_HRESULT = ctypes.c_int32
_I32_OUT = ctypes.POINTER(ctypes.c_int32)
_WSTR = ctypes.c_wchar_p


class DeviceBindingError(ValueError):
    """The original TPM key is unavailable, or the protected data is invalid."""


def _api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype = result
    fn.argtypes = args
    return fn


def _ncrypt(dll, name, *args):
    return _api(dll, "NCrypt" + name, _U32, *args)


def _checked(status, message):
    if status:
        raise DeviceBindingError(message)


def _get_dword(dll, handle, name):
    value, written = _U32(), _U32()
    get = _ncrypt(dll, "GetProperty", _PTR, _WSTR, _PTR, _U32, _U32_OUT, _U32)
    status = get(handle, name, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(written), 0)
    if status or written.value != ctypes.sizeof(value):
        raise DeviceBindingError(_TPM_UNAVAILABLE)
    return value.value


def _set_dword(dll, handle, name, value):
    data = _U32(value)
    setter = _ncrypt(dll, "SetProperty", _PTR, _WSTR, _PTR, _U32, _U32)
    _checked(setter(handle, name, ctypes.byref(data), ctypes.sizeof(data), 0), _TPM_UNAVAILABLE)


@contextmanager
def _provider():
    if sys.platform != "win32":
        raise DeviceBindingError(_TPM_UNAVAILABLE)
    dll = ctypes.WinDLL("ncrypt.dll")
    provider = _PTR()
    open_provider = _ncrypt(dll, "OpenStorageProvider", _PTR_OUT, _WSTR, _U32)
    free = _ncrypt(dll, "FreeObject", _PTR)
    _checked(open_provider(ctypes.byref(provider), _PROVIDER, 0), _TPM_UNAVAILABLE)
    try:
        if not _get_dword(dll, provider, "Impl Type") & _HARDWARE:
            raise DeviceBindingError(_TPM_UNAVAILABLE)
        yield dll, provider
    finally:
        free(provider)


def tpm_available():
    if sys.platform != "win32":
        return False
    try:
        info = (_U32 * 4)()
        device_info = _api(ctypes.WinDLL("tbs.dll"), "Tbsi_GetDeviceInfo", _U32, _U32, _PTR)
        if device_info(ctypes.sizeof(info), ctypes.byref(info)) or info[1] != 2:
            return False
        with _provider() as (dll, provider):
            supports = _ncrypt(dll, "IsAlgSupported", _PTR, _WSTR, _U32)
            return supports(provider, "RSA", 0) == 0
    except (DeviceBindingError, OSError, AttributeError):
        return False


@contextmanager
def _device_key(*, create):
    with _provider() as (dll, provider):
        key = _PTR()
        free = _ncrypt(dll, "FreeObject", _PTR)
        open_key = _ncrypt(dll, "OpenKey", _PTR, _PTR_OUT, _WSTR, _U32, _U32)
        try:
            status = open_key(provider, ctypes.byref(key), _KEY_NAME, 0, _SILENT)
            if create and status in _NOT_FOUND:
                make = _ncrypt(dll, "CreatePersistedKey", _PTR, _PTR_OUT, _WSTR, _WSTR, _U32, _U32)
                status = make(provider, ctypes.byref(key), "RSA", _KEY_NAME, 0, 0)
                if status == _EXISTS:
                    status = open_key(provider, ctypes.byref(key), _KEY_NAME, 0, _SILENT)
                else:
                    _checked(status, _TPM_UNAVAILABLE)
                    _set_dword(dll, key, "Length", 2048)
                    _set_dword(dll, key, "Export Policy", 0)
                    _set_dword(dll, key, "Key Usage", 1)  # NCRYPT_ALLOW_DECRYPT_FLAG
                    finalize = _ncrypt(dll, "FinalizeKey", _PTR, _U32)
                    _checked(finalize(key, _SILENT), _TPM_UNAVAILABLE)
            _checked(status, _TPM_UNAVAILABLE if create else _KEY_UNAVAILABLE)
            if _get_dword(dll, key, "Length") != 2048 or _get_dword(dll, key, "Export Policy") != 0:
                raise DeviceBindingError(_KEY_UNAVAILABLE)
            yield dll, key
        finally:
            if key:
                free(key)


def _key_id(dll, key):
    export = _ncrypt(dll, "ExportKey", _PTR, _PTR, _WSTR, _PTR, _PTR, _U32, _U32_OUT, _U32)
    blob = ctypes.create_string_buffer(512)
    written = _U32()
    _checked(export(key, None, "RSAPUBLICBLOB", None, blob, len(blob), ctypes.byref(written), 0), _KEY_UNAVAILABLE)
    if not 0 < written.value <= len(blob):
        raise DeviceBindingError(_KEY_UNAVAILABLE)
    return hashlib.sha256(blob.raw[:written.value]).digest()[:16]


class _OaepPadding(ctypes.Structure):
    _fields_ = [("algorithm", _WSTR), ("label", _PTR), ("label_size", _U32)]


def _oaep(dll, operation, key, data):
    transform = _ncrypt(dll, operation, _PTR, _PTR, _U32, _PTR, _PTR, _U32, _U32_OUT, _U32)
    buffer = ctypes.create_string_buffer(256)
    count = _U32()
    padding = _OaepPadding("SHA256", None, 0)
    source = ctypes.create_string_buffer(data)
    status = transform(key, source, len(data), ctypes.byref(padding), buffer, len(buffer),
                       ctypes.byref(count), _OAEP | _SILENT)
    if status or not 0 < count.value <= len(buffer):
        raise DeviceBindingError(_KEY_UNAVAILABLE)
    return buffer.raw[:count.value]


def wrap_device_secret(secret):
    if len(secret) != 32 or not tpm_available():
        raise DeviceBindingError(_TPM_UNAVAILABLE)
    with _device_key(create=True) as (dll, key):
        key_id = _key_id(dll, key)
        wrapped = _oaep(dll, "Encrypt", key, secret)
        if len(wrapped) != 256:
            raise DeviceBindingError(_TPM_UNAVAILABLE)
        return key_id, wrapped


def unwrap_device_secret(key_id, wrapped):
    if len(key_id) != 16 or len(wrapped) != 256 or not tpm_available():
        raise DeviceBindingError(_KEY_UNAVAILABLE)
    with _device_key(create=False) as (dll, key):
        if _key_id(dll, key) != key_id:
            raise DeviceBindingError(_KEY_UNAVAILABLE)
        secret = _oaep(dll, "Decrypt", key, wrapped)
        if len(secret) != 32:
            raise DeviceBindingError(_KEY_UNAVAILABLE)
        return secret


_HELLO_CLASS = "Windows.Security.Credentials.UI.UserConsentVerifier"
_STATICS = uuid.UUID("af4f3f91-564c-4ddc-b8b5-973447627c65")
_INTEROP = uuid.UUID("39e050c3-4e74-441a-8dc0-b81104df949c")
_ASYNC_INFO = uuid.UUID("00000036-0000-0000-c000-000000000046")
_WINRT_NAMESPACE = uuid.UUID("11f47ad5-7b73-42c0-abae-878b1e16adee")
_VERIFICATION_OP = uuid.uuid5(
    _WINRT_NAMESPACE, "pinterface({9fc2b0bb-e446-44e2-aa61-9cab8f636af2};enum("
    "Windows.Security.Credentials.UI.UserConsentVerificationResult;i4))"
)
_HELLO_ERROR = "Windows Hello is unavailable for this Windows session."
_NO_INTERFACE = 0x80004002
_NOT_IMPLEMENTED = 0x80004001


class _WinRTError(OSError):
    def __init__(self, status):
        super().__init__(_HELLO_ERROR)
        self.status = status & 0xFFFFFFFF
_HELLO_STATUS = {
    0: "Windows Hello is available.",
    1: "Windows Hello found no available verification device.",
    2: "Set up Windows Hello in Windows sign-in options.",
    3: "Windows Hello is disabled by policy.",
    4: "Windows Hello is busy. Try again.",
    5: "Windows Hello could not verify you after several attempts.",
    6: "Windows Hello verification was canceled.",
}


def _guid(value):
    return (ctypes.c_ubyte * 16).from_buffer_copy(value.bytes_le)


def _method(pointer, index, result, *args):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(_PTR)))[0]
    return ctypes.WINFUNCTYPE(result, _PTR, *args)(table[index])


def _hr(status):
    if status < 0:
        raise _WinRTError(status)


def _release(pointer):
    if pointer:
        _method(pointer, 2, _U32)(pointer)


@contextmanager
def _winrt():
    if sys.platform != "win32":
        raise OSError(_HELLO_ERROR)
    dll = ctypes.WinDLL("combase.dll")
    status = _api(dll, "RoInitialize", _HRESULT, _U32)(1)
    # Use the current apartment if the calling thread has already chosen one.
    if status != -2147417850:  # RPC_E_CHANGED_MODE
        _hr(status)
    try:
        yield dll
    finally:
        if status >= 0:
            _api(dll, "RoUninitialize", None)()


@contextmanager
def _hstring(dll, value):
    out = _PTR()
    create = _api(dll, "WindowsCreateString", _HRESULT, _WSTR, _U32, _PTR_OUT)
    _hr(create(value, len(value.encode("utf-16-le")) // 2, ctypes.byref(out)))
    try:
        yield out
    finally:
        _api(dll, "WindowsDeleteString", _HRESULT, _PTR)(out)


@contextmanager
def _factory(dll, interface):
    out = _PTR()
    with _hstring(dll, _HELLO_CLASS) as name:
        get = _api(dll, "RoGetActivationFactory", _HRESULT, _PTR, _PTR, _PTR_OUT)
        _hr(get(name, _guid(interface), ctypes.byref(out)))
    if not out:
        raise OSError(_HELLO_ERROR)
    try:
        yield out
    finally:
        _release(out)


def _async_result(operation, timeout):
    if not operation:
        raise OSError(_HELLO_ERROR)
    info = _PTR()
    try:
        _hr(_method(operation, 0, _HRESULT, _PTR, _PTR_OUT)(operation, _guid(_ASYNC_INFO), ctypes.byref(info)))
        if not info:
            raise OSError(_HELLO_ERROR)
        deadline = time.monotonic() + timeout
        status, result = ctypes.c_int32(), ctypes.c_int32()
        while True:
            _hr(_method(info, 7, _HRESULT, _I32_OUT)(info, ctypes.byref(status)))
            if status.value == 1:
                _hr(_method(operation, 8, _HRESULT, _I32_OUT)(operation, ctypes.byref(result)))
                return result.value
            if status.value == 2:
                return 6
            if status.value != 0:
                raise OSError(_HELLO_ERROR)
            if time.monotonic() >= deadline:
                _method(info, 9, _HRESULT)(info)
                raise OSError("Windows Hello verification timed out.")
            time.sleep(0.05)
    finally:
        _release(info)
        _release(operation)


def hello_status():
    try:
        with _winrt() as dll, _factory(dll, _STATICS) as factory:
            operation = _PTR()
            _hr(_method(factory, 6, _HRESULT, _PTR_OUT)(factory, ctypes.byref(operation)))
            status = _async_result(operation, 5)
    except (OSError, AttributeError):
        return False, _HELLO_ERROR
    return status == 0, _HELLO_STATUS.get(status, _HELLO_ERROR)


def _request_hello(dll, hwnd, message):
    operation = _PTR()
    try:
        with _factory(dll, _INTEROP) as factory:
            request = _method(factory, 6, _HRESULT, _PTR, _PTR, _PTR, _PTR_OUT)
            _hr(request(factory, hwnd, message, _guid(_VERIFICATION_OP), ctypes.byref(operation)))
    except _WinRTError as exc:
        if exc.status not in (_NO_INTERFACE, _NOT_IMPLEMENTED):
            raise
        # Some Windows builds expose the Hello statics but omit the window-owned interface.
        with _factory(dll, _STATICS) as factory:
            request = _method(factory, 7, _HRESULT, _PTR, _PTR_OUT)
            _hr(request(factory, message, ctypes.byref(operation)))
    return _async_result(operation, 90)


def require_hello(hwnd, *, reason="Verify to continue in Shadow Enclave"):
    available, status = hello_status()
    if not hwnd or not available:
        raise ValueError(status if not available else _HELLO_ERROR)
    try:
        with _winrt() as dll, _hstring(dll, reason) as message:
            status = _request_hello(dll, hwnd, message)
    except (OSError, AttributeError) as exc:
        raise ValueError(str(exc) or _HELLO_ERROR) from exc
    if status != 0:
        raise ValueError(_HELLO_STATUS.get(status, _HELLO_ERROR))
