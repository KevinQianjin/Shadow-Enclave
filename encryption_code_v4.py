import os
from pathlib import Path
import sys
import json
import time
import re
import struct
import math
import wave
import io
import hashlib
import hmac
import tempfile
import unicodedata
import urllib.request
import urllib.error

AUTHOR = "Shadow Enclave Project"
COPYRIGHT_NOTICE = f"Copyright © 2026 {AUTHOR}. All rights reserved."

# A workspace-local install keeps the optional desktop codecs out of the system Python.
_local_deps = Path(__file__).with_name(".enclave-deps")
if _local_deps.is_dir():
    sys.path.append(str(_local_deps))

# Ensure console UTF-8 standard encoding compatibility
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stdin, "reconfigure"):
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def get_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "ShadowEnclave" / "V4"



# Plausible Honey Decoy Pool for Anti-Forensics and Duress Resistance
DECOY_POOL = [
    "Meeting rescheduled to tomorrow morning at nine in conference room B.",
    "Quarterly project report has been submitted to management for review.",
    "The server maintenance and security patch were completed on schedule.",
    "Dinner reservation confirmed for two at seven tonight downtown.",
    "Please remember to update the shared documentation before Friday.",
    "Flight itinerary and hotel booking details have been sent to your email.",
    "Team brainstorming session is postponed until next Monday afternoon.",
    "All client invoices for this billing cycle have been reconciled.",
]


# ==========================================
# Windows System Clipboard and Multiline Input Module
# ==========================================
def _clipboard_functions():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return user32, kernel32


CF_UNICODETEXT = 13


def get_clipboard_text() -> str | None:
    """Read Unicode text from the Windows clipboard."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32, kernel32 = _clipboard_functions()
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except (AttributeError, OSError):
        return None


def set_clipboard_text(text: str) -> bool:
    """Copy Unicode text to the Windows clipboard."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32, kernel32 = _clipboard_functions()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not handle:
            return False
        try:
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return False
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                kernel32.GlobalUnlock(handle)

            # Windows requires a clipboard owner when publishing data.
            window = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
            if not window:
                return False
            try:
                if not user32.OpenClipboard(window):
                    return False
                try:
                    if not user32.EmptyClipboard() or not user32.SetClipboardData(CF_UNICODETEXT, handle):
                        return False
                    handle = None  # Windows owns the buffer after SetClipboardData succeeds.
                    return True
                finally:
                    user32.CloseClipboard()
            finally:
                user32.DestroyWindow(window)
        finally:
            if handle:
                kernel32.GlobalFree(handle)
    except (AttributeError, OSError, UnicodeError):
        return False


def input_multiline(prompt: str, *, multiline_only: bool = False) -> str:
    """
    Intelligent multiline text input:
    1. Press Enter immediately to auto-load clipboard contents.
    2. Or paste text manually and press Enter twice to finish.
    """
    print(prompt)
    print("--------------------------------------------------")
    print("Hint: Press Enter to auto-read clipboard, or paste text and press Enter twice.")
    print("--------------------------------------------------")

    first_line = input().strip()
    if not first_line:
        clip_content = get_clipboard_text()
        if clip_content and clip_content.strip():
            num_l = sum(1 for line in clip_content.splitlines() if line.strip())
            print(f"[Auto-loaded {num_l} lines from system clipboard]")
            return clip_content.strip()
        else:
            print("(Clipboard is empty. Please paste text and press Enter twice):")
            first_line = input().strip()
            if not first_line:
                return ""

    clean_first = first_line.strip('"').strip("'")
    if (
        os.path.isfile(clean_first)
        or clean_first.lower().endswith((".bmp", ".png", ".wav", ".flac"))
        or (not multiline_only and len(first_line) < 260 and not first_line.startswith("-----BEGIN"))
    ):
        return first_line

    lines = [first_line]
    empty_count = 0
    while True:
        try:
            line = input()
            if not line.strip():
                empty_count += 1
                if empty_count >= 1:
                    break
            else:
                empty_count = 0
                lines.append(line)
        except EOFError:
            break
    return "\n".join(lines).strip()


# ==========================================
# Local Key Secure Encryption Storage Module (Windows DPAPI)
# ==========================================
def get_deepseek_api_key() -> str | None:
    import enclave_broker as broker
    if not broker.status().get("has_api_key"):
        return None
    return broker.call("get_api_key")["api_key"]


def has_deepseek_api_key() -> bool:
    import enclave_broker as broker
    try:
        return bool(broker.status()["has_api_key"])
    except broker.BrokerError:
        return False


def deepseek_key_status() -> str:
    import enclave_broker as broker
    try:
        return "Configured in Protected Service" if broker.status().get("has_api_key") else "Not Configured"
    except broker.BrokerError:
        return "V4 Service Unavailable"


def save_deepseek_api_key(key: str) -> bool:
    import enclave_broker as broker
    return broker.call("save_settings", {"settings": load_enclave_config(), "api_key": key})["saved"]


def clear_deepseek_api_key() -> bool:
    import enclave_broker as broker
    return broker.call("clear_api_key")["cleared"]


POETIC_FALLBACK_WORDS = {
    'A': 'autumn', 'B': 'breeze', 'C': 'calm', 'D': 'dusk', 'E': 'evening',
    'F': 'forest', 'G': 'gentle', 'H': 'hills', 'I': 'infinite', 'J': 'journey',
    'K': 'kindle', 'L': 'lunar', 'M': 'moonlight', 'N': 'night', 'O': 'ocean',
    'P': 'peaceful', 'Q': 'quiet', 'R': 'river', 'S': 'silent', 'T': 'twilight',
    'U': 'under', 'V': 'valleys', 'W': 'whisper', 'X': 'xeric', 'Y': 'yellow', 'Z': 'zephyr'
}


def ensure_line_quad(line_str: str, chunk: list[str]) -> str:
    """4-anchor natural cadence alignment: first two + last two words bound to target letters, zero external captions"""
    words = re.findall(r"[A-Za-z]+", line_str)
    while len(words) < 6:
        words.append("calmly")
    if len(chunk) >= 1 and words[0][0].upper() != chunk[0].upper():
        words[0] = POETIC_FALLBACK_WORDS.get(chunk[0].upper(), "silent").capitalize()
    if len(chunk) >= 2 and words[1][0].upper() != chunk[1].upper():
        words[1] = POETIC_FALLBACK_WORDS.get(chunk[1].upper(), "whisper")
    if len(chunk) >= 3 and words[-2][0].upper() != chunk[2].upper():
        words[-2] = POETIC_FALLBACK_WORDS.get(chunk[2].upper(), "peaceful")
    if len(chunk) >= 4 and words[-1][0].upper() != chunk[3].upper():
        words[-1] = POETIC_FALLBACK_WORDS.get(chunk[3].upper(), "valleys")
    return " ".join(words)


