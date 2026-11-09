"""V4 envelope and two-part carrier format."""

import hashlib
import hmac
import math
import re
import secrets
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import enclave_windows as windows

SERVICE_CONTEXT = False

VERSION = 4
SINGLE, TWIN, CAMOUFLAGE, SPLIT_CAMOUFLAGE = 1, 2, 3, 4
SPLIT_MODES = (TWIN, SPLIT_CAMOUFLAGE)
TEXT_MODES = (CAMOUFLAGE, SPLIT_CAMOUFLAGE)
ENVELOPE_MAGIC = b"SENC"
PART_MAGIC = b"SENP"
HEADER = struct.Struct(">4sBB16s16s12s16s")
PART = struct.Struct(">4sBBB16s")
BOUND_FLAG = 0x80
DEVICE = struct.Struct(">16s256s")
TAG_BYTES = 16
MIN_PASSPHRASE = 15
MAX_PASSPHRASE_BYTES = 4096
MAX_PLAINTEXT = 65535
MAX_CAMOUFLAGE = 512
# V4 fixes its KDF profile so untrusted headers cannot specify costly parameters.
MEMORY_KIB, ITERATIONS, LANES = 524_288, 3, 4
PROTECTION_LABEL = "Argon2id (512 MiB, 3 passes) + AES-256-GCM"
# The encrypted body includes the optional typing profile, 2-byte length, UTF-8 plaintext, and 256-byte block padding.
PROFILE = struct.Struct(">BHH8s")
LENGTH_FIELD = struct.Struct(">H")
BLOCK_PADDING_BYTES = 256
_PADDED_FLAG = 0x40
MAX_PADDED_BODY = (
    (PROFILE.size + LENGTH_FIELD.size + MAX_PLAINTEXT + BLOCK_PADDING_BYTES - 1) // BLOCK_PADDING_BYTES
) * BLOCK_PADDING_BYTES
MAX_CAMOUFLAGE_BODY = (
    (PROFILE.size + LENGTH_FIELD.size + MAX_CAMOUFLAGE + BLOCK_PADDING_BYTES - 1) // BLOCK_PADDING_BYTES
) * BLOCK_PADDING_BYTES
MAX_ENVELOPE = HEADER.size + DEVICE.size + MAX_PADDED_BODY + TAG_BYTES
MIN_ENVELOPE = HEADER.size + PROFILE.size + 1 + TAG_BYTES
WIRE_SEED_BYTES = 12  # 8B random seed + 4B authentication tag (replaces 4B magic -> +9B net)
_WIRE_SENC_TAG = 0xA1
_WIRE_SENP_TAG = 0xA2
FORMAT_ERROR = "Only Shadow Enclave V4 carriers are supported."


class AuthenticationError(ValueError):
    """The password or authenticated data was incorrect."""


@dataclass(frozen=True)
class Carrier:
    mode: int
    carrier_id: bytes
    role: int
    data: bytes
    path: str | None = None
    bound: bool = False


@dataclass(frozen=True)
class Envelope:
    mode: int
    carrier_id: bytes
    salt: bytes
    nonce: bytes
    alpha_digest: bytes
    data: bytes
    device: tuple[bytes, bytes] | None = None

    @property
    def bound(self):
        return self.device is not None

    @property
    def header_size(self):
        return HEADER.size + (DEVICE.size if self.bound else 0)


def _password_bytes(password: str) -> bytes:
    try:
        data = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Use a valid UTF-8 passphrase.") from exc
    if not data:
        raise ValueError("Enter a passphrase.")
    if len(data) > MAX_PASSPHRASE_BYTES:
        raise ValueError("Use a passphrase of at most 4,096 UTF-8 bytes.")
    return data


def validate_new_password(password: str) -> None:
    _password_bytes(password)
    if len(password) < MIN_PASSPHRASE or not password.strip():
        raise ValueError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")


def generate_passphrase() -> str:
    """Return 128 random bits as eight short groups of hexadecimal characters."""
    value = secrets.token_hex(16)
    return "-".join(value[index:index + 4] for index in range(0, len(value), 4))


