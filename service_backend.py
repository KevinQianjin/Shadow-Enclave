"""Private broker worker. The installed service is its only supported caller."""
import base64
import json
import math
import re
import sqlite3
import sys
import time
from contextlib import closing
from hashlib import sha256
from pathlib import Path

import win32api
import win32con
import win32crypt
import win32security

import enclave_crypto as crypto
import enclave_cadence as cadence
import enclave_windows as windows

SERVICE = "ShadowEnclaveV4"
DEFAULTS = {
    "require_windows_hello": True, "app_retry_cooldown": True,
    "default_device_bound": True, "default_typing": "Off", "default_cover": "Off",
}
ALLOWED = {"carrier_save_dir", "default_device_bound", "default_typing", "default_cover",
           "require_windows_hello", "app_retry_cooldown"}


def service_identity():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        service_sid = win32security.LookupAccountName(None, "NT SERVICE\\" + SERVICE)[0]
        groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
        return user == "S-1-5-19" and any(sid == service_sid and flags & 4 for sid, flags in groups)
    finally:
        token.Close()


def database(root):
    db = sqlite3.connect(root / "broker.sqlite3", timeout=30)
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            owner TEXT PRIMARY KEY, credential TEXT NOT NULL, public_key TEXT NOT NULL,
            settings TEXT NOT NULL, api_key BLOB
        );
        CREATE TABLE IF NOT EXISTS attempts (
            owner TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS covers (
            owner TEXT NOT NULL, digest BLOB NOT NULL, cover TEXT NOT NULL,
            PRIMARY KEY (owner, digest)
        );
    """)
    return db


def get_state(db, owner):
    row = db.execute("SELECT credential,public_key,settings,api_key FROM users WHERE owner=?", (owner,)).fetchone()
    if not row:
        return {"enrolled": False, "settings": dict(DEFAULTS), "has_api_key": False}
    state = {"enrolled": bool(row[0] and row[1]), "settings": json.loads(row[2]),
             "has_api_key": bool(row[3])}
    if state["enrolled"]:
        state.update(credential=row[0], public_key=row[1])
    return state


def validate_settings(data):
    if not isinstance(data, dict) or set(data) - ALLOWED:
        raise ValueError("Unsupported settings.")
    result = dict(DEFAULTS) | data
    if result["require_windows_hello"] is not True or result["app_retry_cooldown"] is not True:
        raise ValueError("V4 service authorization and retry protection cannot be disabled.")
    if type(result["default_device_bound"]) is not bool:
        raise ValueError("Invalid sharing default.")
    if result["default_typing"] not in ("Off", "Standard", "Strict", "Flexible"):
        raise ValueError("Invalid typing preference.")
    if result["default_cover"] not in ("Off", "Local Topic Match", "AI Cover Document (Requires DeepSeek)"):
        raise ValueError("Invalid alternate document preference.")
    folder = result.get("carrier_save_dir", "")
    if not isinstance(folder, str) or len(folder) > 1024 or "\0" in folder:
        raise ValueError("Invalid output folder.")
    return result


def dispatch(db, request):
    owner, op = request["owner"], request["op"]
    # owner is supplied by the trusted native broker, never copied from client payload.
    if not isinstance(owner, str) or not re.fullmatch(r"S-1-(?:5-21|12-1)(?:-\d+){4}", owner):
        raise ValueError("Unsupported Windows account.")
    data = request.get("payload", {})
    if not isinstance(data, dict):
        raise ValueError("Invalid request.")
    windows._KEY_NAME = "Shadow Enclave V4 Broker " + sha256(owner.encode()).hexdigest()
    state = get_state(db, owner)
    if op == "status":
        return state
    if op == "enroll":
        if state["enrolled"]:
            raise ValueError("This account is already enrolled. Existing credentials are never replaced silently.")
        with db:
            db.execute("""INSERT INTO users VALUES (?,?,?,?,NULL)
                          ON CONFLICT(owner) DO UPDATE SET
                              credential=excluded.credential, public_key=excluded.public_key""",
                       (owner, request["credential"], request["public_key"], json.dumps(DEFAULTS)))
        return {"enrolled": True}
    if not state["enrolled"]:
        raise ValueError("Enroll Windows Hello first.")
    if op == "unenroll":
        with db:
            db.execute("UPDATE users SET credential='', public_key='' WHERE owner=?", (owner,))
        return {"enrolled": False}
    if op == "save_settings":
        config = validate_settings(data["settings"])
        api_key = data.get("api_key", "")
        if not isinstance(api_key, str) or len(api_key) > 2048:
            raise ValueError("Invalid API key.")
        with db:
            db.execute("UPDATE users SET settings=? WHERE owner=?", (json.dumps(config), owner))
            if api_key.strip():
                encrypted = win32crypt.CryptProtectData(api_key.strip().encode(), "Shadow Enclave V4", None, None, None, 0)
                db.execute("UPDATE users SET api_key=? WHERE owner=?", (encrypted, owner))
        return {"saved": True}
    if op == "clear_api_key":
        with db:
            db.execute("UPDATE users SET api_key=NULL WHERE owner=?", (owner,))
        return {"cleared": True}
    if op == "get_api_key":
        row = db.execute("SELECT api_key FROM users WHERE owner=?", (owner,)).fetchone()
        return {"api_key": win32crypt.CryptUnprotectData(row[0], None, None, None, 0)[1].decode() if row[0] else None}
    if op == "encrypt":
        if type(data.get("bound")) is not bool or type(data.get("mode")) is not int:
            raise ValueError("Invalid encryption mode.")
        profile = data.get("profile")
        if not isinstance(profile, list) or len(profile) != 4:
            raise ValueError("Invalid typing profile.")
        profile = (*profile[:3], base64.b64decode(profile[3], validate=True))
        cover = data.get("cover", "")
        if not isinstance(cover, str) or len(cover) > 8192:
            raise ValueError("Invalid alternate document.")
        envelope, parts = crypto.seal(data["text"], data["password"], data["mode"],
                                     bound=data["bound"], profile=profile)
        if cover:
            with db:
                db.execute("INSERT INTO covers VALUES (?,?,?)", (owner, sha256(envelope).digest(), cover))
        return {"envelope": base64.b64encode(envelope).decode(),
                "parts": [base64.b64encode(part).decode() for part in parts]}
    if op == "decrypt":
        envelope = crypto.parse_envelope(base64.b64decode(data["envelope"], validate=True))
        # Account-wide failures prevent header edits or alternate carriers from bypassing the limit.
        db.execute("BEGIN IMMEDIATE")
        failures, until = db.execute("SELECT failures,blocked FROM attempts WHERE owner=?", (owner,)).fetchone() or (0, 0)
        now = time.time()
        if until > now:
            db.rollback()
            raise ValueError(f"Retry protection is active. Try again in {math.ceil(until - now)} seconds.")
        failures += 1
        # Reserve the attempt durably BEFORE key use. Crashing cannot erase a failed attempt.
        until = now + 30 if failures >= 3 else 0
        db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?)", (owner, 0 if until else failures, until))
        db.commit()
        time.sleep(1)
        try:
            text, profile = crypto.unseal(envelope, data["password"])
            if profile[0]:
                passed, _ = cadence.evaluate_cadence(profile[1] / 10, profile[3], data.get("typing"), profile[0])
                if not passed:
                    raise crypto.AuthenticationError("Typing check did not match.")
        except (crypto.AuthenticationError, windows.DeviceBindingError):
            row = db.execute("SELECT cover FROM covers WHERE owner=? AND digest=?",
                             (owner, sha256(envelope.data).digest())).fetchone()
            if row and row[0]:
                return {"text": row[0]}
            raise crypto.AuthenticationError("Unable to authenticate or unlock this V4 carrier.")
        with db:
            db.execute("DELETE FROM attempts WHERE owner=?", (owner,))
        return {"text": text}
    raise ValueError("Unsupported broker operation.")


def main():
    if not service_identity():
        raise PermissionError("This backend runs only inside the installed V4 service identity.")
    config = json.loads((Path(sys.executable).resolve().parent.parent / "service.json").read_text(encoding="utf-8-sig"))
    root = Path(config["data_directory"])
    crypto.SERVICE_CONTEXT = True
    raw = sys.stdin.buffer.readline(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("Request too large.")
    with closing(database(root)) as db:
        request = json.loads(raw)
        try:
            result = dispatch(db, request)
            if request["op"] == "status":
                result["data_directory"] = str(root)
        except (ValueError, OSError, KeyError, TypeError) as error:
            result = {"error": str(error)}
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stdout.write('{"error":"The protected backend could not complete this operation."}\n')
        sys.exit(1)