COVER_DOCUMENT_RULE = (
    "Return only the finished English document. Use no preface, explanation, disclaimer, "
    "or wording that identifies the document as a decoy, honeypot, bait, disinformation, "
    "fictional example, simulation, or placeholder."
)
COVER_LABELS = re.compile(
    r"\b(?:de[\s_-]*coys?|honey[\s_-]*(?:pots?|traps?|tokens?|encryption)|baits?|"
    r"disinformation|misinformation|fabricat(?:ed|ion)|fictional|fictitious|"
    r"simulat(?:ed|ion)|synthetic|placeholders?|fake|ai[\s_-]+generated|"
    r"(?:sample|mock|dummy)[\s_-]+(?:memo|report|document|text|data)|"
    r"for[\s_-]+(?:illustration|demonstration)[\s_-]+only)\b",
    re.IGNORECASE,
)


def cover_document_is_usable(text: object) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    return COVER_LABELS.search(normalized) is None and not any(
        label in normalized for label in ("\u871c\u7f50", "\u8a98\u990c", "\u8bf1\u9975")
    )


def generate_dynamic_ai_decoy(api_key: str | None, decoy_idx: int) -> str:
    """Generate dynamic AI honey decoy memo when coerced or incorrect credentials entered"""
    if api_key:
        try:
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            user_prompt = (
                "Generate a highly convincing confidential internal executive memorandum for a multinational enterprise.\n"
                "Include: Project codename (e.g. Project Aurora, Nexus Prime), meeting timestamp, realistic operational budgets, action milestones, and strict NDAs.\n"
                "Use a formal tone and write about 80 to 150 words. "
                + COVER_DOCUMENT_RULE
            )
            data = {
                "model": "deepseek-flash",
                "messages": [
                    {"role": "system", "content": "You are a senior corporate intelligence and governance auditor specialized in top-secret business documentation."},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7
            }
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
                if cover_document_is_usable(res):
                    return res
        except Exception:
            pass
    return DECOY_POOL[decoy_idx % len(DECOY_POOL)]


def get_downloads_dir() -> str:
    """Get system default Downloads directory path"""
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(dl):
        return dl
    return os.getcwd()

def load_enclave_config() -> dict:
    import enclave_broker as broker
    try:
        return broker.status()["settings"]
    except broker.BrokerError:
        return {"require_windows_hello": True, "app_retry_cooldown": True, "default_device_bound": True}


def save_enclave_config(cfg: dict) -> bool:
    import enclave_broker as broker
    return broker.call("save_settings", {"settings": cfg})["saved"]


def prepare_local_directory(value: str) -> Path:
    if not value.strip().strip('"'):
        raise ValueError("Enter a local folder.")
    path = Path(os.path.expandvars(value.strip().strip('"'))).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path, prefix=".enclave-write-"):
        pass
    return path


def get_verification_data_dir() -> Path:
    import enclave_broker as broker
    try:
        return Path(broker.status()["data_directory"])
    except (broker.BrokerError, KeyError):
        return Path(os.environ.get("ProgramData", "C:/ProgramData")) / "ShadowEnclave" / "V4"


def windows_hello_enabled() -> bool:
    return True


def get_carrier_save_dir() -> str:
    """Get user-configured carrier save directory; defaults to Downloads"""
    cfg = load_enclave_config()
    saved = cfg.get("carrier_save_dir")
    if isinstance(saved, str) and saved and os.path.isdir(saved):
        return os.path.abspath(saved)
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(dl):
        return os.path.abspath(dl)
    return os.path.abspath(os.getcwd())


