"""Authenticated local client for the installed V4 broker."""
import base64
import ctypes
import json
import os
import struct
import subprocess
import threading
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pywintypes
import win32con
import win32file
import win32pipe
import win32security
import win32service
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PIPE = r"\\.\pipe\ShadowEnclaveV4"
ACCESS = 0x120083
LIMIT = 1_048_576
_LOCK = threading.RLock()
_SESSION = ExitStack()
_PIPE = None
_FRESH_HELLO = {"decrypt", "save_settings", "clear_api_key", "unenroll"}


class BrokerError(ValueError):
    pass


class SessionError(BrokerError):
    pass


def install_root():
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Shadow Enclave V4"


@contextmanager
def _connect():
    try:
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            service = win32service.OpenService(scm, "ShadowEnclaveV4", win32service.SERVICE_QUERY_STATUS)
            try:
                state = win32service.QueryServiceStatusEx(service)
            finally:
                win32service.CloseServiceHandle(service)
        finally:
            win32service.CloseServiceHandle(scm)
        if state["CurrentState"] != win32service.SERVICE_RUNNING:
            raise BrokerError("The V4 service is stopped. Start ShadowEnclaveV4 in Windows Services.")
        win32pipe.WaitNamedPipe(PIPE, 15000)
        pipe = win32file.CreateFile(PIPE, ACCESS, 0, None, win32con.OPEN_EXISTING, 0x120000, None)
        try:
            descriptor = win32security.GetSecurityInfo(pipe, win32security.SE_KERNEL_OBJECT, win32security.OWNER_SECURITY_INFORMATION)
            if win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner()) != "S-1-5-19":
                raise BrokerError("Unexpected broker pipe owner.")
            pid = ctypes.c_uint32()
            get_pid = ctypes.WinDLL("kernel32", use_last_error=True).GetNamedPipeServerProcessId
            get_pid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_pid.restype = ctypes.c_int
            if not get_pid(int(pipe), ctypes.byref(pid)) or pid.value != state["ProcessId"]:
                raise BrokerError("The pipe does not belong to the installed V4 service.")
        except BaseException:
            pipe.Close()
            raise
    except pywintypes.error as error:
        raise BrokerError("Install and start the V4 service using Install V4.cmd before continuing.") from error
    try:
        yield pipe
    finally:
        pipe.Close()


def _send(pipe, value):
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    if not 0 < len(encoded) <= LIMIT:
        raise BrokerError("Request too large.")
    win32file.WriteFile(pipe, struct.pack("<I", len(encoded)) + encoded)


def _read(pipe):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            _, block = win32file.ReadFile(pipe, size - len(data))
            if not block:
                raise SessionError("The V4 session closed. Verify Windows Hello to open it again.")
            data.extend(block)
        return bytes(data)
    size, = struct.unpack("<I", exact(4))
    if not 0 < size <= LIMIT:
        raise BrokerError("Invalid broker response.")
    response = json.loads(exact(size))
    if not isinstance(response, dict):
        raise BrokerError("Invalid broker response.")
    if response.get("session_closed"):
        raise SessionError("The V4 session closed. Verify Windows Hello to open it again.")
    if "error" in response:
        raise BrokerError(response["error"])
    return response


def status():
    with _LOCK:
        try:
            with _connect() as pipe:
                _send(pipe, {"op": "status"})
                return _read(pipe)
        except pywintypes.error as error:
            raise BrokerError("The V4 service disconnected. Try again.") from error


