"""Lossless carrier conversion and validated local rendering recipes."""

import io
import json
import math
import re
import urllib.request
from dataclasses import dataclass

from PIL import Image

IMAGE_FORMATS = ("BMP", "PNG")
AUDIO_FORMATS = ("WAV", "FLAC")
CARRIER_EXTENSIONS = (".bmp", ".png", ".wav", ".flac")
MAX_IMAGE_PIXELS = 4_000_000
MAX_AUDIO_FRAMES = 1_000_000


def flac_available():
    try:
        import soundfile
        return soundfile.check_format("FLAC", "PCM_16")
    except (ImportError, OSError):
        return False


def convert_image(data: bytes, target: str) -> bytes:
    if target == "BMP" and data.startswith(b"BM"):
        return data
    if target not in IMAGE_FORMATS:
        raise ValueError("Select BMP or PNG.")
    out = io.BytesIO()
    with Image.open(io.BytesIO(data), formats=["BMP", "PNG"]) as image:
        if image.mode != "RGB" or image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("The image carrier must contain 24-bit RGB pixels.")
        image.save(out, format=target)
    return out.getvalue()


def convert_audio(data: bytes, target: str) -> bytes:
    if target == "WAV" and data.startswith(b"RIFF"):
        return data
    if target not in AUDIO_FORMATS:
        raise ValueError("Select WAV or FLAC.")
    try:
        import soundfile
    except (ImportError, OSError) as exc:
        raise ValueError("FLAC requires SoundFile. Install the desktop dependencies.") from exc
    out = io.BytesIO()
    try:
        with soundfile.SoundFile(io.BytesIO(data)) as audio:
            if (audio.format not in AUDIO_FORMATS or audio.subtype != "PCM_16"
                    or audio.channels != 1 or audio.frames > MAX_AUDIO_FRAMES):
                raise ValueError("The audio carrier must be 16-bit mono PCM.")
            samples = audio.buffer_read(audio.frames, dtype="int16")
            with soundfile.SoundFile(
                out, "w", audio.samplerate, 1, format=target, subtype="PCM_16"
            ) as result:
                result.buffer_write(samples, dtype="int16")
    except RuntimeError as exc:
        raise ValueError("Unable to read or write the lossless audio carrier.") from exc
    return out.getvalue()


@dataclass(frozen=True)
class ImageStyle:
    theme: str
    name: str = ""
    accent: str = ""
    light: float = 1.0
    texture: float = 0.0
    focus: float = 0.5
    scale: float = 1.0


@dataclass(frozen=True)
class AudioStyle:
    theme: str
    name: str = ""
    tempo: float = 1.0
    pitch: int = 0
    timbre: str = "native"
    room: float = 0.0
    pulse: float = 0.0


IMAGE_COMPOSITIONS = {
    "rain_city": {"name": "Rainy City Lights", "desc": "Street-level perspective, wet asphalt and reflected signs"},
    "ukiyo": {"name": "Ukiyo-e Waves", "desc": "Carved foam curves, ink bands and a paper border"},
    "botanical": {"name": "Botanical Illustration", "desc": "Herbarium study with leaf veins and specimen labeling"},
    "poster": {"name": "Vintage Travel Poster", "desc": "Flat color blocks, simplified terrain and editorial typography"},
    "geometry": {"name": "Geometric Abstract", "desc": "Asymmetric cut-paper shapes and graphic stripes"},
    "architecture": {"name": "Monochrome Architecture", "desc": "Angular facade planes, vanishing lines and window rhythm"},
}

AUDIO_COMPOSITIONS = {
    "forest": {"name": "Forest Breeze", "desc": "Flute calls, bird-like motifs and wind"},
    "ocean_bells": {"name": "Ocean Bells", "desc": "A slow surf bed with sparse resonant bells"},
    "space": {"name": "Space Atmosphere", "desc": "Slow evolving chords and a deep drone"},
    "percussion": {"name": "Minimal Percussion", "desc": "Dry clicks, low kicks and syncopated metallic ticks"},
    "strings": {"name": "Cinematic Strings", "desc": "Layered bowed chords and a rising melody"},
}
TIMBRES = ("native", "piano", "electric", "flute", "bell", "synth", "pluck", "strings", "space")


