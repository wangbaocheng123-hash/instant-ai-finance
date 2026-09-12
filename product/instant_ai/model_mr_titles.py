"""Model Mr title guards and ephemeral early-frame extraction.

Cover frames are used only as title evidence.  They are never converted into,
or merged with, the canonical video original.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path


COVER_FRAME_TIMESTAMPS = (0.0, 0.35, 0.8, 1.2)
MAX_COVER_FRAMES = len(COVER_FRAME_TIMESTAMPS)
MAX_COVER_FRAME_BYTES = 1_500_000
MAX_GENERATED_TITLE_LENGTH = 60
TITLE_SOURCES = {
    "source",
    "source_placeholder",
    "cover_ocr",
    "ai_video_original",
    "manual",
}

_PLACEHOLDER_PATTERNS = (
    re.compile(r"^抖音(?:作品|视频)(?:[_\s#：:\-]*[A-Za-z0-9._:-]+)?$", re.IGNORECASE),
    re.compile(r"^(?:模型先生|模型哥看世界)在抖音记录美好生活(?:\d{8})?$"),
    re.compile(r"^(?:模型先生|模型哥看世界)的抖音(?:[-—–|｜·].*)?$"),
    re.compile(r"^(?:未命名作品|暂无标题|标题待识别)$"),
)


class CoverFrameUnavailable(RuntimeError):
    pass


def clean_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split()).strip(" #`'\"“”‘’")


def is_placeholder_title(value: object) -> bool:
    title = clean_title(value)
    if not title:
        return True
    return any(pattern.fullmatch(title) for pattern in _PLACEHOLDER_PATTERNS)


def is_meaningful_title(value: object) -> bool:
    title = clean_title(value)
    return bool(
        title
        and len(title) <= 120
        and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", title)
        and not is_placeholder_title(title)
    )


def clean_generated_title(value: object) -> str:
    title = clean_title(value)
    title = re.sub(r"^(?:标题|作品标题|建议标题)[：:]\s*", "", title)
    title = title.replace("?", "？").replace("!", "！")
    title = title.strip(" #`'\"“”‘’。，,；;")
    if (
        len(title) < 2
        or len(title) > MAX_GENERATED_TITLE_LENGTH
        or not re.search(r"[A-Za-z\u4e00-\u9fff]", title)
        or is_placeholder_title(title)
    ):
        return ""
    return title


def normalize_title_source(value: object, *, title: object = "") -> str:
    source = str(value or "").strip()
    if source in TITLE_SOURCES:
        return source
    return "source_placeholder" if is_placeholder_title(title) else "source"


def title_needs_generation(title: object, source: object = "") -> bool:
    normalized_source = normalize_title_source(source, title=title)
    if normalized_source in {"manual", "source", "cover_ocr", "ai_video_original"}:
        return False
    return is_placeholder_title(title)


def extract_cover_frame_data_urls(source: Path, work_id: int) -> list[str]:
    """Extract bounded early frames for one multimodal request, then delete them."""

    path = Path(source).resolve(strict=True)
    if not path.is_file():
        raise CoverFrameUnavailable("这条作品没有可识别的本地视频。")
    configured = os.environ.get("INSTANT_AI_FFMPEG", "").strip()
    ffmpeg = (shutil.which(configured) if configured else None) or configured or shutil.which("ffmpeg") or ""
    if not ffmpeg or not Path(ffmpeg).is_file():
        raise CoverFrameUnavailable("服务器没有可用的 ffmpeg，无法读取视频开头画面。")

    try:
        safe_work_id = max(0, int(work_id))
    except (TypeError, ValueError):
        safe_work_id = 0
    images: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"instant-ai-model-title-{safe_work_id}-") as temporary:
        root = Path(temporary)
        for index, timestamp in enumerate(COVER_FRAME_TIMESTAMPS):
            output = root / f"frame-{index}.jpg"
            try:
                result = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        str(timestamp),
                        "-i",
                        str(path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=576:-2:flags=lanczos",
                        "-q:v",
                        "5",
                        str(output),
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode or not output.is_file():
                continue
            size = output.stat().st_size
            if size <= 0 or size > MAX_COVER_FRAME_BYTES:
                continue
            encoded = base64.b64encode(output.read_bytes()).decode("ascii")
            images.append(f"data:image/jpeg;base64,{encoded}")
    if not images:
        raise CoverFrameUnavailable("视频开头画面提取失败。")
    return images[:MAX_COVER_FRAMES]


__all__ = [
    "COVER_FRAME_TIMESTAMPS",
    "CoverFrameUnavailable",
    "clean_generated_title",
    "clean_title",
    "extract_cover_frame_data_urls",
    "is_meaningful_title",
    "is_placeholder_title",
    "normalize_title_source",
    "title_needs_generation",
]