def capture_keystroke_password(prompt: str = "Enter decryption passphrase:\n", hide_input: bool = True) -> tuple[str, float, float, list[float]]:
    """
    Capture passphrase string and keystroke dynamic biometric characteristics in real time.
    Returns: (password_str, mean_flight_ms, std_flight_ms, intervals_list)
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()

    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\r\n"), 0.0, 0.0, []

    try:
        import msvcrt
    except ImportError:
        from getpass import getpass

        return getpass(""), 0.0, 0.0, []

    chars = []
    timestamps = []
    while True:
        ch = msvcrt.getwch()
        now_ns = time.perf_counter_ns()
        if ch in ("\r", "\n"):
            print()
            break
        if ch == "\x03":
            print()
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if ch in ("\x08", "\x7f"):
            if chars:
                removed = chars.pop()
                timestamps.pop()
                if 0xDC00 <= ord(removed) <= 0xDFFF and chars and 0xD800 <= ord(chars[-1]) <= 0xDBFF:
                    chars.pop()
                    timestamps.pop()
                print("\b \b", end="", flush=True)
        else:
            chars.append(ch)
            timestamps.append(now_ns)
            print("*" if hide_input else ch, end="", flush=True)
    try:
        pwd = "".join(chars).encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeError as exc:
        raise ValueError("Enter a valid Unicode passphrase.") from exc
    if len(timestamps) < 2:
        return pwd, 0.0, 0.0, []

    intervals = [(timestamps[i] - timestamps[i - 1]) / 1_000_000.0 for i in range(1, len(timestamps))]
    mean_val = sum(intervals) / len(intervals)
    variance = sum((x - mean_val) ** 2 for x in intervals) / len(intervals)
    std_val = math.sqrt(variance)
    return pwd, mean_val, std_val, intervals


BIOMETRIC_LEVELS = {"Standard": 1, "Strict": 2, "Flexible": 3}


def format_keystroke_metrics(mean, std, intervals) -> str:
    count = len(intervals)
    if not count:
        return "0 intervals measured"
    unit = "interval" if count == 1 else "intervals"
    return f"{count} {unit}  /  Mean {mean:.1f} ms  /  Std. dev. {std:.1f} ms"


def console_keystroke_metrics(password, mean, std, intervals) -> str:
    measured = intervals if sys.platform == "win32" and sys.stdin.isatty() and len(password) > 1 else ()
    return format_keystroke_metrics(mean, std, measured)


def enroll_biometric_password_3pass(prompt_title: str = "Password", allow_empty: bool = True) -> tuple[str, float, float, list[float]]:
    """
    3-Pass Password Confirmation & Keystroke Biometric Ensemble Calibration:
    Prompts user to enter password 3 consecutive times:
    1. Guarantees 100% password confirmation (prevents typos).
    2. Synthesizes an ensemble biometric baseline by averaging cadence vectors across 3 samples.
    Returns: (password, ensemble_mean, ensemble_std, ensemble_intervals)
    """
    while True:
        print("\n" + "-" * 58)
        print(" [3-Pass Password Confirmation & Biometric Calibration]")
        print("-" * 58)
        if allow_empty:
            print("Notice: Press Enter on Pass 1 for Master Key (no password).")

        # Pass 1
        pwd1, m1, s1, int1 = capture_keystroke_password(prompt="[Pass 1/3] Set Password: ", hide_input=True)
        if not pwd1 and allow_empty:
            return "", 0.0, 0.0, []

        if not pwd1:
            print("[Notice] Password cannot be empty. Please enter a valid password.")
            continue

        print(f"Sample 1/3: {console_keystroke_metrics(pwd1, m1, s1, int1)}")

        # Pass 2
        pwd2, m2, s2, int2 = capture_keystroke_password(prompt="[Pass 2/3] Confirm Password: ", hide_input=True)
        if pwd2 != pwd1:
            print("[Notice] Passwords do not match! Biometric enrollment reset. Please re-enter from Pass 1.")
            continue

        print(f"Sample 2/3: {console_keystroke_metrics(pwd2, m2, s2, int2)}")

        # Pass 3
        pwd3, m3, s3, int3 = capture_keystroke_password(prompt="[Pass 3/3] Biometric Calibration Confirmation: ", hide_input=True)
        if pwd3 != pwd1:
            print("[Notice] Passwords do not match! Biometric enrollment reset. Please re-enter from Pass 1.")
            continue

        print(f"Sample 3/3: {console_keystroke_metrics(pwd3, m3, s3, int3)}")

        # Synthesize ensemble biometric baseline:
        ensemble_mean = (m1 + m2 + m3) / 3.0
        ensemble_std = (s1 + s2 + s3) / 3.0

        min_len = min(len(int1), len(int2), len(int3))
        if min_len > 0:
            ensemble_intervals = [(int1[i] + int2[i] + int3[i]) / 3.0 for i in range(min_len)]
        else:
            ensemble_intervals = [ensemble_mean]

        print("-" * 58)
        print("[PASS] 3-Pass Password Confirmed 100%.")
        print(f"Typing baseline: {console_keystroke_metrics(pwd1, ensemble_mean, ensemble_std, ensemble_intervals)}")
        print("-" * 58)

        return pwd1, ensemble_mean, ensemble_std, ensemble_intervals if min_len else []


def compute_cadence_sig(intervals: list[float], mean_val: float) -> bytes:
    """Compute 8-byte normalized cadence biometric signature digest"""
    if not intervals or mean_val <= 0:
        return b"\x40" * 8
    denom = mean_val
    raw = [int(min(255, max(0, round((x / denom) * 64)))) for x in intervals[:8]]
    while len(raw) < 8:
        raw.append(64)
    return bytes(raw[:8])


# ==========================================
# 2D Integer Wavelet Transform (2D DWT/IWT) & Keyed Randomized Steganography Engine
# ==========================================
STEGO_ANCHOR_BYTES = 24  # 16B keyed/random stego_seed + 4B masked_len + 4B verify_tag
STEGO_OVERHEAD_BYTES = 25  # Anchor (24B) + up to 1B header tag when stripping/encoding magic
_STEGO_RAW_TAG = 0xA0
_STEGO_SENC_TAG = 0xA1
_STEGO_SENP_TAG = 0xA2


def _strip_stego_magic(payload: bytes) -> bytes:
    """Strip plaintext SENC/SENP magic header before steganographic whitening."""
    if payload.startswith(b"SENC"):
        return bytes([_STEGO_SENC_TAG]) + payload[4:]
    if payload.startswith(b"SENP"):
        return bytes([_STEGO_SENP_TAG]) + payload[4:]
    return bytes([_STEGO_RAW_TAG]) + payload


def _restore_stego_magic(wire: bytes) -> bytes | None:
    """Restore canonical SENC/SENP magic header after steganographic extraction."""
    if not wire:
        return None
    tag = wire[0]
    if tag == _STEGO_SENC_TAG:
        return b"SENC" + wire[1:]
    if tag == _STEGO_SENP_TAG:
        return b"SENP" + wire[1:]
    if tag == _STEGO_RAW_TAG:
        return wire[1:]
    return None


def _stego_hkdf(ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 keystream generator for steganographic PRNG and payload whitening."""
    prk = hmac.new(b"enclave-v4-stego-salt-2026", ikm, hashlib.sha256).digest()
    out = bytearray()
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _fisher_yates_step(swaps: dict[int, int], step_idx: int, total_slots: int, rand_u32: int) -> int:
    """Single step of a sparse partial Fisher-Yates shuffle over [0, total_slots)."""
    j = step_idx + (rand_u32 % (total_slots - step_idx))
    val_i = swaps.get(step_idx, step_idx)
    val_j = swaps.get(j, j)
    swaps[step_idx] = val_j
    swaps[j] = val_i
    return val_j


def _derive_stego_seed(payload: bytes, cover_digest: bytes, domain: bytes) -> bytes:
    """Derive a 16-byte stego seed from the payload's carrier_id (if present) and fresh OS entropy."""
    if len(payload) >= 22 and payload[:4] == b"SENC":
        key_material = payload[6:22]
    elif len(payload) >= 23 and payload[:4] == b"SENP":
        key_material = payload[7:23]
    else:
        key_material = hashlib.sha256(payload).digest()[:16]
    return _stego_hkdf(key_material + os.urandom(16) + cover_digest, domain + b"-seed", 16)


