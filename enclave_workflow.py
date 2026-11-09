"""Shared carrier workflows for the terminal and desktop interfaces."""

import base64
import io
import json
import math
import os
import re
import urllib.error
import urllib.request
import struct
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import encryption_code_v4 as core
import enclave_media as media
import enclave_crypto as crypto
import enclave_cadence as cadence
import enclave_windows as windows


@dataclass(frozen=True)
class PasswordSample:
    text: str = ""
    mean: float = 160.0
    std: float = 35.0
    intervals: tuple[float, ...] = ()


@dataclass(frozen=True)
class Protection:
    password: PasswordSample
    wavelet: bool = True
    cadence_level: int = 0
    cover: str = "none"
    bound: bool = False


@dataclass(frozen=True)
class OutputSpec:
    format: str
    style: media.ImageStyle | media.AudioStyle | None = None
    size: int = 512
    sample_rate: int = 44100


@dataclass(frozen=True)
class CarrierOutput:
    files: tuple[str, ...] = ()
    texts: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    bound: bool = False
    formats: tuple[str, ...] = ()


def available_formats():
    return ("BMP", "PNG", "WAV", "FLAC", "Text Armor") if media.flac_available() else ("BMP", "PNG", "WAV", "Text Armor")


def style_catalog(kind):
    if kind == "image":
        return core.VISUAL_THEMES_CATALOG | media.IMAGE_COMPOSITIONS
    return core.MELODY_STYLES_CATALOG | media.AUDIO_COMPOSITIONS


def kind_of(fmt):
    if fmt in media.IMAGE_FORMATS:
        return "image"
    if fmt in media.AUDIO_FORMATS:
        return "audio"
    return "text"


def default_style(kind, theme):
    return media.ImageStyle(theme) if kind == "image" else media.default_audio_style(theme)


def render_preview(style):
    if isinstance(style, media.ImageStyle):
        return core.render_scenic_bmp(b"", width=256, height=256, style=style)
    return core.render_melodic_wav(b"", duration_sec=2.4, sample_rate=22050, style=style)


MODES = {
    crypto.SINGLE: "Single Carrier",
    crypto.TWIN: "Two Carriers",
    crypto.CAMOUFLAGE: "Camouflage Text",
    crypto.SPLIT_CAMOUFLAGE: "Split Camouflage Text",
}
DECRYPT_SHORTCUTS = dict(zip(MODES, ("l", "n", "d", "h"), strict=True))


PORTABLE_LABEL = "Portable — Ready to Share"
BOUND_LABEL = "This PC Only — Device Bound"
BOUND_WARNING = (
    "Keep the original PC, Windows account, enrolled Hello credential, service data, and TPM key. "
    "Clearing the TPM or losing its key can make this carrier unrecoverable."
)
LOCAL_FEATURES = (
    "This PC Only: Hello and retry protection are enforced by the V4 service. "
    "Portable: recipients can decrypt with the passphrase; local Hello and retry rules do not travel. "
    "Typing is an auxiliary check, not a biometric security boundary."
)
COOLDOWN_FAILURES = 3
COOLDOWN_SECONDS = 30


def cooldown_enabled():
    return core.load_enclave_config().get("app_retry_cooldown", True) is True


def cooldown_label():
    return f"Service retry protection: {COOLDOWN_SECONDS}s after {COOLDOWN_FAILURES} failures per Windows account; 1s minimum delay"


def sharing_message(bound, count, *, camouflage=False):
    if bound:
        parts = " Both parts are required." if count == 2 else ""
        return f"{BOUND_LABEL} · Requires the original PC's TPM key, Windows account, and passphrase.{parts}"
    what = "both parts and the passphrase" if count == 2 else "the carrier and the passphrase"
    handling = (" Send unchanged .txt attachments. Recipients decrypt locally in V4; DeepSeek is used to create the template." if camouflage
                else " Send original media files as attachments and keep carrier text unchanged.")
    return f"{PORTABLE_LABEL} · Recipient needs V4, {what}.{handling}"