def _public_blob(der):
    key = serialization.load_der_public_key(base64.b64decode(der, validate=True))
    if not isinstance(key, rsa.RSAPublicKey) or not 2048 <= key.key_size <= 4096:
        raise BrokerError("Unsupported Windows Hello key.")
    n = key.public_numbers()
    exponent = n.e.to_bytes((n.e.bit_length() + 7) // 8, "big")
    modulus = n.n.to_bytes(key.key_size // 8, "big")
    return base64.b64encode(struct.pack("<6I", 0x31415352, key.key_size, len(exponent),
                                      len(modulus), 0, 0) + exponent + modulus).decode()


def _authorize(connect, op, payload, state, *, owner_hwnd=None):
    create = op == "enroll"
    name = "ShadowEnclave.V4." + uuid.uuid4().hex if create else state["credential"]
    bridge = install_root() / "hello_bridge.ps1"
    if not bridge.is_file():
        raise BrokerError("The installed Hello bridge is missing. Repair the V4 installation.")
    command = [str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
               "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(bridge),
               "-Name", name]
    if create:
        command.append("-Create")
    if owner_hwnd:
        command.extend(("-OwnerHwnd", str(owner_hwnd)))
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    if owner_hwnd:
        # Let the native Hello helper request foreground activation if Windows permits it.
        ctypes.windll.user32.AllowSetForegroundWindow(process.pid)
    sent_signature = False
    try:
        def native():
            line = process.stdout.readline()
            if not line:
                raise BrokerError("Windows Hello did not complete.")
            result = json.loads(line)
            if "error" in result:
                raise BrokerError(result["error"])
            return result
        public = _public_blob(native()["public_der"])
        pipe = connect()
        _send(pipe, {"op": op, "payload": payload, "credential": name, "public_key": public})
        challenge = _read(pipe)
        if "challenge" not in challenge:
            raise BrokerError("The service did not request a signature. Update the installed V4 service.")
        try:
            process.stdin.write(json.dumps(challenge) + "\n")
            process.stdin.flush()
            proof = native()
        except (OSError, ValueError):
            _send(pipe, {"cancelled": True})
            _read(pipe)
            raise
        sent_signature = True
        _send(pipe, proof)
        return pipe, _read(pipe)
    finally:
        try:
            if process.poll() is None:
                # Preserve a new credential if the service might already have committed enrollment.
                process.stdin.write("commit\n" if sent_signature else "abort\n")
                process.stdin.flush()
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()


def session_is_open():
    return _PIPE is not None


def close_session():
    global _PIPE
    with _LOCK:
        _PIPE = None
        _SESSION.close()


def open_session(*, enroll=False, owner_hwnd=None):
    global _PIPE
    with _LOCK:
        if session_is_open():
            return
        state = status()
        if state.get("session_protocol") != 1:
            raise BrokerError("Update the installed V4 service using Install V4.cmd.")
        if not state["enrolled"] and not enroll:
            raise BrokerError("Enroll Windows Hello to open V4.")
        operation = "open_session" if state["enrolled"] else "enroll"
        with ExitStack() as pending:
            try:
                pipe, result = _authorize(lambda: pending.enter_context(_connect()), operation, {}, state,
                                          owner_hwnd=owner_hwnd)
            except pywintypes.error as error:
                raise BrokerError("The Windows Hello session timed out or disconnected. Try again.") from error
            if result.get("session_open") is not True:
                raise BrokerError("Update the installed V4 service before opening the app.")
            _SESSION.callback(pending.pop_all().close)
            _PIPE = pipe


def enroll():
    open_session(enroll=True)
    return status()


def call(operation, payload=None, *, owner_hwnd=None):
    with _LOCK:
        if not session_is_open():
            raise SessionError("Verify Windows Hello to open V4.")
        try:
            if operation in _FRESH_HELLO:
                state = status()
                if not state["enrolled"]:
                    raise SessionError("Enroll Windows Hello to open V4.")
                _, result = _authorize(lambda: _PIPE, operation, payload or {}, state, owner_hwnd=owner_hwnd)
            else:
                _send(_PIPE, {"op": operation, "payload": payload or {}})
                result = _read(_PIPE)
        except (pywintypes.error, SessionError) as error:
            close_session()
            raise SessionError("The V4 session closed. Verify Windows Hello to open it again.") from error
        if operation == "unenroll":
            close_session()
        return result