def _pack_stego_bits(payload: bytes, cover_digest: bytes, total_slots: int, domain: bytes) -> tuple[list[int], list[int]]:
    """Return (positions, bits) for randomized, whitened steganographic embedding with magic stripped and carrier-XOR masked."""
    stripped_payload = _strip_stego_magic(payload)
    total_bytes = STEGO_ANCHOR_BYTES + len(stripped_payload)
    total_bits = total_bytes * 8
    if total_bits > total_slots:
        raise ValueError("Payload exceeds steganographic carrier capacity.")

    stego_seed = _derive_stego_seed(payload, cover_digest, domain)
    header_stream = _stego_hkdf(cover_digest + stego_seed, domain + b"-header", 8)
    len_mask = struct.unpack(">I", header_stream[:4])[0]
    verify_tag = header_stream[4:8]
    masked_len = struct.pack(">I", len(stripped_payload) ^ len_mask)
    anchor_bytes = stego_seed + masked_len + verify_tag

    payload_mask = _stego_hkdf(cover_digest + stego_seed, domain + b"-mask", len(stripped_payload))
    whitened_payload = bytes(a ^ b for a, b in zip(stripped_payload, payload_mask))
    full_blob = anchor_bytes + whitened_payload

    bits = [((byte_val >> (7 - bit_i)) & 1) for byte_val in full_blob for bit_i in range(8)]

    swaps: dict[int, int] = {}
    positions: list[int] = []
    anchor_bits = STEGO_ANCHOR_BYTES * 8
    anchor_rng = _stego_hkdf(cover_digest, domain + b"-anchor-perm", anchor_bits * 4)
    for i in range(anchor_bits):
        u32 = struct.unpack_from(">I", anchor_rng, i * 4)[0]
        positions.append(_fisher_yates_step(swaps, i, total_slots, u32))

    payload_bits = len(stripped_payload) * 8
    if payload_bits > 0:
        payload_rng = _stego_hkdf(cover_digest + stego_seed, domain + b"-payload-perm", payload_bits * 4)
        for k in range(payload_bits):
            u32 = struct.unpack_from(">I", payload_rng, k * 4)[0]
            positions.append(_fisher_yates_step(swaps, anchor_bits + k, total_slots, u32))

    return positions, bits


