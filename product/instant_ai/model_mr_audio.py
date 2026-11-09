"""On-demand, private audio derivatives of the owner's existing local videos.

Finite repeats put the stop point in the media itself: screen-off playback does
not depend on a JavaScript timeout firing. No speech recognition or download.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


AUDIO_MINUTES = {0, 15, 30, 60, 90}
AUDIO_CACHE_BYTES = 512 * 1024 * 1024
AUDIO_FILE_BYTES = 128 * 1024 * 1024
AUDIO_CACHE_AGE = 30 * 24 * 3600
_CACHE_FILE = re.compile(r"[a-f0-9]{64}(?:-(?:15|30|60|90))?\.m4a")
_PREPARE_LOCK = threading.Lock()


class ModelMrAudioUnavailable(RuntimeError):
    pass


def _run(command: list[str], timeout: int = 45) -> bytes:
    try:
        return subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True, timeout=timeout,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        # ffmpeg stderr contains private paths; never return it to the caller.
        raise ModelMrAudioUnavailable("音频准备失败，请稍后重试。") from error


def _probe(source: Path) -> dict:
    try:
        data = json.loads(_run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "mov,matroska,webm", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,duration,bit_rate:format=duration",
            "-of", "json", str(source),
        ], 10))
        stream = data.get("streams", [])[0]
        duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
        bitrate = int(stream.get("bit_rate") or 0)
        if not math.isfinite(duration) or not 0 < duration <= 6 * 3600:
            raise ValueError("unsupported duration")
        return {"codec": stream.get("codec_name"), "duration": duration, "bitrate": bitrate}
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ModelMrAudioUnavailable("这条作品暂没有可播放的音轨。") from error


def _prune(cache: Path, protected: set[Path], reserve: int = 0) -> None:
    now = time.time()
    entries = []
    for entry in cache.iterdir():
        if not _CACHE_FILE.fullmatch(entry.name) or entry.is_symlink() or not entry.is_file():
            continue
        info = entry.stat()
        if entry not in protected and info.st_mtime < now - AUDIO_CACHE_AGE:
            entry.unlink()
        else:
            entries.append((info.st_mtime, info.st_size, entry))
    size = sum(item[1] for item in entries)
    for _, length, entry in sorted(entries):
        if size + reserve <= AUDIO_CACHE_BYTES:
            break
        if entry not in protected:
            entry.unlink()
            size -= length
    if size + reserve > AUDIO_CACHE_BYTES:
        raise ModelMrAudioUnavailable("音频缓存空间不足，请稍后重试。")


def _encode(source: Path, target: Path, *, copy: bool, seconds: int = 0) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".audio-", suffix=".m4a", dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm"]
        if seconds:
            command += ["-stream_loop", "-1"]
        command += ["-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
                    "-map_metadata", "-1", "-map_chapters", "-1"]
        if seconds:
            command += ["-t", str(seconds)]
        command += ["-c:a", "copy"] if copy else ["-c:a", "aac", "-b:a", "96k", "-ac", "2"]
        command += ["-fs", str(AUDIO_FILE_BYTES), "-movflags", "+faststart", str(temporary)]
        _run(command)
        result = _probe(temporary)
        expected = seconds or _probe(source)["duration"]
        if temporary.stat().st_size >= AUDIO_FILE_BYTES or abs(result["duration"] - expected) > 0.25:
            raise ModelMrAudioUnavailable("音频时长校验失败，请稍后重试。")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_model_mr_audio(source: Path, library_root: Path, minutes: int = 0) -> Path:
    """Return an owner-private M4A; minutes=0 is the original, otherwise finite repeats.

    The caller must authenticate and resolve the work's approved local video
    before calling. Cache identity changes with the source, including HD repairs.
    """
    if type(minutes) is not int or minutes not in AUDIO_MINUTES:
        raise ValueError("定时仅支持15、30、60、90分钟。")
    if not _PREPARE_LOCK.acquire(timeout=55):
        raise ModelMrAudioUnavailable("正在准备另一段音频，请稍后重试。")
    try:
        source = source.resolve(strict=True)
        info = source.stat()
        if not source.is_file() or not 0 < info.st_size <= 2 * 1024**3:
            raise ModelMrAudioUnavailable("本地视频暂不可用。")
        root = library_root.resolve(strict=True)
        cache = root / "audio-cache"
        if cache.is_symlink():
            raise ModelMrAudioUnavailable("音频缓存目录不可用。")
        cache.mkdir(mode=0o700, exist_ok=True)
        identity = f"v1:{source}:{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        original = cache / f"{key}.m4a"
        target = cache / (f"{key}-{minutes}.m4a" if minutes else f"{key}.m4a")
        for entry in {original, target}:
            if entry.is_symlink():
                raise ModelMrAudioUnavailable("音频缓存文件不可用。")
        if target.is_file() and target.stat().st_size > 0:
            target.touch()
            _prune(cache, {original, target})
            return target
        _prune(cache, {original, target}, reserve=2 * AUDIO_FILE_BYTES)
        if shutil.disk_usage(cache).free < 2 * AUDIO_FILE_BYTES:
            raise ModelMrAudioUnavailable("磁盘可用空间不足，暂不能准备音频。")
        if not original.is_file() or not original.stat().st_size:
            audio = _probe(source)
            _encode(source, original, copy=audio["codec"] == "aac" and audio["bitrate"] <= 192_000)
        if minutes:
            _encode(original, target, copy=True, seconds=minutes * 60)
        target.touch()
        _prune(cache, {original, target})
        return target
    except OSError as error:
        raise ModelMrAudioUnavailable("本地音频暂不可用，请稍后重试。") from error
    finally:
        _PREPARE_LOCK.release()
