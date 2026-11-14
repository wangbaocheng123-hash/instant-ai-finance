"""A small, pinned recording vocabulary for dates and work numbers.

The original recordings and derived PCM stay in owner-private runtime data.
No words, dates, video content, or credentials are sent to a speech service.
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen


VERSION = "recorded-cnlink-bd238bcc-v1"
RATE = 16000
_LOCK = threading.Lock()
_DIGITS = "零一二三四五六七八九"
_MANIFEST = Path(__file__).with_name("announcement_prompts.json")


def _integer(text: str) -> int:
    if "万" in text:
        major, minor = text.split("万", 1)
        return _integer(major) * 10000 + (_integer(minor) if minor else 0)
    if all(char in _DIGITS for char in text):
        return int("".join(str(_DIGITS.index(char)) for char in text))
    total = digit = 0
    for char in text:
        if char in _DIGITS:
            digit = _DIGITS.index(char)
        else:
            total += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
    return total + digit


def _number(value: int, ordinal: bool = False) -> list[str]:
    if not 0 <= value <= 500000:
        raise ValueError("number out of range")
    if value >= 10000:
        # Long identifiers remain unambiguous without an unavailable 万 clip.
        digits = list(str(value))
        return [f"digits/8000/{'h-' if ordinal else ''}{digits[0]}.wav"] + [f"digits/8000/{n}.wav" for n in digits[1:]]
    parts = []
    if value >= 1000:
        parts += [str(value // 1000), "thousand"]
        value %= 1000
        if 0 < value < 100:
            parts.append("0")
    if value >= 100:
        parts += [str(value // 100), "hundred"]
        value %= 100
        if 0 < value < 10:
            parts.append("0")
    if value or not parts:
        if value <= 19:
            parts.append(str(value))
        else:
            parts.append(str(value // 10 * 10))
            if value % 10:
                parts.append(str(value % 10))
    if ordinal:
        first = int(parts.pop(0))
        if first >= 20:
            parts = [f"h-{first // 10}", "10", *parts]
        else:
            parts.insert(0, f"h-{first}")
    return [f"digits/8000/{name}.wav" for name in parts]


def announcement_parts(text: str) -> list[str | float] | None:
    match = re.fullmatch(r"([零一二三四五六七八九]{4})年([零一二三四五六七八九十]+)月([零一二三四五六七八九十]+)日，第([零一二三四五六七八九十百千万]+)条。", text)
    if not match:
        return None  # Unknown dates retain the explicit legacy announcement.
    year, month, day, sequence = match.groups()
    number = _integer(sequence)
    if not 1 <= number <= 500000 or not 1 <= _integer(month) <= 12 or not 1 <= _integer(day) <= 31:
        return None
    return [0.12, *[f"digits/8000/{_DIGITS.index(char)}.wav" for char in year],
            "time/8000/year.wav", 0.12, *_number(_integer(month)), "time/8000/month.wav", 0.1,
            *_number(_integer(day)), "time/8000/day.wav", 0.28,
            *_number(number, True), "directory/8000/dir-result_match.wav", 0.16]


def _pcm(raw: bytes, suffix: bool) -> bytes:
    # Sources include A-law WAVs; decode once without installing any component.
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-f", "wav", "-i", "pipe:0",
         "-vn", "-ac", "1", "-ar", str(RATE), "-af", "highpass=f=65,lowpass=f=3400",
         "-f", "s16le", "pipe:1"], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10,
    ).stdout
    samples = array.array("h", decoded)
    if sys.byteorder != "little":
        samples.byteswap()
    if suffix:
        # The source phrase starts with 条 and has a measured silent boundary
        # at 0.34–0.47s before 相. Keep that entire first syllable, not its tail.
        samples = samples[:round(.42 * RATE)]
    if not samples or len(samples) > 4 * RATE:
        raise ValueError("prompt duration invalid")
    peak = max(map(abs, samples))
    if peak < 100:
        raise ValueError("silent prompt")
    threshold = max(45, peak * .006)
    voiced = [index for index, value in enumerate(samples) if abs(value) > threshold]
    start, end = max(0, voiced[0] - 400), min(len(samples), voiced[-1] + 400)
    samples = samples[start:end]
    gain = min(4, 20600 / peak)
    fade = round(.005 * RATE)
    for index, value in enumerate(samples):
        envelope = min(1, index / fade, (len(samples) - 1 - index) / fade)
        samples[index] = round(value * gain * max(0, envelope))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def ensure_recorded_prompts(library_root: Path) -> Path:
    if not _LOCK.acquire(timeout=60):
        raise RuntimeError("recorded prompts busy")
    try:
        root = library_root.resolve(strict=True) / "audio-tools"
        if root.is_symlink():
            raise ValueError("invalid prompt directory")
        root.mkdir(mode=0o700, exist_ok=True)
        target = root / VERSION
        if target.is_symlink():
            raise ValueError("invalid prompt directory")
        manifest_bytes = _MANIFEST.read_bytes()
        manifest = json.loads(manifest_bytes)
        fingerprint = hashlib.sha256(manifest_bytes).hexdigest()
        marker = target / "verified.json"
        if marker.is_file() and not marker.is_symlink():
            if json.loads(marker.read_text()).get("manifest") == fingerprint and all(
                (target / name).is_file() and not (target / name).is_symlink() for name in manifest["files"]):
                return target
            raise ValueError("invalid prompt cache")
        if target.exists():
            raise ValueError("incomplete prompt cache")
        temporary = Path(tempfile.mkdtemp(prefix=".recorded-", dir=root))
        deadline = time.monotonic() + 45
        try:
            def prepare(item):
                name, digest = item
                if not re.fullmatch(r"(?:digits|time|directory)/8000/[a-z0-9_-]+\.wav", name):
                    raise ValueError("invalid prompt name")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("prompt preparation timed out")
                with urlopen(manifest["base_url"] + name, timeout=min(20, remaining)) as response:
                    raw = response.read(128 * 1024 + 1)
                if len(raw) > 128 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError("prompt hash mismatch")
                data = _pcm(raw, name.startswith("directory/"))
                path = temporary / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o600)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(prepare, manifest["files"].items()))
            for source, name in [("chinese-prompts-MPL-2.0.txt", "LICENSE"), ("chinese-prompts-README.md", "README.md")]:
                (temporary / name).write_bytes((Path(__file__).parent / "licenses" / source).read_bytes())
            (temporary / "SOURCE.txt").write_text(manifest["repository"] + "/tree/" + manifest["commit"] + "\n"
                "Derivatives: mono 16kHz PCM, fixed gain, short edge fades and silence trimming; 条 excerpt 0–0.42s.\n")
            (temporary / "verified.json").write_text(json.dumps({"manifest": fingerprint}))
            os.replace(temporary, target)
            return target
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    finally:
        _LOCK.release()


def render_recorded_announcement(text: str, output: Path, library_root: Path) -> bool:
    parts = announcement_parts(text)
    if parts is None:
        return False
    root = ensure_recorded_prompts(library_root)
    result = bytearray()
    for part in parts:
        if isinstance(part, float):
            result.extend(b"\0\0" * round(part * RATE))
        else:
            path = root / part
            if path.is_symlink() or not path.is_file():
                raise ValueError("prompt unavailable")
            data = path.read_bytes()
            if not data or len(data) % 2 or len(data) > 4 * RATE * 2:
                raise ValueError("prompt PCM invalid")
            result.extend(data)
            result.extend(b"\0\0" * round(.035 * RATE))
    if not 0 < len(result) <= RATE * 2 * 20:
        raise ValueError("announcement length invalid")
    with wave.open(str(output), "wb") as wavefile:
        wavefile.setnchannels(1)
        wavefile.setsampwidth(2)
        wavefile.setframerate(RATE)
        wavefile.writeframes(result)
    return True