def _profile(protection):
    level = protection.cadence_level
    if level == 0:
        return 0, 0, 0, bytes(8)
    sample = protection.password
    if (level not in cadence.BIOMETRIC_THRESHOLDS or len(sample.intervals) < 2
            or not math.isfinite(sample.mean) or not 12 <= sample.mean <= 6553.5
            or not math.isfinite(sample.std) or not 0 <= sample.std <= 6553.5
            or any(not math.isfinite(value) or value <= 0 for value in sample.intervals)):
        raise ValueError("Retype the passphrase to record a usable typing profile.")
    signature = core.compute_cadence_sig(sample.intervals, sample.mean)
    return level, round(sample.mean * 10), round(sample.std * 10), signature


def _armor(data):
    wire = crypto.pack_wire_carrier(data)
    return "-----BEGIN SHADOW ENCLAVE V4-----\n" + base64.encodebytes(wire).decode("ascii") + "-----END SHADOW ENCLAVE V4-----"


def encrypt_carriers(text, protection, specs, directory):
    if len(specs) not in (1, 2) or any(spec.format not in available_formats() for spec in specs):
        raise ValueError("Choose one or two supported output formats.")
    if protection.cadence_level and not protection.bound:
        raise ValueError("Local typing checks require This PC Only.")
    if protection.cover not in {"none", "local", "ai"}:
        raise ValueError("Select a supported local protection option.")
    api_key = core.get_deepseek_api_key() if protection.cover == "ai" else None
    if protection.cover == "ai" and not api_key:
        raise ValueError("AI Cover Document requires DeepSeek. Add an API key in Settings.")
    mode = crypto.TWIN if len(specs) == 2 else crypto.SINGLE
    if protection.cover == "ai":
        cover = core.generate_dynamic_ai_decoy(api_key, int(time.time_ns() & 0xFFFFFFFF))
    elif protection.cover == "local":
        cover = core.generate_offline_mirror_decoy(text)
    else:
        cover = ""
    envelope, payloads = crypto.seal(text, protection.password.text, mode, bound=protection.bound,
                                     profile=_profile(protection), cover=cover)
    carrier_id = crypto.parse_envelope(envelope).carrier_id
    files = []
    armors = []
    details = []
    twin = len(specs) == 2
    for index, (spec, payload) in enumerate(zip(specs, payloads, strict=True)):
        part = ("alpha", "beta")[index]
        stem = f"twin_{part}_{carrier_id.hex()}" if twin else f"carrier_{carrier_id.hex()}"
        label = f"Part {part.title()}" if twin else "Carrier"
        if spec.format == "Text Armor":
            armors.append(_armor(payload))
            details.append(f"{label}: Text Armor")
        elif spec.format in media.IMAGE_FORMATS:
            data = core.render_scenic_bmp(
                payload, width=spec.size, height=spec.size, use_dwt=protection.wavelet,
                style=spec.style or media.ImageStyle("sunset")
            )
            files.append((os.path.join(directory, f"{stem}.{spec.format.lower()}"), media.convert_image(data, spec.format)))
            width, height = struct.unpack_from("<II", data, 18)
            details.append(f"{label}: {spec.format}, {width} x {height} pixels, 24-bit RGB")
        else:
            data = core.render_melodic_wav(
                payload, sample_rate=spec.sample_rate, style=spec.style or media.default_audio_style("ambient")
            )
            files.append((os.path.join(directory, f"{stem}.{spec.format.lower()}"), media.convert_audio(data, spec.format)))
            with wave.open(io.BytesIO(data)) as pcm:
                duration = pcm.getnframes() / pcm.getframerate()
            details.append(f"{label}: {spec.format}, {spec.sample_rate:,} Hz, 16-bit mono, {duration:.1f}s")
    if files and not core.save_carrier_files(*files):
        raise OSError("Unable to write the complete output. Check the output folder and try again.")
    return CarrierOutput(tuple(path for path, _ in files), tuple(armors), tuple(details),
                         protection.bound, tuple(spec.format for spec in specs))