def default_audio_style(theme: str) -> AudioStyle:
    return AudioStyle(theme)


def _number(recipe, key, low, high):
    value = recipe.get(key)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid style parameter: {key}.")
    return value


def request_variations(api_key, media, theme, prompt, allowed_themes, *, same_theme=False):
    """Send only the selected theme and the explicitly supplied style prompt."""
    if not api_key:
        raise ValueError("Requires DeepSeek. Add an API key in Settings.")
    if media == "image":
        schema = '{"theme":"style code","name":"short title","accent":"#RRGGBB","light":1.0,"texture":0.3,"focus":0.5,"scale":1.0}'
        ranges = "light 0.7-1.35, texture 0-1, focus 0.15-0.85, scale 0.65-1.6."
    elif media == "audio":
        schema = '{"theme":"style code","name":"short title","tempo":1.0,"pitch":0,"timbre":"flute","room":0.3,"pulse":0.2}'
        ranges = f"tempo 0.65-1.55, pitch integer -12 to 12, room/pulse 0-0.75, timbre one of {', '.join(TIMBRES)}."
    else:
        raise ValueError("Select an image or audio theme.")
    if same_theme:
        instruction = f"All three recipes must use theme {theme}. Vary rendering parameters within that theme."
    else:
        medium = "composition, subject and perspective" if media == "image" else "arrangement, rhythm and instrumentation"
        instruction = f"Use three different theme codes with different {medium}. The starting theme is a reference."
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps({
            "model": "deepseek-flash",
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": (
                    'Return one JSON object with a "variations" array containing exactly three distinct '
                    f"{media} recipes. Each recipe follows {schema}. Limits: {ranges} "
                    f"Allowed theme codes: {', '.join(allowed_themes)}. {instruction}"
                )},
                {"role": "user", "content": f"Starting theme: {theme}. Style request: {prompt.strip()[:350] or 'Explore three contrasting styles.'}"},
            ],
        }).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        message = json.loads(response.read())["choices"][0]["message"]["content"]
    try:
        recipes = json.loads(message)["variations"]
        if not isinstance(recipes, list) or len(recipes) != 3:
            raise ValueError("Expected three style variations.")
        styles = []
        for item in recipes:
            code, name = item["theme"], item["name"]
            if code not in allowed_themes or not isinstance(name, str) or not name.strip():
                raise ValueError("Unknown theme or missing style title.")
            name = " ".join(name.split())[:56]
            if media == "image":
                accent = item.get("accent", "")
                if not isinstance(accent, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
                    raise ValueError("Invalid image accent color.")
                styles.append(ImageStyle(code, name, accent.upper(), _number(item, "light", 0.7, 1.35),
                                         _number(item, "texture", 0, 1), _number(item, "focus", 0.15, 0.85),
                                         _number(item, "scale", 0.65, 1.6)))
            else:
                pitch, timbre = item["pitch"], item["timbre"]
                if type(pitch) is not int or not -12 <= pitch <= 12 or timbre not in TIMBRES:
                    raise ValueError("Invalid audio pitch or timbre.")
                styles.append(AudioStyle(code, name, _number(item, "tempo", 0.65, 1.55), pitch, timbre,
                                         _number(item, "room", 0, 0.75), _number(item, "pulse", 0, 0.75)))
        fingerprints = {tuple(value for key, value in vars(style).items() if key != "name") for style in styles}
        if len(fingerprints) != 3:
            raise ValueError("The service returned duplicate variations.")
        themes = {style.theme for style in styles}
        if (same_theme and themes != {theme}) or (not same_theme and len(themes) != 3):
            mode = "the selected theme" if same_theme else "three different themes"
            raise ValueError(f"DeepSeek did not use {mode}.")
        return styles
    except (TypeError, KeyError, ValueError) as exc:
        mode = "three usable styles within the selected theme" if same_theme else "three distinct themes"
        raise ValueError(f"DeepSeek did not return {mode}. Try again or select a local theme.") from exc
