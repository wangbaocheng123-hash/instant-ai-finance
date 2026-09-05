from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_ffmpeg() -> Path:
    """Find an existing ffmpeg without requiring a separate installation."""
    configured = os.getenv("MX_AGENT_FFMPEG_PATH", "").strip()
    if configured and Path(configured).is_file():
        return Path(configured).resolve()

    found = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if found:
        return Path(found).resolve()

    local_app_data = Path(
        os.getenv("LOCALAPPDATA")
        or Path.home() / "AppData" / "Local"
    )
    apps_dir = local_app_data / "JianyingPro" / "Apps"
    candidates = list(apps_dir.glob("*/ffmpeg.exe")) if apps_dir.is_dir() else []
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime).resolve()

    raise ValueError("没有找到 ffmpeg，无法从视频提取音频")