def _unpack_stego_bits(bit_reader, cover_digest: bytes, total_slots: int, domain: bytes) -> bytes | None:
    """Attempt to extract, carrier-unmask, and restore a randomized stego payload."""
    anchor_bits = STEGO_ANCHOR_BYTES * 8
    if total_slots < anchor_bits:
        return None

    swaps: dict[int, int] = {}
    anchor_rng = _stego_hkdf(cover_digest, domain + b"-anchor-perm", anchor_bits * 4)
    anchor_buf = bytearray(STEGO_ANCHOR_BYTES)
    for i in range(anchor_bits):
        u32 = struct.unpack_from(">I", anchor_rng, i * 4)[0]
        pos = _fisher_yates_step(swaps, i, total_slots, u32)
        anchor_buf[i // 8] |= (bit_reader(pos) & 1) << (7 - (i % 8))

    stego_seed = bytes(anchor_buf[:16])
    masked_len = struct.unpack(">I", anchor_buf[16:20])[0]
    candidate_tag = bytes(anchor_buf[20:24])

    header_stream = _stego_hkdf(cover_digest + stego_seed, domain + b"-header", 8)
    len_mask = struct.unpack(">I", header_stream[:4])[0]
    expected_tag = header_stream[4:8]
    if not hmac.compare_digest(candidate_tag, expected_tag):
        return None

    payload_len = masked_len ^ len_mask
    if payload_len <= 0 or (STEGO_ANCHOR_BYTES + payload_len) * 8 > total_slots:
        return None

    payload_bits = payload_len * 8
    payload_rng = _stego_hkdf(cover_digest + stego_seed, domain + b"-payload-perm", payload_bits * 4)
    whitened_buf = bytearray(payload_len)
    for k in range(payload_bits):
        u32 = struct.unpack_from(">I", payload_rng, k * 4)[0]
        pos = _fisher_yates_step(swaps, anchor_bits + k, total_slots, u32)
        whitened_buf[k // 8] |= (bit_reader(pos) & 1) << (7 - (k % 8))

    payload_mask = _stego_hkdf(cover_digest + stego_seed, domain + b"-mask", payload_len)
    stripped_payload = bytes(a ^ b for a, b in zip(whitened_buf, payload_mask))
    return _restore_stego_magic(stripped_payload)


def _dwt_cover_digest(fwd: list[list[int]], width: int, height: int) -> bytes:
    """Compute a SHA-256 digest of wavelet features invariant to LH LSB embedding."""
    w2 = width // 2
    h2 = height // 2
    hasher = hashlib.sha256()
    hasher.update(struct.pack(">II", width, height))
    for y in range(height):
        row = fwd[y]
        if y < h2:
            buf = [row[x] for x in range(w2)] + [row[x] & ~1 for x in range(w2, width)]
        else:
            buf = row
        hasher.update(struct.pack(f">{width}i", *buf))
    return hasher.digest()


def _bmp_lsb_cover_digest(raw_pixels: bytes | bytearray) -> bytes:
    """Compute a SHA-256 digest of spatial pixel MSBs (bits 7..1), invariant to LSB embedding."""
    return hashlib.sha256(bytes(b & 0xFE for b in raw_pixels)).digest()


def _wav_lsb_cover_digest(raw_frames: bytes | bytearray) -> bytes:
    """Compute a SHA-256 digest of 16-bit little-endian PCM MSBs, invariant to sample LSB embedding."""
    masked = bytearray(raw_frames)
    for i in range(0, len(masked), 2):
        masked[i] &= 0xFE
    return hashlib.sha256(masked).digest()


def iwt2d_forward(matrix: list[list[int]], width: int, height: int) -> list[list[int]]:
    """Forward 2D Integer Wavelet Transform (Lifting Scheme Haar)"""
    w2 = width // 2
    h2 = height // 2
    row_t = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(w2):
            a = matrix[y][2 * x]
            b = matrix[y][2 * x + 1]
            row_t[y][x] = (a + b) >> 1
            row_t[y][w2 + x] = a - b
    col_t = [[0] * width for _ in range(height)]
    for x in range(width):
        for y in range(h2):
            a = row_t[2 * y][x]
            b = row_t[2 * y + 1][x]
            col_t[y][x] = (a + b) >> 1
            col_t[h2 + y][x] = a - b
    return col_t


def iwt2d_inverse(matrix: list[list[int]], width: int, height: int) -> list[list[int]]:
    """Inverse 2D Integer Wavelet Transform (Lossless exact integer reconstruction)"""
    w2 = width // 2
    h2 = height // 2
    col_inv = [[0] * width for _ in range(height)]
    for x in range(width):
        for y in range(h2):
            low = matrix[y][x]
            h = matrix[h2 + y][x]
            col_inv[2 * y][x] = low + ((h + 1) >> 1)
            col_inv[2 * y + 1][x] = low - (h >> 1)
    orig = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(w2):
            low = col_inv[y][x]
            h = col_inv[y][w2 + x]
            orig[y][2 * x] = low + ((h + 1) >> 1)
            orig[y][2 * x + 1] = low - (h >> 1)
    return orig


def embed_dwt_bmp(raw_pixels: bytearray, width: int, height: int, payload: bytes) -> bytearray:
    """Embed payload into 2D DWT/IWT mid-frequency subband (LH) at HKDF-randomized coefficient positions."""
    w2 = width // 2
    h2 = height // 2
    total_slots = w2 * h2
    if width % 2 or height % 2 or (len(payload) + STEGO_OVERHEAD_BYTES) * 8 > total_slots:
        raise ValueError("DWT payload exceeds the capacity of this image or its dimensions are odd.")

    # Leave room for a one-level adjustment without clipping embedded bits.
    green_plane = [
        [min(254, max(1, raw_pixels[(y * width + x) * 3 + 1])) for x in range(width)]
        for y in range(height)
    ]
    fwd = iwt2d_forward(green_plane, width, height)
    cover_digest = _dwt_cover_digest(fwd, width, height)
    positions, bits = _pack_stego_bits(payload, cover_digest, total_slots, b"v4-dwt-lh")

    for pos, bit in zip(positions, bits):
        y, lx = divmod(pos, w2)
        x = w2 + lx
        fwd[y][x] = (fwd[y][x] & ~1) | bit

    inv = iwt2d_inverse(fwd, width, height)
    out_pixels = bytearray(raw_pixels)
    for y in range(height):
        for x in range(width):
            out_pixels[(y * width + x) * 3 + 1] = inv[y][x]
    return out_pixels


def extract_dwt_bmp(data_pixels: bytes, width: int, height: int) -> bytes | None:
    """Extract payload from 2D DWT/IWT wavelet coefficients using keyed anchor verification."""
    try:
        if width % 2 or height % 2:
            return None
        w2 = width // 2
        h2 = height // 2
        total_slots = w2 * h2
        if total_slots < STEGO_ANCHOR_BYTES * 8:
            return None

        green_plane = [[data_pixels[(y * width + x) * 3 + 1] for x in range(width)] for y in range(height)]
        fwd = iwt2d_forward(green_plane, width, height)
        cover_digest = _dwt_cover_digest(fwd, width, height)

        def _read_lh_bit(pos: int) -> int:
            y, lx = divmod(pos, w2)
            return fwd[y][w2 + lx] & 1

        return _unpack_stego_bits(_read_lh_bit, cover_digest, total_slots, b"v4-dwt-lh")
    except Exception:
        return None


# ==========================================
# DeepSeek-Driven Semantic Mirror Decoy Engine
# ==========================================
def generate_offline_mirror_decoy(secret_text: str) -> str:
    """Generate high-fidelity domain-specific inverted disinformation decoy offline"""
    txt_lower = secret_text.lower()
    if any(k in txt_lower for k in ["attack", "military", "target", "operation", "strike", "troop", "weapon", "coord"]):
        return (
            "[CLASSIFIED MILITARY DIRECTIVE - REVOCATION NOTICE]\n"
            "ORIGIN: JOINT COMMAND THEATER HEADQUARTERS\n"
            "DIRECTIVE: OPERATION STAND-DOWN AND RETREAT\n"
            "1. Tactical maneuver 'Operation Iron Wall' scheduled for 0400 hours is hereby CANCELLED.\n"
            "2. All strike units are ordered to disengage and fall back to defensive position Echo-4.\n"
            "3. Munition resupply and forward artillery preparations are suspended until further notice.\n"
            "4. Maintain strict radio silence and activate passive electronic countermeasures only."
        )
    elif any(k in txt_lower for k in ["fund", "money", "dollar", "usd", "account", "bank", "transfer", "profit", "stock"]):
        return (
            "[CONFIDENTIAL FINANCIAL AUDIT RECONCILIATION]\n"
            "STATUS: TRANSACTION REVERSED / AUDIT COMPLETED\n"
            "1. The anticipated capital transfer of $25,400,000 has been successfully RECALLED to the escrow reserve.\n"
            "2. Internal compliance investigation revealed zero discrepancy; fiscal accounts are fully balanced.\n"
            "3. Mergers and acquisitions advisory board has rejected the proposed buyout term sheet.\n"
            "4. Q3 dividend distribution is deferred to fiscal year-end contingency allocations."
        )
    elif any(k in txt_lower for k in ["key", "password", "token", "auth", "secret", "database", "admin", "root"]):
        return (
            "[INFRASTRUCTURE SECURITY INCIDENT POST-MORTEM]\n"
            "SECURITY TIER: INTERNAL DEFENSE CLEARED\n"
            "1. Production root credentials and access tokens were successfully rotated across all cloud clusters.\n"
            "2. Intrusion telemetry confirmed zero unauthorized ingress; alert was classified as a false positive.\n"
            "3. Hardware security modules (HSM) report 100% cryptographic integrity with zero payload leakage.\n"
            "4. Access privileges for legacy diagnostic service accounts have been permanently revoked."
        )
    else:
        return (
            "[STRATEGIC LIAISON MEMORANDUM]\n"
            "CLASSIFICATION: RESTRICTED DISTRIBUTION\n"
            "SUBJECT: EXECUTIVE STATUS CONSOLIDATION\n"
            "1. The preliminary evaluation findings previously discussed have been comprehensively negated.\n"
            "2. All operational milestones originally targeted for this quarter are formally archived as inactive.\n"
            "3. Further bilateral synchronization meetings are indefinitely suspended pending administrative review.\n"
            "4. Archive this briefing and securely purge all transient draft telemetry."
        )


def build_bmp_carrier(payload: bytes, width: int = 128, height: int = 128, style_seed: int = 0) -> bytes:
    """Generate 100% compliant true-color BMP image carrier with HKDF-randomized LSB embedding."""
    total_pixels = width * height
    capacity_bytes = total_pixels * 3 // 8
    if len(payload) + STEGO_OVERHEAD_BYTES > capacity_bytes:
        needed = (len(payload) + STEGO_OVERHEAD_BYTES) * 8 // 3 + 100
        side = int(math.isqrt(needed)) + 1
        width = (side + 3) & ~3
        height = side

    row_size = (width * 3 + 3) & ~3
    image_size = row_size * height
    file_size = 54 + image_size
    bmp_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, 54)
    dib_header = struct.pack('<IIIHHIIIIII', 40, width, height, 1, 24, 0, image_size, 2835, 2835, 0, 0)

    raw_pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            idx = (y * width + x) * 3
            if style_seed == 1:
                # Sunset Gold Fractal Palette
                raw_pixels[idx] = int((128 + 127 * math.cos((x - y) * 0.04)) % 256)      # B
                raw_pixels[idx+1] = int((128 + 127 * math.sin(y * 0.06)) % 256)          # G
                raw_pixels[idx+2] = int((180 + 75 * math.sin((x + y) * 0.04)) % 256)     # R
            elif style_seed == 2:
                # Emerald Aurora Fractal Palette
                raw_pixels[idx] = int((100 + 90 * math.sin(x * 0.05)) % 256)             # B
                raw_pixels[idx+1] = int((170 + 80 * math.sin((x + y) * 0.04)) % 256)     # G
                raw_pixels[idx+2] = int((80 + 70 * math.cos(y * 0.05)) % 256)            # R
            else:
                # Deep Cyber Azure Fractal Palette
                raw_pixels[idx] = int((128 + 127 * math.sin(x * 0.05)) % 256)            # B
                raw_pixels[idx+1] = int((128 + 127 * math.cos(y * 0.05)) % 256)          # G
                raw_pixels[idx+2] = int((128 + 127 * math.sin((x + y) * 0.03)) % 256)    # R

    if payload:
        cover_digest = _bmp_lsb_cover_digest(raw_pixels)
        positions, bits = _pack_stego_bits(payload, cover_digest, len(raw_pixels), b"v4-bmp-lsb")
        for pos, bit in zip(positions, bits):
            raw_pixels[pos] = (raw_pixels[pos] & 0xFE) | bit

    rows = []
    padding = b'\x00' * (row_size - width * 3)
    for y in range(height):
        start = y * width * 3
        rows.append(raw_pixels[start:start + width * 3] + padding)
    return bmp_header + dib_header + b''.join(rows)


def extract_bmp_carrier(data: bytes) -> bytes:
    """Extract steganographic payload from real BMP image carrier (Auto-detects 2D DWT or Spatial LSB)"""
    if len(data) < 54 or data[:2] != b'BM':
        raise ValueError('Invalid BMP file format')
    from enclave_media import MAX_IMAGE_PIXELS

    width, height = struct.unpack('<II', data[18:26])
    row_size = (width * 3 + 3) & ~3
    offset = struct.unpack('<I', data[10:14])[0]
    planes, depth, compression = struct.unpack_from('<HHI', data, 26)
    if (width == 0 or height == 0 or offset < 54 or (planes, depth, compression) != (1, 24, 0)
            or row_size * height > len(data) - offset or not 11 <= width * height <= MAX_IMAGE_PIXELS):
        raise ValueError("Unsupported or truncated BMP carrier.")

    raw_pixel_bytes = bytearray()
    p = offset
    for y in range(height):
        raw_pixel_bytes.extend(data[p : p + width * 3])
        p += row_size

    # 1. Attempt 2D DWT / IWT Wavelet extraction
    dwt_extracted = extract_dwt_bmp(bytes(raw_pixel_bytes), width, height)
    if dwt_extracted is not None:
        return dwt_extracted

    # 2. Fallback to HKDF-randomized spatial LSB extraction
    cover_digest = _bmp_lsb_cover_digest(raw_pixel_bytes)
    lsb_extracted = _unpack_stego_bits(
        lambda pos: raw_pixel_bytes[pos] & 1,
        cover_digest,
        len(raw_pixel_bytes),
        b"v4-bmp-lsb",
    )
    if lsb_extracted is None:
        raise ValueError('Invalid payload length or corrupted BMP carrier')
    return lsb_extracted


def build_wav_carrier(payload: bytes, duration_sec: float = 3.0, sample_rate: int = 44100) -> bytes:
    """Generate 100% compliant RIFF WAV audio carrier with natural harmonic spectrum and randomized LSB embedding."""
    return render_melodic_wav(payload, melody_type="ambient", duration_sec=duration_sec, sample_rate=sample_rate)


def extract_wav_carrier(data: bytes) -> bytes:
    """Extract steganographic payload from genuine WAV audio carrier using keyed randomized sample positions."""
    from enclave_media import MAX_AUDIO_FRAMES

    buf = io.BytesIO(data)
    with wave.open(buf, 'rb') as wf:
        num_frames = wf.getnframes()
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or not 32 <= num_frames <= MAX_AUDIO_FRAMES:
            raise ValueError("Unsupported WAV carrier. Expected 16-bit mono audio.")
        raw = wf.readframes(num_frames)
        if len(raw) != num_frames * 2:
            raise ValueError("Truncated WAV carrier.")

    cover_digest = _wav_lsb_cover_digest(raw)
    extracted = _unpack_stego_bits(
        lambda pos: raw[2 * pos] & 1,
        cover_digest,
        num_frames,
        b"v4-wav-lsb",
    )
    if extracted is None:
        raise ValueError('WAV stego payload length invalid or carrier corrupted')
    return extracted


VISUAL_THEMES_CATALOG = {
    "sunset": {"name": "Twilight Ridge & Solitary Peak", "desc": "Atmospheric Rayleigh diffusion with mountain silhouette", "celestial": "sun"},
    "emerald": {"name": "Emerald Aurora & Starry Peaks", "desc": "Hyperbolic sine aurora ribbons over solitary mountain peaks", "celestial": "aurora"},
    "cyber": {"name": "Cyberpunk Neon Matrix", "desc": "Cyan-magenta synthesizer perspective grid with neon digital rain", "celestial": "grid"},
    "ocean": {"name": "Aerial Surf & Sand", "desc": "Top-down tidal foam and a diagonal coastline", "celestial": "moon"},
    "sakura": {"name": "Sakura Dawn & Morning Mist", "desc": "Delicate pearl pink haze, soft cream gradient, and falling sakura petals", "celestial": "dawn"},
    "desert": {"name": "Desert Dunes at Sunset", "desc": "Ochre and golden sand dune curves with radiant desert sunset horizon", "celestial": "sun"},
    "ink": {"name": "Monochrome Jiangnan Ink Wash", "desc": "Minimalist Chinese Xuan paper wash with Hui-style distant silhouettes", "celestial": "seal"},
    "nebula": {"name": "Deep Cosmic Nebula Singularity", "desc": "Violet-indigo bipolar nebula clouds with pulsating stellar core", "celestial": "pulsar"}
}

MELODY_STYLES_CATALOG = {
    "ambient": {"name": "Ethereal Piano & Canon Arpeggios", "desc": "Major canon chord cycle with resonant piano sustains"},
    "zen": {"name": "Eastern Zen Pentatonic Plucks", "desc": "Gong-Shang-Jiao-Zhi-Yu acoustic plucks with valley echoes"},
    "cyber": {"name": "Retro 80s Synthwave Pulse", "desc": "Low-pass filtered saw bass with cyberpunk neon arpeggios"},
    "lullaby": {"name": "Music Box Lullaby & Gentle Sine", "desc": "Crystalline music box sine tones with ultra-soft envelope"},
    "celtic": {"name": "Celtic Highland Flute & Drones", "desc": "Dorian pastoral whistle with open fifth drone bass"},
    "jazz": {"name": "Midnight Cool Jazz & Blues", "desc": "Flat-seventh minor jazz progression with soft breath vibrato"},
    "lofi": {"name": "Rainy Lo-Fi Chillwave Chords", "desc": "Analog tape flutter with warm minor-seventh Rhodes chords"},
    "rain": {"name": "Mountain Rain & Pure Droplets", "desc": "Harmonic resonant rainfall with crisp acoustic rain bell chimes"}
}


def render_scenic_bmp(payload: bytes, theme: str = "sunset", width: int = 256, height: int = 256,
                      use_dwt: bool = False, style=None) -> bytes:
    """Render a 24-bit image and embed the payload after styling at HKDF-randomized positions."""
    from enclave_art import render_scene
    from enclave_media import ImageStyle

    style = style or ImageStyle(theme)
    if use_dwt:
        width += width % 2
        height += height % 2
        needed_bits = (len(payload) + STEGO_OVERHEAD_BYTES) * 8
        if (width // 2) * (height // 2) < needed_bits:
            side = 2 * (math.isqrt(needed_bits - 1) + 1)
            width, height = max(width, side), max(height, side)
    elif len(payload) + STEGO_OVERHEAD_BYTES > width * height * 3 // 8:
        needed = (len(payload) + STEGO_OVERHEAD_BYTES) * 8 // 3 + 100
        side = math.isqrt(needed) + 1
        width = (side + 3) & ~3
        height = side

    row_size = (width * 3 + 3) & ~3
    image_size = row_size * height
    file_size = 54 + image_size
    bmp_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, 54)
    dib_header = struct.pack('<IIIHHIIIIII', 40, width, height, 1, 24, 0, image_size, 2835, 2835, 0, 0)

    image = render_scene(style, width, height)
    raw_pixels = bytearray(image.tobytes("raw", "BGR", 0, -1))

    # Embed only after rendering so style changes cannot alter embedded bits.
    if payload:
        if use_dwt:
            raw_pixels = embed_dwt_bmp(raw_pixels, width, height, payload)
        else:
            cover_digest = _bmp_lsb_cover_digest(raw_pixels)
            positions, bits = _pack_stego_bits(payload, cover_digest, len(raw_pixels), b"v4-bmp-lsb")
            for pos, bit in zip(positions, bits):
                raw_pixels[pos] = (raw_pixels[pos] & 0xFE) | bit

    rows = []
    padding = b'\x00' * (row_size - width * 3)
    for y in range(height):
        start = y * width * 3
        rows.append(raw_pixels[start:start + width * 3] + padding)

    return bmp_header + dib_header + b''.join(rows)


def _apply_natural_room_floor(frames: bytearray, sample_rate: int, theme: str) -> None:
    """Add a subtle pink-filtered acoustic room floor so silent/fade tails exhibit natural broadband spectrum."""
    state = int.from_bytes(hashlib.sha256(f"enclave-room-{theme}-{sample_rate}".encode()).digest()[:8], "big") | 1
    lp = 0.0
    total_samples = len(frames) // 2
    for i in range(total_samples):
        # Xorshift64* PRNG for fast acoustic thermal/room floor synthesis
        state ^= (state >> 12) & 0xFFFFFFFFFFFFFFFF
        state ^= (state << 25) & 0xFFFFFFFFFFFFFFFF
        state ^= (state >> 27) & 0xFFFFFFFFFFFFFFFF
        rnd = ((state * 0x2545F4914F6CDD1D) >> 32) & 0xFFFFFFFF
        white = ((rnd / 2147483648.0) - 1.0)
        lp = 0.88 * lp + 0.12 * white
        dither = int(round(lp * 14.0)) & ~1
        if dither:
            sample = struct.unpack_from("<h", frames, 2 * i)[0]
            mixed = max(-32767, min(32767, sample + dither))
            struct.pack_into("<h", frames, 2 * i, mixed)


def render_melodic_wav(payload: bytes, melody_type: str = "ambient", duration_sec: float = 6.5,
                       sample_rate: int = 44100, style=None) -> bytes:
    """Render 16-bit mono PCM with natural harmonic spectrum and embed the payload at randomized sample positions."""
    from enclave_media import default_audio_style
    from enclave_music import render_pcm

    style = style or default_audio_style(melody_type)
    min_samples = (len(payload) + STEGO_OVERHEAD_BYTES) * 8 + 1000
    total_samples = max(int(duration_sec * sample_rate), min_samples)
    frames = bytearray(render_pcm(style, sample_rate, total_samples))
    _apply_natural_room_floor(frames, sample_rate, getattr(style, "theme", melody_type))

    if payload:
        cover_digest = _wav_lsb_cover_digest(frames)
        positions, bits = _pack_stego_bits(payload, cover_digest, total_samples, b"v4-wav-lsb")
        for pos, bit in zip(positions, bits):
            offset = 2 * pos
            frames[offset] = (frames[offset] & 0xFE) | bit

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return buf.getvalue()


def ask_image_resolution() -> tuple[int, int]:
    """Prompt user to select resolution format for scenic wallpaper"""
    print("\nSelect Carrier Resolution:")
    print("1. 256 x 256 pixels [Default]")
    print("2. 512 x 512 pixels")
    print("3. 1024 x 1024 pixels")
    res_choice = input("Select (1-3, press Enter for 1): ").strip()
    if res_choice == '2':
        return 512, 512
    elif res_choice == '3':
        return 1024, 1024
    else:
        return 256, 256


def save_carrier_files(*files: tuple[str, bytes]) -> bool:
    created = []
    try:
        for path, data in files:
            with open(path, "xb") as carrier:
                created.append(path)
                carrier.write(data)
    except OSError as exc:
        print(f"Unable to save the carrier. Check the output directory and try again. {exc}")
        for path in created:
            try:
                os.remove(path)
            except OSError:
                print(f"Could not remove the incomplete output: {path}")
        return False
    return True


def find_carrier_file(text: str) -> str | None:
    clean = text.strip().strip('"').strip("'")
    if not clean or "\n" in clean or "\r" in clean:
        return None
    if os.path.isfile(clean):
        return os.path.abspath(clean)
    for directory in (get_carrier_save_dir(), get_downloads_dir()):
        candidate = os.path.join(directory, clean)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def find_twin_files(text: str) -> tuple[str | None, str | None]:
    first = find_carrier_file(text)
    if first:
        return first, None
    tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', text)
    if len(tokens) == 2:
        first, second = (find_carrier_file(token) for token in tokens)
        if first and second:
            return first, second
    return None, None


def extract_media_payload(data: bytes) -> bytes | None:
    if data.startswith(b"BM"):
        return extract_bmp_carrier(data)
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return extract_wav_carrier(data)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        from enclave_media import convert_image
        return extract_bmp_carrier(convert_image(data, "BMP"))
    if data.startswith(b"fLaC"):
        from enclave_media import convert_audio
        return extract_wav_carrier(convert_audio(data, "WAV"))
    return None


def choose_menu(title: str, options: dict[str, str]) -> str:
    print(f"\n{title}")
    for key, label in options.items():
        print(f"  {key}. {label}")
    print("  0. Back")
    while True:
        choice = input("Select an option: ").strip().lower()
        if choice in options or choice == "0":
            return choice
        print("Choose one of the listed options.")


def detect_decryption_mode(text: str) -> str | None:
    from enclave_workflow import DECRYPT_SHORTCUTS, read_carriers

    try:
        return DECRYPT_SHORTCUTS[read_carriers(text)[0].mode]
    except (ValueError, OSError, EOFError, struct.error, wave.Error):
        return None


def choose_operation() -> tuple[str, str | None]:
    print("\nSHADOW ENCLAVE V4")
    print("  1. Encrypt    2. Decrypt    3. Settings    4. Format & About    5. Exit")
    choice = input("Select an option: ").strip().lower()
    if choice == "1":
        while True:
            mode = choose_menu("Encrypt", {
                "c": "Camouflage Text (DeepSeek)", "k": "Single Carrier (Offline)", "m": "Two Carriers (Offline)",
            })
            if mode != "c":
                return mode, None
            mode = choose_menu("Camouflage Text (DeepSeek)", {"c": "Single Text", "g": "Split into Two Texts"})
            if mode != "0":
                return mode, None
    if choice == "2":
        while True:
            mode = choose_menu("Decrypt", {
                "1": "Detect Format", "d": "Camouflage Text", "l": "Single Carrier", "n": "Two Carriers",
            })
            if mode == "d":
                mode = choose_menu("Camouflage Text (V4)", {"d": "Single Text", "h": "Split Camouflage Text"})
                if mode == "0":
                    continue
            if mode != "1":
                return mode, None

            text = input_multiline(
                "Paste ciphertext or enter a carrier path. Press Enter on an empty prompt to use the clipboard. Finish pasted text with a blank line.",
                multiline_only=True,
            )
            if not text:
                continue
            detected = detect_decryption_mode(text)
            if detected:
                return detected, text
            print("Only Shadow Enclave V4 carriers are supported.")
    if choice == "4":
        from enclave_crypto import PROTECTION_LABEL
        from enclave_workflow import LOCAL_FEATURES

        print(f"Carrier Protection: {PROTECTION_LABEL}")
        print(f"Local App Protection: {LOCAL_FEATURES}")
        print(f"Author: {AUTHOR}")
        print(COPYRIGHT_NOTICE)
        return "0", None
    return {"3": "o", "5": "p"}.get(choice, choice), None


def result_action(copy_text: str | None = None, details: str | None = None, *, second_text: str | None = None) -> bool:
    actions = ["[Enter] Home", "[r] Repeat"]
    if copy_text is not None:
        actions.append("[c] Copy" if second_text is None else "[c] Copy Part A")
    if second_text is not None:
        actions.append("[b] Copy Part B")
    if details:
        actions.append("[d] Details")
    while True:
        try:
            action = input("\n" + "  ".join(actions) + ": ").strip().lower()
        except EOFError:
            return False
        if not action or action == "0":
            return False
        if action == "r":
            return True
        if action == "c" and copy_text is not None:
            print("Copied to clipboard." if set_clipboard_text(copy_text) else "Clipboard unavailable.")
        elif action == "b" and second_text is not None:
            print("Copied to clipboard." if set_clipboard_text(second_text) else "Clipboard unavailable.")
        elif action == "d" and details:
            print(details)
        else:
            print("Choose one of the listed actions.")


def show_files_result(paths: tuple[str, ...], details: str) -> bool:
    print("\nEncryption complete. Saved:")
    for path in paths:
        print(f"  {path}")
    return result_action(details=details)


def show_pair_result(title: str, first: str, second: str) -> bool:
    print(f"\nEncryption complete. {title}. Keep the two parts separate.")
    print(f"\nPart A\n{'-' * 50}\n{first}")
    print(f"\nPart B\n{'-' * 50}\n{second}")
    return result_action(first, second_text=second)


def show_text_result(title: str, text: str, details: str | None = None) -> bool:
    print(f"\n{title}")
    print("-" * 50)
    print(text)
    print("-" * 50)
    return result_action(text, details)


# ==========================================
# Main Program Entry Point
# ==========================================
if __name__ == "__main__":
    # The desktop and terminal share the module's current settings and codecs.
    sys.modules["encryption_code_v4"] = sys.modules[__name__]
    if "--gui" in sys.argv:
        from enclave_desktop import main

        raise SystemExit(main())


if __name__ == '__main__':
    import enclave_broker as broker

    try:
        if not broker.status()["enrolled"]:
            print("Enroll Windows Hello to open Shadow Enclave V4.")
            broker.enroll()
        else:
            print("Verify Windows Hello to open Shadow Enclave V4.")
            broker.open_session()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    repeat_choice = ""
    while True:
        if not broker.session_is_open():
            print("The V4 session closed. Restart the program to verify again.")
            break
        try:
            choice, supplied_input = (repeat_choice, None) if repeat_choice else choose_operation()
        except EOFError:
            break
        repeat_choice = ""
        if choice == "0":
            continue
        if choice in {"c", "d", "g", "h", "k", "l", "m", "n"}:
            from enclave_workflow import run_carrier_cli

            try:
                if run_carrier_cli(choice, supplied_input):
                    repeat_choice = choice
            except EOFError:
                break
            except (ValueError, OSError, struct.error, wave.Error) as exc:
                print(f"Unable to complete the operation. {exc}")

        elif choice == 'o':
            try:
                from enclave_desktop import main as desktop_main

                print("Opening desktop Settings. Windows Hello is required to save changes.")
                desktop_main(open_settings=True)
            except (ImportError, OSError) as exc:
                print(f"Unable to open desktop Settings. {exc}")

        elif choice == 'p':
            print("Goodbye.")
            break

        else:
            print("Invalid input. Please enter a valid menu option.")