def estimate_passphrase_entropy(password: str) -> tuple[float, str]:
    """Estimate passphrase entropy in bits and return (bits, rating_label)."""
    if not password or not password.strip():
        return 0.0, "Empty"
    text = password.strip()
    if re.fullmatch(r"[0-9a-fA-F]{4}(?:-[0-9a-fA-F]{4}){7}", text):
        return 128.0, "Very Strong"
    pool = 0
    if any("a" <= ch <= "z" for ch in password):
        pool += 26
    if any("A" <= ch <= "Z" for ch in password):
        pool += 26
    if any("0" <= ch <= "9" for ch in password):
        pool += 10
    if any(32 <= ord(ch) < 127 and not ch.isalnum() for ch in password):
        pool += 33
    if any(ord(ch) >= 127 for ch in password):
        pool += 100
    unique_ratio = len(set(password)) / len(password)
    effective_len = len(password) * (0.5 + 0.5 * unique_ratio)
    bits = min(256.0, round(effective_len * math.log2(max(2, pool)), 1))
    if bits < 50:
        rating = "Weak"
    elif bits < 75:
        rating = "Moderate"
    elif bits < 110:
        rating = "Strong"
    else:
        rating = "Very Strong"
    return bits, rating


def format_passphrase_strength(password: str, *, portable: bool = False) -> str:
    """Format estimated passphrase entropy and Portable-mode guidance for display."""
    if not password or not password.strip():
        if portable:
            return "Estimated entropy: 0 bits · Portable mode defaults to a 128-bit random passphrase"
        return "Estimated entropy: 0 bits"
    bits, rating = estimate_passphrase_entropy(password)
    summary = f"Estimated entropy: {bits:.0f} bits ({rating})"
    if portable and bits < 100:
        return f"{summary} · 128-bit random passphrase recommended for Portable mode"
    return summary


def _key(password: bytes, salt: bytes) -> bytes:
    return Argon2id(salt=salt, length=32, iterations=ITERATIONS, lanes=LANES, memory_cost=MEMORY_KIB).derive(password)


def _device_key(password_key: bytes, secret: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b"Shadow Enclave V4 TPM-bound AES-GCM key").derive(password_key + secret)


def _xor(first: bytes, second: bytes) -> bytes:
    return bytes(left ^ right for left, right in zip(first, second, strict=True))


def _digest(part: bytes) -> bytes:
    return hashlib.sha256(part).digest()[:16]


def _wire_mask(seed: bytes, length: int) -> bytes:
    prk = hmac.new(b"ShadowEnclave-V4-Wire-Salt", seed, hashlib.sha256).digest()
    out = bytearray()
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + b"v4-carrier-wire-mask" + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def pack_wire_carrier(data: bytes) -> bytes:
    """Strip plaintext SENC/SENP magic and XOR-mask the entire carrier payload."""
    if data.startswith(ENVELOPE_MAGIC):
        stripped = bytes([_WIRE_SENC_TAG]) + data[len(ENVELOPE_MAGIC):]
    elif data.startswith(PART_MAGIC):
        stripped = bytes([_WIRE_SENP_TAG]) + data[len(PART_MAGIC):]
    else:
        raise ValueError(FORMAT_ERROR)
    seed = bytearray(secrets.token_bytes(8))
    if bytes(seed[:4]) in (ENVELOPE_MAGIC, PART_MAGIC):
        seed[0] ^= 0xFF
    seed_bytes = bytes(seed)
    masked = _xor(stripped, _wire_mask(seed_bytes, len(stripped)))
    tag = hmac.new(b"ShadowEnclave-V4-Wire-Auth", seed_bytes + stripped, hashlib.sha256).digest()[:4]
    return seed_bytes + tag + masked


def unpack_wire_carrier(data: bytes) -> bytes:
    """Restore a wire-masked carrier payload to its canonical SENC/SENP representation."""
    if data.startswith((ENVELOPE_MAGIC, PART_MAGIC)):
        return data
    if len(data) < WIRE_SEED_BYTES + 2:
        raise ValueError(FORMAT_ERROR)
    seed_bytes = data[:8]
    tag = data[8:12]
    masked = data[12:]
    stripped = _xor(masked, _wire_mask(seed_bytes, len(masked)))
    expected_tag = hmac.new(b"ShadowEnclave-V4-Wire-Auth", seed_bytes + stripped, hashlib.sha256).digest()[:4]
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError(FORMAT_ERROR)
    if stripped[0] == _WIRE_SENC_TAG:
        return ENVELOPE_MAGIC + stripped[1:]
    if stripped[0] == _WIRE_SENP_TAG:
        return PART_MAGIC + stripped[1:]
    raise ValueError(FORMAT_ERROR)


