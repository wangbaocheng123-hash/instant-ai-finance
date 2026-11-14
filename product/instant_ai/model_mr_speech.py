"""Fixed Chinese recordings for dates and numbers; no video text leaves the app.

The pinned offline eSpeak NG fallback remains for explicit unknown-date text.
Recordings and the fallback runtime stay in owner-private data, outside Git.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen


ENGINE_VERSION = "0.2.4"
ANNOUNCEMENT_VERSION = "recorded-cnlink-bd238bcc-v1"
ENGINE_SHA256 = "08721baf27d13d461f6be6eed9a65277e70d68234ff484fd8b9897b222cdcb6d"
ENGINE_URL = "https://files.pythonhosted.org/packages/de/1e/25ec5ab07528c0fbb215a61800a38eca05c8a99445515a02d7fa5debcb32/espeakng_loader-0.2.4-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
_ENGINE_LOCK = threading.Lock()
_BEIJING = timezone(timedelta(hours=8))


class ModelMrSpeechUnavailable(RuntimeError):
    pass


def chinese_number(value: int) -> str:
    if type(value) is not int or not 0 <= value <= 500_000:
        raise ValueError("作品序号无效。")
    digits = "零一二三四五六七八九"
    if value == 0:
        return digits[0]
    if value >= 10_000:
        major, minor = divmod(value, 10_000)
        return chinese_number(major) + "万" + (("零" if minor < 1000 else "") + chinese_number(minor) if minor else "")
    output = ""
    zero = False
    for scale, unit in [(1000, "千"), (100, "百"), (10, "十"), (1, "")]:
        digit, value = divmod(value, scale)
        if digit:
            if zero:
                output += "零"
            output += ("" if digit == 1 and scale == 10 and not output else digits[digit]) + unit
            zero = False
        elif output and value:
            zero = True
    return output


def announcement_text(published_at: str, sequence_number: int) -> str:
    try:
        stamp = datetime.fromisoformat(str(published_at).strip().replace("Z", "+00:00"))
        stamp = stamp.replace(tzinfo=_BEIJING) if stamp.tzinfo is None else stamp.astimezone(_BEIJING)
        year = "".join("零一二三四五六七八九"[int(digit)] for digit in str(stamp.year))
        date = f"{year}年{chinese_number(stamp.month)}月{chinese_number(stamp.day)}日"
    except (ValueError, TypeError, OverflowError):
        date = "发布日期未知"
    return f"{date}，第{chinese_number(sequence_number)}条。"


def ensure_speech_engine(library_root: Path) -> Path:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ModelMrSpeechUnavailable("当前服务器尚未配置中文报读组件。")
    with _ENGINE_LOCK:
        root = library_root.resolve(strict=True)
        tools = root / "audio-tools"
        if tools.is_symlink():
            raise ModelMrSpeechUnavailable("中文报读组件目录不可用。")
        tools.mkdir(mode=0o700, exist_ok=True)
        installed = tools / f"espeakng-{ENGINE_VERSION}-{ENGINE_SHA256[:12]}"
        if installed.is_symlink():
            raise ModelMrSpeechUnavailable("中文报读组件目录不可用。")
        engine = installed / "espeakng_loader"
        marker = installed / "verified.json"
        if marker.is_file() and not marker.is_symlink() and (engine / "libespeak-ng.so").is_file():
            if (engine / "libespeak-ng.so").is_symlink() or (engine / "espeak-ng-data").is_symlink():
                raise ModelMrSpeechUnavailable("中文报读组件文件不可用。")
            try:
                if json.loads(marker.read_text())["sha256"] == ENGINE_SHA256:
                    return engine
            except (ValueError, KeyError):
                raise ModelMrSpeechUnavailable("中文报读组件清单不可用。") from None
        temporary = Path(tempfile.mkdtemp(prefix=".speech-", dir=tools))
        try:
            wheel = temporary / "engine.whl"
            digest = hashlib.sha256()
            length = 0
            started = time.monotonic()
            with urlopen(ENGINE_URL, timeout=30) as response, wheel.open("wb") as output:
                while chunk := response.read(256 * 1024):
                    length += len(chunk)
                    if length > 12 * 1024**2 or time.monotonic() - started > 60:
                        raise ModelMrSpeechUnavailable("中文报读组件下载超过限制。")
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != ENGINE_SHA256:
                raise ModelMrSpeechUnavailable("中文报读组件校验失败。")
            extracted = temporary / "runtime"
            extracted.mkdir(mode=0o700)
            with zipfile.ZipFile(wheel) as archive:
                if sum(item.file_size for item in archive.infolist()) > 32 * 1024**2:
                    raise ModelMrSpeechUnavailable("中文报读组件解包超过限制。")
                for item in archive.infolist():
                    relative = Path(item.filename)
                    if relative.is_absolute() or ".." in relative.parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ModelMrSpeechUnavailable("中文报读组件格式无效。")
                    target = extracted / relative
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        target.write_bytes(archive.read(item))
                        target.chmod(0o600)
            (extracted / "verified.json").write_text(json.dumps({"sha256": ENGINE_SHA256}))
            (extracted / "COPYING").write_text((Path(__file__).parent / "licenses" / "espeak-ng-GPL-3.0.txt").read_text())
            (extracted / "SOURCE.txt").write_text(
                "eSpeak NG, GPL-3.0\n"
                "https://github.com/espeak-ng/espeak-ng/tree/4870adfa25b1a32b4361592f1be8a40337c58d6c\n"
                "Runtime carrier espeakng-loader 0.2.4 (MIT wrapper; its Python module is not executed).\n"
                "https://github.com/thewh1teagle/espeakng-loader/tree/146599e29be31bf17d99f0bcb7dbb2f92aef3d95\n"
            )
            if installed.exists():
                raise ModelMrSpeechUnavailable("中文报读组件不完整，请先检查组件目录。")
            os.replace(extracted, installed)
            return engine
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            raise ModelMrSpeechUnavailable("中文报读组件准备失败，请稍后重试。") from error
        finally:
            shutil.rmtree(temporary)


def synthesize_announcement(text: str, output: Path, library_root: Path) -> None:
    from .model_mr_prompts import render_recorded_announcement
    try:
        if render_recorded_announcement(text, output, library_root):
            return
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, wave.Error) as error:
        raise ModelMrSpeechUnavailable("固定日期报读准备失败，请稍后重试。") from error
    engine = ensure_speech_engine(library_root)
    try:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), str(engine), text, str(output)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       timeout=15, check=True)
        with wave.open(str(output), "rb") as speech:
            if not 0 < speech.getnframes() / speech.getframerate() <= 20:
                raise ModelMrSpeechUnavailable("中文播报时长校验失败。")
    except (OSError, subprocess.SubprocessError, wave.Error) as error:
        raise ModelMrSpeechUnavailable("中文日期和序号报读生成失败。") from error


def _speech_worker(engine: Path, text: str, output: Path) -> None:
    # eSpeak has process-global state. A short-lived worker isolates concurrent
    # HTTP requests and contains native-library errors without an audio device.
    if len(text) > 100:
        raise ValueError("announcement too long")
    speaker = ctypes.CDLL(str(engine / "libespeak-ng.so"))
    speaker.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    rate = speaker.espeak_Initialize(2, 0, str(engine / "espeak-ng-data").encode(), 0)
    if rate <= 0 or speaker.espeak_SetVoiceByName(b"cmn") != 0:
        raise RuntimeError("Chinese voice unavailable")
    speaker.espeak_SetParameter(1, 145, 0)
    frames = []
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)

    @callback_type
    def receive(samples, count, events):
        if samples and count > 0:
            frames.append(ctypes.string_at(samples, count * 2))
        return 1 if sum(map(len, frames)) > 2 * 1024**2 else 0

    speaker.espeak_SetSynthCallback(receive)
    speaker.espeak_Synth.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int,
                                   ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    encoded = text.encode("utf-8")
    if speaker.espeak_Synth(encoded, len(encoded) + 1, 0, 1, 0, 1, None, None) != 0:
        raise RuntimeError("speech synthesis failed")
    speaker.espeak_Synchronize()
    speaker.espeak_Terminate()
    with wave.open(str(output), "wb") as speech:
        speech.setnchannels(1)
        speech.setsampwidth(2)
        speech.setframerate(rate)
        speech.writeframes(b"".join(frames))


if __name__ == "__main__":
    _speech_worker(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
