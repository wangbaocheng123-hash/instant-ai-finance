from __future__ import annotations

import os
import re
from pathlib import Path

from .settings import ROOT_DIR


CREATOR_DATA_ROOT = Path(
    os.getenv("BLOGGER_AGENT_MEDIA_DIR", str(ROOT_DIR / "博主数据"))
).expanduser().resolve()
INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def creator_folder_name(name: str) -> str:
    """Return a readable Windows-safe folder name for one creator."""

    value = INVALID_WINDOWS_CHARS.sub("_", str(name or "").strip())
    value = re.sub(r"\s+", " ", value).rstrip(" .")[:80]
    if not value:
        value = "未命名博主"
    if value.upper() in RESERVED_WINDOWS_NAMES:
        value = f"{value}_博主"
    return value


def ensure_creator_directories(
    creator_name: str,
    *,
    data_root: Path | None = None,
) -> dict[str, Path]:
    root_base = (data_root or CREATOR_DATA_ROOT).resolve()
    root = (root_base / creator_folder_name(creator_name)).resolve()
    if not root.is_relative_to(root_base):
        raise RuntimeError("博主数据目录越界。")
    paths = {
        "root": root,
        "videos": root / "视频",
        "images": root / "图片",
        "originals": root / "视频原文",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def rename_creator_directory(
    old_name: str,
    new_name: str,
    *,
    data_root: Path | None = None,
) -> Path:
    root_base = (data_root or CREATOR_DATA_ROOT).resolve()
    source = (root_base / creator_folder_name(old_name)).resolve()
    target = (root_base / creator_folder_name(new_name)).resolve()
    if not source.is_relative_to(root_base) or not target.is_relative_to(root_base):
        raise RuntimeError("博主数据目录越界。")
    if source == target:
        return ensure_creator_directories(new_name, data_root=root_base)["root"]
    if source.exists() and target.exists():
        raise ValueError("新博主名称对应的数据文件夹已经存在，无法自动重命名。")
    if source.exists():
        source.rename(target)
    return ensure_creator_directories(new_name, data_root=root_base)["root"]