def _pad_body(profile: tuple[int, int, int, bytes], raw: bytes) -> bytes:
    """Pack profile + length + plaintext and pad with CSPRNG bytes to a multiple of 256 bytes."""
    flagged_profile = (profile[0] | _PADDED_FLAG, profile[1], profile[2], profile[3])
    unpadded = PROFILE.pack(*flagged_profile) + LENGTH_FIELD.pack(len(raw)) + raw
    padded_len = ((len(unpadded) + BLOCK_PADDING_BYTES - 1) // BLOCK_PADDING_BYTES) * BLOCK_PADDING_BYTES
    return unpadded + secrets.token_bytes(padded_len - len(unpadded))


def seal(plaintext: str, password: str, mode: int, *, bound: bool = False,
         profile: tuple[int, int, int, bytes] = (0, 0, 0, bytes(8)),
         cover: str = "") -> tuple[bytes, tuple[bytes, ...]]:
    if not SERVICE_CONTEXT:
        import base64
        import enclave_broker
        result = enclave_broker.call("encrypt", {
            "text": plaintext, "password": password, "mode": mode, "bound": bound,
            "profile": [*profile[:3], base64.b64encode(profile[3]).decode("ascii")], "cover": cover,
        })
        return (base64.b64decode(result["envelope"], validate=True),
                tuple(base64.b64decode(part, validate=True) for part in result["parts"]))
    validate_new_password(password)
    if mode not in (SINGLE, TWIN, CAMOUFLAGE, SPLIT_CAMOUFLAGE):
        raise ValueError("Invalid V4 encryption options.")
    raw = plaintext.encode("utf-8")
    max_size = MAX_CAMOUFLAGE if mode in TEXT_MODES else MAX_PLAINTEXT
    if not raw or len(raw) > max_size:
        raise ValueError(f"Enter between 1 and {max_size:,} UTF-8 bytes.")
    if profile[0] and not bound:
        raise ValueError("Local typing checks require This PC Only.")
    body = _pad_body(profile, raw)
    carrier_id, salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16), secrets.token_bytes(12)
    key = _key(_password_bytes(password), salt)
    device_data = b""
    if bound:
        device_secret = secrets.token_bytes(32)
        key_id, wrapped = windows.wrap_device_secret(device_secret)
        device_data = DEVICE.pack(key_id, wrapped)
        key = _device_key(key, device_secret, salt)
    wire_mode = mode | BOUND_FLAG if bound else mode
    alpha = secrets.token_bytes(HEADER.size + len(device_data) + len(body) + TAG_BYTES) if mode in SPLIT_MODES else None
    header = HEADER.pack(ENVELOPE_MAGIC, VERSION, wire_mode, carrier_id, salt, nonce,
                         _digest(alpha) if alpha is not None else bytes(16)) + device_data
    ciphertext = AESGCM(key).encrypt(nonce, body, header)
    envelope = header + ciphertext
    if alpha is None:
        return envelope, (envelope,)
    return envelope, (PART.pack(PART_MAGIC, VERSION, wire_mode, 1, carrier_id) + alpha,
                      PART.pack(PART_MAGIC, VERSION, wire_mode, 2, carrier_id) + _xor(alpha, envelope))


def parse_envelope(data: bytes) -> Envelope:
    if not MIN_ENVELOPE <= len(data) <= MAX_ENVELOPE:
        raise ValueError(FORMAT_ERROR)
    magic, version, wire_mode, carrier_id, salt, nonce, alpha_digest = HEADER.unpack_from(data)
    mode = wire_mode & ~BOUND_FLAG
    bound = bool(wire_mode & BOUND_FLAG)
    if magic != ENVELOPE_MAGIC or version != VERSION or mode not in (SINGLE, TWIN, CAMOUFLAGE, SPLIT_CAMOUFLAGE):
        raise ValueError(FORMAT_ERROR)
    extra = DEVICE.size if bound else 0
    max_body = MAX_CAMOUFLAGE_BODY if mode in TEXT_MODES else MAX_PADDED_BODY
    maximum = HEADER.size + max_body + TAG_BYTES
    if ((mode not in SPLIT_MODES and alpha_digest != bytes(16))
            or not MIN_ENVELOPE + extra <= len(data) <= maximum + extra):
        raise ValueError("Invalid V4 envelope.")
    device = DEVICE.unpack_from(data, HEADER.size) if bound else None
    return Envelope(mode, carrier_id, salt, nonce, alpha_digest, data, device)