_ARMOR = re.compile(r"-----BEGIN SHADOW ENCLAVE V4-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END SHADOW ENCLAVE V4-----")
_MAX_TEXT_CARRIER = 150_000
_CAMOUFLAGE_LINES = 24


def _encode_camouflage(data, templates):
    wire = crypto.pack_wire_carrier(data)
    framed = len(wire).to_bytes(2, "big") + wire
    encoded = "".join(chr(65 + byte // 26) + chr(65 + byte % 26) for byte in framed)
    return "\n".join(
        core.ensure_line_quad(templates[(i // 4) % len(templates)], list(encoded[i:i + 4].ljust(4, "A")))
        for i in range(0, len(encoded), 4)
    )


def _parse_camouflage(text, *, path=None, max_parts=1):
    if len(text) > max_parts * _MAX_TEXT_CARRIER + 2:
        raise ValueError(crypto.FORMAT_ERROR)
    values = bytearray()
    limit = (
        crypto.PART.size + crypto.HEADER.size + crypto.DEVICE.size
        + crypto.MAX_CAMOUFLAGE_BODY + crypto.TAG_BYTES + crypto.WIRE_SEED_BYTES
    )
    for line in text.splitlines():
        if not line.strip():
            continue
        words = re.findall(r"[A-Za-z]+", line)
        if len(words) < 6 or len(values) >= max_parts * (limit + 3):
            raise ValueError(crypto.FORMAT_ERROR)
        for first, second in ((words[0], words[1]), (words[-2], words[-1])):
            byte = (ord(first[0].upper()) - 65) * 26 + ord(second[0].upper()) - 65
            if byte > 255:
                raise ValueError(crypto.FORMAT_ERROR)
            values.append(byte)
    carriers = []
    cursor = 0
    while cursor < len(values):
        size = int.from_bytes(values[cursor:cursor + 2], "big")
        start = cursor + 2
        end = start + size
        cursor = end + size % 2
        if (len(carriers) >= max_parts or not 0 < size <= limit or cursor > len(values)
                or (size % 2 and values[end] != 0)):
            raise ValueError(crypto.FORMAT_ERROR)
        carrier = crypto.parse_carrier(bytes(values[start:end]), path)
        if carrier.mode not in crypto.TEXT_MODES:
            raise ValueError(crypto.FORMAT_ERROR)
        carriers.append(carrier)
    if not carriers:
        raise ValueError(crypto.FORMAT_ERROR)
    return tuple(carriers)


def _read_carrier(source):
    path = core.find_carrier_file(source)
    if path:
        target = Path(path)
        if target.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("This carrier exceeds the V4 file size limit.")
        data = target.read_bytes()
        embedded = core.extract_media_payload(data)
        if embedded is not None:
            carrier = crypto.parse_carrier(embedded, path)
            if carrier.mode in crypto.TEXT_MODES:
                raise ValueError(crypto.FORMAT_ERROR)
            return carrier
        if len(data) > _MAX_TEXT_CARRIER:
            raise ValueError(crypto.FORMAT_ERROR)
        source = data.decode("utf-8-sig", errors="replace").strip()
    if len(source) > _MAX_TEXT_CARRIER:
        raise ValueError(crypto.FORMAT_ERROR)
    armor = _ARMOR.fullmatch(source.strip())
    if not armor:
        return _parse_camouflage(source, path=path)[0]
    binary = base64.b64decode("".join(armor[1].split()), validate=True)
    carrier = crypto.parse_carrier(binary, path)
    if carrier.mode in crypto.TEXT_MODES:
        raise ValueError(crypto.FORMAT_ERROR)
    return carrier


def _find_partner(carrier):
    if not carrier.path:
        return None
    source = Path(carrier.path)
    swaps = {"alpha": "beta", "beta": "alpha", "vision": "audio", "audio": "vision"}
    name = source.stem
    for role, partner in swaps.items():
        if f"_{role}_" in name:
            other = name.replace(f"_{role}_", f"_{partner}_", 1)
            break
    else:
        return None
    for directory in dict.fromkeys((source.parent, Path(core.get_carrier_save_dir()), Path(core.get_downloads_dir()))):
        for ext in dict.fromkeys((source.suffix.lower(), *media.CARRIER_EXTENSIONS)):
            candidate = directory / (other + ext)
            if candidate.is_file():
                try:
                    item = _read_carrier(str(candidate))
                except (OSError, ValueError):
                    continue
                if (item.carrier_id == carrier.carrier_id and item.mode == carrier.mode
                    and item.bound == carrier.bound and {item.role, carrier.role} == {1, 2}):
                    return item
    return None


def read_carriers(first, second=""):
    """Read a complete single carrier, a matched pair, or a first part awaiting its partner."""
    if not first.strip():
        first, second = second, ""
        if not first.strip():
            raise ValueError("Select a carrier file or paste its carrier text.")
    armors = list(_ARMOR.finditer(first)) if len(first) <= _MAX_TEXT_CARRIER * 2 + 1 else []
    first_path, second_path = core.find_twin_files(first) if not armors else (None, None)
    if second_path and second.strip():
        raise ValueError("Select a single pair of V4 carriers.")
    if len(armors) > 1:
        if len(armors) != 2 or second.strip() or "".join(_ARMOR.split(first)[::2]).strip():
            raise ValueError("Select a single pair of V4 carriers.")
        first, second = (match[0] for match in armors)
    if not (armors or first_path or second.strip()):
        first_carrier, *combined = _parse_camouflage(first, max_parts=2)
        if combined:
            items = tuple(sorted((first_carrier, combined[0]), key=lambda item: item.role))
            crypto.join_parts(items)
            return items
    else:
        first_carrier = _read_carrier(first_path or first)
    if not first_carrier.role:
        if second.strip() or second_path:
            raise ValueError("This V4 format uses only one carrier.")
        return (first_carrier,)
    partner = _read_carrier(second_path or second) if second.strip() or second_path else _find_partner(first_carrier)
    if partner is None:
        return (first_carrier,)
    items = tuple(sorted((first_carrier, partner), key=lambda item: item.role))
    crypto.join_parts(items)
    return items


def save_camouflage_pair(texts, directory):
    paired = sorted(((_parse_camouflage(text)[0], text) for text in texts), key=lambda item: item[0].role)
    first, _ = paired[0]
    crypto.join_parts(tuple(carrier for carrier, _ in paired))
    if first.mode != crypto.SPLIT_CAMOUFLAGE:
        raise ValueError("Select a matching split camouflage text pair.")
    stem = first.carrier_id.hex()
    paths = tuple(str(Path(directory) / f"letter_{role}_{stem}.txt") for role in ("alpha", "beta"))
    files = tuple((path, item[1].encode("utf-8")) for path, item in zip(paths, paired, strict=True))
    if not core.save_carrier_files(*files):
        raise OSError("Unable to save the text pair. Select a different folder and try again.")
    return paths


def decrypt_carriers(carriers, password, *, owner_hwnd=None):
    import enclave_broker as broker
    envelope = crypto.join_parts(carriers)
    result = broker.call("decrypt", {
        "envelope": base64.b64encode(envelope.data).decode("ascii"), "password": password.text,
        "typing": {"mean": password.mean, "std": password.std, "intervals": list(password.intervals)},
    }, owner_hwnd=owner_hwnd)
    return result["text"]


def _fetch_templates(api_key, count):
    if not api_key:
        raise ValueError("Camouflage Text requires DeepSeek. Add an API key in Settings.")
    keys = ("first", "second")[:count]
    example = {key: ["Amber leaves drift slowly above the quiet river"] for key in keys}
    prompt = (
        f"Return a JSON object shaped like {json.dumps(example)}. "
        f"Write {_CAMOUFLAGE_LINES} different lines of tranquil English nature prose per array. "
        "Each line must contain seven to twelve English words. "
        "Use plain letters and spaces, with no title, bullets, or commentary. "
        "For two arrays, use different settings: a woodland in the first and a coastline in the second."
    )
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps({"model": "deepseek-flash", "max_tokens": 1200 if count == 1 else 2200,
                         "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            choice = json.load(response)["choices"][0]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("DeepSeek rejected access. Check the API key in Settings.") from exc
        if exc.code == 402:
            raise ValueError("DeepSeek reported insufficient balance.") from exc
        if exc.code == 429:
            raise ValueError("DeepSeek is rate limiting requests. Try again shortly.") from exc
        raise ValueError(f"DeepSeek returned HTTP {exc.code}. Try again later.") from exc
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise ValueError("Unable to obtain a DeepSeek text template. Check Settings and try again.") from exc
    if isinstance(choice, dict) and choice.get("finish_reason") == "length":
        raise ValueError("DeepSeek truncated the text templates. Try again.")
    try:
        document = json.loads(choice["message"]["content"])
        groups = []
        for key in keys:
            group = document[key]
            if not isinstance(group, list):
                raise ValueError("Expected a list of lines.")
            lines = []
            for line in group[:_CAMOUFLAGE_LINES]:
                if core.cover_document_is_usable(line):
                    words = re.findall(r"[A-Za-z]+", line)[:12]
                    if len(words) >= 6:
                        lines.append(" ".join(words))
            if not lines:
                raise ValueError("Empty template.")
            groups.append(lines)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("DeepSeek did not return usable text templates. Try again.") from exc
    return groups


def encrypt_camouflage(text, api_key, *, split=False, password="", bound=False):
    if not api_key:
        raise ValueError("Camouflage Text requires DeepSeek. Add an API key in Settings.")
    mode = crypto.SPLIT_CAMOUFLAGE if split else crypto.CAMOUFLAGE
    _, packets = crypto.seal(text, password, mode, bound=bound)
    groups = _fetch_templates(api_key, len(packets))
    return tuple(_encode_camouflage(data, lines) for data, lines in zip(packets, groups, strict=True))


def _choose_output_cli(twin):
    formats = {
        "1": ("BMP", "BMP"), "2": ("WAV", "WAV"), "3": ("BMP", "WAV"), "4": ("Text Armor", "Text Armor"),
        "5": ("PNG", "PNG"), "6": ("FLAC", "FLAC"), "7": ("PNG", "FLAC"),
    } if twin else {
        "1": ("BMP",), "2": ("WAV",), "3": ("Text Armor",), "4": ("PNG",), "5": ("FLAC",),
    }
    available = available_formats()
    formats = {key: group for key, group in formats.items() if all(fmt in available for fmt in group)}
    directory = core.get_carrier_save_dir()
    while True:
        print(f"\nOutput folder: {directory}")
        choices = {key: " + ".join(group) for key, group in formats.items()}
        choice = core.choose_menu("Output Format", choices | {"s": "Change Output Folder"})
        if choice == "0":
            return None
        if choice != "s":
            return formats[choice], directory
        directory = core.prompt_configure_save_dir()


def _output_spec_cli(fmt, index):
    kind = kind_of(fmt)
    if kind == "text":
        return OutputSpec(fmt)
    catalog = style_catalog(kind)
    codes = tuple(catalog)
    default = ("sunset", "emerald")[index] if kind == "image" else ("ambient", "zen")[index]
    print(f"\nPart {'AB'[index]} / {kind.title()} Theme")
    for number, theme in enumerate(codes, 1):
        print(f"  {number:2}. {catalog[theme]['name']}")
    selected = input(f"Select a theme (Enter for {catalog[default]['name']}): ").strip()
    if selected.isascii() and selected.isdigit() and len(selected) < 4 and 1 <= int(selected) <= len(codes):
        default = codes[int(selected) - 1]
    size = core.ask_image_resolution()[0] if kind == "image" else 512
    return OutputSpec(fmt, default_style(kind, default), size)


def _read_password_cli(prompt):
    text, mean, std, intervals = core.capture_keystroke_password(prompt)
    return PasswordSample(text, mean, std, tuple(intervals))


def _new_password_cli(cadence_level=0, *, portable=False):
    if cadence_level:
        pwd, mean, std, intervals = core.enroll_biometric_password_3pass("Passphrase", allow_empty=False)
        sample = PasswordSample(pwd, mean, std, tuple(intervals))
    elif portable:
        choice = input("Press Enter to generate a 128-bit random passphrase (recommended for Portable), or type C for custom: ").strip().lower()
        if choice != "c":
            generated = crypto.generate_passphrase()
            print(f"Generated passphrase: {generated}")
            print("Save it securely. This console may retain the displayed passphrase in its scrollback.")
            sample = PasswordSample(generated)
        else:
            sample = _read_password_cli("Enter a new passphrase (15+ characters): ")
            crypto.validate_new_password(sample.text)
            if sample.text != _read_password_cli("Confirm passphrase: ").text:
                raise ValueError("Passphrases do not match.")
    else:
        if input("Enter for a custom passphrase or type G to generate one: ").strip().lower() == "g":
            generated = crypto.generate_passphrase()
            print(f"Generated passphrase: {generated}")
            print("Save it securely. This console may retain the displayed passphrase in its scrollback.")
            sample = PasswordSample(generated)
        else:
            sample = _read_password_cli("Enter a new passphrase (15+ characters): ")
        crypto.validate_new_password(sample.text)
        if sample.text != _read_password_cli("Confirm passphrase: ").text:
            raise ValueError("Passphrases do not match.")
    crypto.validate_new_password(sample.text)
    print(crypto.format_passphrase_strength(sample.text, portable=portable))
    return sample


def _choose_sharing_cli():
    print(f"\nSharing & Device Binding\n  1. {PORTABLE_LABEL}")
    print("     A recipient can decrypt using V4 and the passphrase.")
    available = windows.tpm_available()
    if available:
        print(f"  2. {BOUND_LABEL}")
        print(f"     {BOUND_WARNING}")
    else:
        print(f"  {BOUND_LABEL} (TPM 2.0 unavailable)")
    print(f"  {LOCAL_FEATURES}")
    while True:
        prompt = "Select [1: Portable, 2: This PC Only, 0: Back]: " if available else "Select [1: Portable, 0: Back]: "
        choice = input(prompt).strip() or "1"
        if choice == "0":
            return None
        if choice == "1":
            return False
        if choice == "2" and available:
            return True
        print("Select an available sharing option.")


def _cli_protection(has_image, bound):
    wavelet, level, cover = has_image, 0, "none"
    if input("Press Enter for defaults, or type A for advanced options: ").strip().lower() == "a":
        if has_image:
            wavelet = input("Use wavelet embedding? [Y/n]: ").strip().lower() != "n"
        if bound:
            level = int(input("Local typing check [0: Off, 1: Standard, 2: Strict, 3: Flexible]: ").strip() or "0")
            if level not in (0, 1, 2, 3):
                raise ValueError("Select a valid typing profile.")
        else:
            print("Typing check: Available with This PC Only.")
        cover = {"1": "local", "2": "ai"}.get(input(
            "Alternate document [0: Off, 1: Local, 2: AI (Requires DeepSeek)]: "
        ).strip(), "none")
    return Protection(_new_password_cli(level, portable=not bound), wavelet, level, cover, bound)


def _decrypt_cli(choice, supplied_input):
    mode = next(mode for mode, shortcut in DECRYPT_SHORTCUTS.items() if shortcut == choice)
    print(f"\nDecrypt / {MODES[mode]} (V4)")
    first = supplied_input or core.input_multiline(
        "Enter a carrier path or paste V4 text. Finish pasted text with a blank line:",
        multiline_only=True,
    )
    if not first:
        return False
    items = read_carriers(first)
    if items[0].mode != mode:
        raise ValueError(f"Select {MODES[items[0].mode]} under Decrypt.")
    if items[0].role and len(items) == 1:
        found = "Alpha" if items[0].role == 1 else "Beta"
        second = core.input_multiline(f"Found {found}. Select the matching carrier:", multiline_only=True)
        if not second:
            return False
        items = read_carriers(first, second)
    if len(items) == 2:
        print("V4 carrier pair matched.")
    print(sharing_message(items[0].bound, len(items), camouflage=mode in crypto.TEXT_MODES))
    sample = _read_password_cli("Enter passphrase: ")
    print(f"Typing sample: {core.console_keystroke_metrics(sample.text, sample.mean, sample.std, sample.intervals)}")
    text = decrypt_carriers(items, sample)
    return core.show_text_result("Decryption complete", text, f"Format: {MODES[mode]} (V4)")


def run_carrier_cli(choice, supplied_input):
    if choice in DECRYPT_SHORTCUTS.values():
        return _decrypt_cli(choice, supplied_input)
    if choice in {"c", "g"}:
        text = input("Enter plaintext (leave blank to return):\n")
        if not text:
            return False
        api_key = core.get_deepseek_api_key()
        if not api_key:
            raise ValueError("Camouflage Text requires DeepSeek. Add an API key in Settings.")
        bound = _choose_sharing_cli()
        if bound is None:
            return False
        sample = _new_password_cli(portable=not bound)
        texts = encrypt_camouflage(text, api_key, split=choice == "g", password=sample.text, bound=bound)
        print(sharing_message(bound, len(texts), camouflage=True))
        return core.show_pair_result("Two V4 camouflage texts", *texts) if len(texts) == 2 else core.show_text_result(
            "Encryption complete. V4 camouflage text", texts[0]
        )

    twin = choice == "m"
    print("\nEncrypt / " + ("Two Carriers (V4)" if twin else "Single Carrier (V4)"))
    text = input("Enter plaintext (leave blank to return):\n")
    if not text:
        return False
    selection = _choose_output_cli(twin)
    if selection is None:
        return False
    formats, directory = selection
    bound = _choose_sharing_cli()
    if bound is None:
        return False
    specs = tuple(_output_spec_cli(fmt, index) for index, fmt in enumerate(formats))
    protection = _cli_protection(any(fmt in media.IMAGE_FORMATS for fmt in formats), bound)
    print(f"\nReview V4 encryption: {' + '.join(formats)}")
    if any(fmt != "Text Armor" for fmt in formats):
        print(f"Output folder: {directory}")
    print(f"Carrier protection: {crypto.PROTECTION_LABEL}")
    print(sharing_message(bound, len(formats)))
    if bound:
        print(BOUND_WARNING)
    print(f"Local app protection: {cooldown_label()}")
    if input("Press Enter to encrypt, or type 0 to cancel: ").strip():
        return False
    output = encrypt_carriers(text, protection, specs, directory)
    print(sharing_message(output.bound, len(output.formats)))
    for detail in output.details:
        if detail.endswith("could not be saved."):
            print(detail)
    if len(output.texts) == 2:
        return core.show_pair_result("Two V4 text armors", *output.texts)
    if output.texts:
        return core.show_text_result("Encryption complete. V4 text armor", output.texts[0])
    return core.show_files_result(output.files, "\n".join(output.details))
