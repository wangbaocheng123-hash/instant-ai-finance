"""Owner-private HLS event playlists with offline spoken dates and work numbers.

Native iPhone playback advances through media segments, without an `ended`
script having to wake the locked page and start a new audio element.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model_mr import ModelMrClient
from .model_mr_audio import AUDIO_MINUTES, ModelMrAudioUnavailable, _probe, _run, prepare_model_mr_audio
from .model_mr_speech import ANNOUNCEMENT_VERSION, announcement_text, synthesize_announcement


QUEUE_CACHE_BYTES = 512 * 1024**2
QUEUE_ARTIFACT_BYTES = 128 * 1024**2
QUEUE_IDLE_SECONDS = 6 * 3600
_ARTIFACT_LOCK = threading.Lock()
_ARTIFACT_NAME = re.compile(r"[0-9a-f]{64}")
_SEGMENT_NAME = re.compile(r"(?:intro|body)-[0-9]{5}\.ts|(?:intro|body)\.m3u8|manifest\.json")


def _queue_cache(root: Path) -> Path:
    base = root.resolve(strict=True) / "audio-cache"
    if base.is_symlink():
        raise ModelMrAudioUnavailable("音频缓存目录不可用。")
    base.mkdir(mode=0o700, exist_ok=True)
    cache = base / "sequences"
    if cache.is_symlink():
        raise ModelMrAudioUnavailable("顺序音频缓存目录不可用。")
    cache.mkdir(mode=0o700, exist_ok=True)
    return cache


def _prune(cache: Path, protected: Path, reserve: int = 0) -> None:
    entries = []
    for directory in cache.iterdir():
        if not _ARTIFACT_NAME.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        files = list(directory.iterdir())
        if any(item.is_symlink() or not item.is_file() or not _SEGMENT_NAME.fullmatch(item.name) for item in files):
            continue
        length = sum(item.stat().st_size for item in files)
        entries.append((directory.stat().st_mtime, length, directory, files))
    total = sum(item[1] for item in entries)
    for updated, length, directory, files in sorted(entries):
        if directory == protected or updated > time.time() - 60:
            continue
        if total + reserve <= QUEUE_CACHE_BYTES and updated > time.time() - 30 * 86400:
            continue
        for item in files:
            item.unlink()
        directory.rmdir()
        total -= length
    if total + reserve > QUEUE_CACHE_BYTES:
        raise ModelMrAudioUnavailable("顺序音频缓存暂满，请稍后重试。")


def _segments(source: Path, directory: Path, prefix: str, codec: str, *, offset: float = 0, limit: float = 0) -> list[dict[str, Any]]:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,wav"]
    if offset:
        command += ["-ss", str(offset)]
    command += ["-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1"]
    if limit:
        command += ["-t", str(limit)]
    if codec == "mp3":
        command += ["-c:a", "libmp3lame", "-b:a", "96k" if prefix == "intro" else "64k", "-ar", "48000", "-ac", "1"]
    elif prefix == "intro":
        command += ["-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "1"]
    else:
        command += ["-c:a", "copy"]
    command += ["-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod", "-hls_segment_filename",
                str(directory / f"{prefix}-%05d.ts"), str(directory / f"{prefix}.m3u8")]
    _run(command)
    parsed = []
    duration = None
    for line in (directory / f"{prefix}.m3u8").read_text().splitlines():
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].rstrip(","))
        elif line and not line.startswith("#"):
            if not re.fullmatch(rf"{prefix}-[0-9]{{5}}\.ts", line) or duration is None or not 0 < duration <= 8:
                raise ModelMrAudioUnavailable("顺序音频分段校验失败。")
            target = directory / line
            if target.is_symlink() or not target.is_file() or target.stat().st_size == 0:
                raise ModelMrAudioUnavailable("顺序音频分段不完整。")
            parsed.append({"filename": line, "duration": duration, "discontinuity": not parsed})
            duration = None
    if not parsed:
        raise ModelMrAudioUnavailable("顺序音频尚未准备完整。")
    return parsed


def prepare_queue_work(work: dict[str, Any], library_root: Path, codec: str, offset: float, budget: float, announce: bool = True) -> tuple[Path, dict[str, Any]]:
    with _ARTIFACT_LOCK:
        original = prepare_model_mr_audio(work["source"], library_root)
        audio = _probe(original)
        offset = offset % audio["duration"]
        text = announcement_text(work["published_at"], work["sequence_number"])
        info = original.stat()
        identity = f"v2:{ANNOUNCEMENT_VERSION}:{original}:{info.st_size}:{text}:{codec}:{offset:.3f}:{budget:.3f}:{announce}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        cache = _queue_cache(library_root)
        target = cache / key
        if target.is_symlink():
            raise ModelMrAudioUnavailable("顺序音频缓存文件不可用。")
        if (target / "manifest.json").is_symlink():
            raise ModelMrAudioUnavailable("顺序音频缓存清单不可用。")
        if (target / "manifest.json").is_file():
            try:
                manifest = json.loads((target / "manifest.json").read_text())
                valid = bool(manifest["segments"]) and all(
                    re.fullmatch(r"(?:intro|body)-[0-9]{5}\.ts", item["filename"])
                    and 0 < item["duration"] <= 8 and type(item["discontinuity"]) is bool
                    and not (target / item["filename"]).is_symlink()
                    and (target / item["filename"]).is_file()
                    for item in manifest["segments"])
            except (ValueError, TypeError, KeyError):
                valid = False
            if valid:
                target.touch()
                return target, manifest
        _prune(cache, target, QUEUE_ARTIFACT_BYTES)
        if shutil.disk_usage(cache).free < 2 * QUEUE_ARTIFACT_BYTES:
            raise ModelMrAudioUnavailable("磁盘空间不足，暂不能准备顺序音频。")
        temporary = Path(tempfile.mkdtemp(prefix=".sequence-", dir=cache))
        try:
            intro = []
            if announce:
                speech = temporary / "announcement.wav"
                synthesize_announcement(text, speech, library_root)
                intro = _segments(speech, temporary, "intro", codec, limit=budget)
                speech.unlink()
            intro_seconds = sum(item["duration"] for item in intro)
            remaining = max(0.0, budget - intro_seconds) if budget else 0.0
            body = _segments(original, temporary, "body", codec, offset=offset, limit=remaining) if not budget or remaining > 0.05 else []
            segments = intro + body
            if sum(item.stat().st_size for item in temporary.iterdir()) > QUEUE_ARTIFACT_BYTES:
                raise ModelMrAudioUnavailable("这一条顺序音频超过缓存大小限制。")
            manifest = {"segments": segments, "intro_seconds": intro_seconds, "body_offset": offset,
                        "duration": sum(item["duration"] for item in segments)}
            (temporary / "manifest.json").write_text(json.dumps(manifest))
            if target.exists():
                raise ModelMrAudioUnavailable("顺序音频缓存不完整，请稍后重试。")
            os.replace(temporary, target)
            _prune(cache, target)
            return target, manifest
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


@dataclass
class _Queue:
    root: Path
    works: list[dict[str, Any]]
    start_id: int
    minutes: int
    offset: float
    codec: str
    continuous: bool = True
    first_intro: bool = True
    seconds: float = 0
    touched: float = field(default_factory=time.monotonic)
    cursor: int = 0
    requested: int = -1
    done: bool = False
    elapsed: float = 0
    segments: list[dict[str, Any]] = field(default_factory=list)
    tracks: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    lock: Any = field(default_factory=threading.Lock)


class ModelMrAudioQueues:
    def __init__(self):
        self._sessions: dict[str, _Queue] = {}
        self._lock = threading.Lock()

    def _get(self, token: str) -> _Queue:
        with self._lock:
            session = self._sessions.get(token)
            if session is None or time.monotonic() - session.touched > QUEUE_IDLE_SECONDS:
                raise ModelMrAudioUnavailable("本次顺序播放已失效，请重新点击播放音频。")
            session.touched = time.monotonic()
            return session

    def playlist(self, client: ModelMrClient, token: str, start_id: int, minutes: int, offset: float, codec: str,
                 continuous: bool = True, first_intro: bool = True, seconds: float = 0) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", token) or type(minutes) is not int or minutes not in AUDIO_MINUTES or codec not in {"aac", "mp3"} or not math.isfinite(offset) or not 0 <= offset <= 21600:
            raise ValueError("顺序播放参数无效。")
        if type(continuous) is not bool or type(first_intro) is not bool or not math.isfinite(seconds) or seconds < 0 or seconds > minutes * 60:
            raise ValueError("定时剩余时长无效。")
        budget = seconds or minutes * 60
        with self._lock:
            now = time.monotonic()
            for old in [key for key, item in self._sessions.items() if now - item.touched > QUEUE_IDLE_SECONDS]:
                del self._sessions[old]
            session = self._sessions.get(token)
            if session is None:
                if len(self._sessions) >= 8:
                    oldest = min(self._sessions, key=lambda key: self._sessions[key].touched)
                    del self._sessions[oldest]
                works = client.audio_queue(start_id)
                session = _Queue(client.snapshot_path.parent, works if continuous else works[:1], start_id, minutes, offset, codec,
                                 continuous=continuous, first_intro=first_intro, seconds=budget)
                self._sessions[token] = session
            elif (session.start_id, session.minutes, session.offset, session.codec, session.continuous, session.first_intro, session.seconds) != (start_id, minutes, offset, codec, continuous, first_intro, budget):
                raise ValueError("本次顺序播放设置已改变，请重新开始。")
            session.touched = now
        with session.lock:
            # HLS clients reload EVENT manifests themselves while the page sleeps.
            # Extend only near the requested tail; do not encode the entire library.
            if not session.done and (not session.segments or session.requested >= 0 and session.requested >= len(session.segments) - 6):
                self._extend(session)
            lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-PLAYLIST-TYPE:EVENT", "#EXT-X-TARGETDURATION:8",
                     "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-START:TIME-OFFSET=0,PRECISE=YES"]
            for index, item in enumerate(session.segments):
                if item["discontinuity"]:
                    lines.append("#EXT-X-DISCONTINUITY")
                lines += [f"#EXTINF:{item['duration']:.6f},", f"/media/model-mr/audio-queues/{token}/segments/{index}.ts"]
            if session.done:
                lines.append("#EXT-X-ENDLIST")
            return "\n".join(lines) + "\n"

    def _extend(self, session: _Queue) -> None:
        while session.cursor < len(session.works) or not session.continuous and session.minutes:
            work = session.works[session.cursor if session.continuous else 0]
            offset = session.offset if session.cursor == 0 else 0
            remaining = max(0.0, session.seconds - session.elapsed) if session.minutes else 0.0
            if session.minutes and remaining <= 0.1:
                session.done = True
                return
            # Validate local source audio before generating speech. A silent or
            # damaged work is skipped visibly; a voice/runtime failure is fatal.
            try:
                _probe(work["source"])
            except ModelMrAudioUnavailable:
                if session.cursor == 0 or not session.continuous:
                    raise
                session.cursor += 1
                session.skipped += 1
                continue
            announce = (session.cursor > 0 and session.continuous) or (session.cursor == 0 and session.first_intro)
            directory, manifest = prepare_queue_work(work, session.root, session.codec, offset, remaining, announce)
            start = session.elapsed
            recipe = {"work": work, "offset": offset, "budget": remaining, "announce": announce}
            for item in manifest["segments"]:
                session.segments.append({**item, "directory": directory, "recipe": recipe})
            session.elapsed += manifest["duration"]
            session.tracks.append({key: work[key] for key in ["id", "title", "published_at", "sequence_number"]} | {
                "start": start, "end": session.elapsed, "intro_seconds": manifest["intro_seconds"],
                "body_offset": manifest["body_offset"],
            })
            session.cursor += 1
            session.done = bool((session.continuous or not session.minutes) and session.cursor >= len(session.works)) or bool(session.minutes and session.elapsed >= session.seconds - 0.1)
            return
        session.done = True
        if not session.segments:
            raise ModelMrAudioUnavailable("所选及后续作品暂没有可播放的音轨。")

    def segment(self, token: str, index: int) -> Path:
        session = self._get(token)
        with session.lock:
            if not 0 <= index < len(session.segments):
                raise ValueError("音频分段不存在。")
            item = session.segments[index]
            session.requested = max(session.requested, index)
            target = item["directory"] / item["filename"]
            if item["directory"].is_symlink() or target.is_symlink():
                raise ModelMrAudioUnavailable("音频分段不可用。")
            if not target.is_file():
                recipe = item["recipe"]
                directory, _ = prepare_queue_work(recipe["work"], session.root, session.codec, recipe["offset"], recipe["budget"], recipe["announce"])
                target = directory / item["filename"]
            item["directory"].touch()
            return target

    def state(self, token: str, position: float) -> dict[str, Any]:
        session = self._get(token)
        with session.lock:
            current = next((track for track in session.tracks if track["start"] <= position < track["end"]),
                           session.tracks[-1] if session.tracks else None)
            return {"current": current, "done": session.done, "skipped": session.skipped,
                    "prepared_seconds": session.elapsed, "remaining_works": max(0, len(session.works) - session.cursor)}


MODEL_MR_AUDIO_QUEUES = ModelMrAudioQueues()