def parse_carrier(data: bytes, path: str | None = None) -> Carrier:
    if not data.startswith((ENVELOPE_MAGIC, PART_MAGIC)):
        data = unpack_wire_carrier(data)
    if data.startswith(ENVELOPE_MAGIC):
        envelope = parse_envelope(data)
        if envelope.mode in SPLIT_MODES:
            raise ValueError("This V4 format requires two separate carriers.")
        return Carrier(envelope.mode, envelope.carrier_id, 0, data, path, envelope.bound)
    if not data.startswith(PART_MAGIC) or not PART.size + MIN_ENVELOPE <= len(data) <= PART.size + MAX_ENVELOPE:
        raise ValueError(FORMAT_ERROR)
    _, version, wire_mode, role, carrier_id = PART.unpack_from(data)
    mode = wire_mode & ~BOUND_FLAG
    bound = bool(wire_mode & BOUND_FLAG)
    if (version != VERSION or mode not in SPLIT_MODES or role not in (1, 2)
            or (bound and len(data) < PART.size + MIN_ENVELOPE + DEVICE.size)):
        raise ValueError(FORMAT_ERROR)
    return Carrier(mode, carrier_id, role, data[PART.size:], path, bound)


def join_parts(carriers: tuple[Carrier, ...]) -> Envelope:
    if len(carriers) == 1 and not carriers[0].role:
        envelope = parse_envelope(carriers[0].data)
        if envelope.mode in SPLIT_MODES:
            raise ValueError("Both V4 carrier parts are required.")
        return envelope
    if len(carriers) != 2:
        raise ValueError("Both V4 carrier parts are required.")
    first, second = sorted(carriers, key=lambda item: item.role)
    if ((first.role, second.role) != (1, 2) or first.mode != second.mode
            or first.bound != second.bound or first.carrier_id != second.carrier_id or len(first.data) != len(second.data)):
        raise ValueError("The carriers do not match. Select Alpha and Beta from the same pair.")
    try:
        envelope = parse_envelope(_xor(first.data, second.data))
    except ValueError as exc:
        raise ValueError("The V4 pair is damaged or mismatched.") from exc
    if (envelope.mode != first.mode or envelope.bound != first.bound or envelope.carrier_id != first.carrier_id
            or not hmac.compare_digest(envelope.alpha_digest, _digest(first.data))):
        raise ValueError("The V4 pair is damaged or mismatched.")
    return envelope


def unseal(envelope: Envelope, password: str) -> tuple[str, tuple[int, int, int, bytes]]:
    if not SERVICE_CONTEXT:
        raise AuthenticationError("Use the V4 broker to authorize decryption.")
    try:
        secret = _password_bytes(password)
    except ValueError as exc:
        raise AuthenticationError("Incorrect passphrase or altered V4 data.") from exc
    device_secret = windows.unwrap_device_secret(*envelope.device) if envelope.device is not None else None
    key = _key(secret, envelope.salt)
    if device_secret is not None:
        key = _device_key(key, device_secret, envelope.salt)
    try:
        body = AESGCM(key).decrypt(
            envelope.nonce, envelope.data[envelope.header_size:], envelope.data[:envelope.header_size]
        )
    except InvalidTag as exc:
        raise AuthenticationError("Incorrect passphrase or altered V4 data.") from exc
    raw_level, mean, std, cadence = PROFILE.unpack_from(body)
    is_padded = bool(raw_level & _PADDED_FLAG)
    level = raw_level & ~_PADDED_FLAG
    if level not in (0, 1, 2, 3):
        raise ValueError("Invalid authenticated typing profile.")
    if is_padded:
        if len(body) < BLOCK_PADDING_BYTES or len(body) % BLOCK_PADDING_BYTES != 0:
            raise ValueError("Invalid authenticated padded body.")
        (plain_len,) = LENGTH_FIELD.unpack_from(body, PROFILE.size)
        plain_start = PROFILE.size + LENGTH_FIELD.size
        max_plain = MAX_CAMOUFLAGE if envelope.mode in TEXT_MODES else MAX_PLAINTEXT
        if not 1 <= plain_len <= min(len(body) - plain_start, max_plain):
            raise ValueError("Invalid authenticated plaintext length.")
        return body[plain_start:plain_start + plain_len].decode("utf-8"), (level, mean, std, cadence)
    return body[PROFILE.size:].decode("utf-8"), (level, mean, std, cadence)
