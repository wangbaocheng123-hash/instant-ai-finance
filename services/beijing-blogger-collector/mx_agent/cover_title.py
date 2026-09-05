from __future__ import annotations

import os
import re
import statistics
import subprocess
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image

from .comment_ocr import ocr_image_payload
from .media_tools import find_ffmpeg
from .settings import DATA_DIR
from .storage import Storage


RUNTIME_DIR = Path(
    os.getenv("MX_AGENT_RUNTIME_DIR")
    or Path(os.getenv("LOCALAPPDATA", str(DATA_DIR))) / "MXAgent"
)

FRAME_TIMESTAMPS = (0.0, 0.35, 0.8, 1.2)

_UI_LINE_PATTERNS = (
    re.compile(r"^(首页|朋友|消息|我|关注|推荐|热点|团购|同城|商城)$"),
    re.compile(r"^\d+$"),
    re.compile(r"^(特别关注|展开|回复|分享|个更新)$"),
    re.compile(r"理财有风险|投资需谨慎"),
    re.compile(r"^@?模型先生"),
    re.compile(r"^@?模型哥看世界"),
    re.compile(r"(分钟前|小时前|天前|刚刚)$"),
)


def clean_cover_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff]+", "", text)
    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff+&/·：:？！?！]+$", "", text)
    text = text.replace("?", "？").replace("!", "！")
    return text.strip("，,。.;；、:：`'\" ")


def _line_geometry(line: dict[str, Any]) -> tuple[float, float, float, float] | None:
    boxes = []
    for word in line.get("words") or []:
        try:
            x = float(word.get("x") or 0)
            y = float(word.get("y") or 0)
            width = float(word.get("width") or 0)
            height = float(word.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            boxes.append((x, y, x + width, y + height))
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _prepare_cover_line(
    line: dict[str, Any], image_width: int
) -> tuple[str, str, tuple[float, float, float, float] | None]:
    words: list[dict[str, Any]] = []
    for word in line.get("words") or []:
        try:
            item = {
                "text": str(word.get("text") or ""),
                "x": float(word.get("x") or 0),
                "y": float(word.get("y") or 0),
                "width": float(word.get("width") or 0),
                "height": float(word.get("height") or 0),
            }
        except (TypeError, ValueError):
            continue
        if item["width"] > 0 and item["height"] > 0:
            words.append(item)

    if not words or not any(word["text"].strip() for word in words):
        raw = str(line.get("text") or "")
        return clean_cover_title(raw), raw, _line_geometry(line)

    content_words = [word for word in words if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", word["text"])]
    median_height = statistics.median(word["height"] for word in content_words) if content_words else 0.0
    edge_limit = max(1.0, image_width * 0.03)
    filtered: list[dict[str, Any]] = []
    for word in words:
        is_left_edge_artifact = (
            word["x"] <= edge_limit
            and median_height > 0
            and word["height"] > median_height * 1.28
        )
        is_tiny_outline_artifact = median_height > 0 and word["height"] < median_height * 0.4
        if not is_left_edge_artifact and not is_tiny_outline_artifact:
            filtered.append(word)

    raw = " ".join(word["text"] for word in filtered)
    geometry_words = [
        word for word in filtered if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", word["text"])
    ]
    if not geometry_words:
        return clean_cover_title(raw), raw, None
    geometry = (
        min(word["x"] for word in geometry_words),
        min(word["y"] for word in geometry_words),
        max(word["x"] + word["width"] for word in geometry_words),
        max(word["y"] + word["height"] for word in geometry_words),
    )
    return clean_cover_title(raw), raw, geometry


def _valid_title_text(text: str) -> bool:
    if len(text) < 2 or len(text) > 48:
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", text):
        return False
    return not any(pattern.search(text) for pattern in _UI_LINE_PATTERNS)


def select_cover_title(payload: dict[str, Any], image_size: tuple[int, int]) -> dict[str, Any] | None:
    width, height = image_size
    candidates: list[dict[str, Any]] = []
    for line in payload.get("lines") or []:
        text, raw_text, geometry = _prepare_cover_line(line, width)
        if not geometry or not _valid_title_text(text):
            continue
        left, top, right, bottom = geometry
        line_height = bottom - top
        height_ratio = line_height / max(1.0, float(height))
        if height_ratio < 0.022:
            continue
        center_y = (top + bottom) / 2.0
        edge_penalty = 0.6 if center_y < height * 0.08 or center_y > height * 0.90 else 1.0
        score = (height_ratio * 1000.0 + min(len(text), 24) * 0.8) * edge_penalty
        candidates.append(
            {
                "text": text,
                "raw_text": raw_text,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "height": line_height,
                "height_ratio": height_ratio,
                "score": score,
            }
        )

    if not candidates:
        return None
    candidates.sort(key=lambda item: item["score"], reverse=True)
    primary = candidates[0]
    related = [
        item
        for item in candidates
        if item["height"] >= primary["height"] * 0.62
        and abs(((item["top"] + item["bottom"]) / 2.0) - ((primary["top"] + primary["bottom"]) / 2.0))
        <= primary["height"] * 2.8
    ]
    related.sort(key=lambda item: (item["top"], item["left"]))
    title = "".join(item["text"] for item in related)
    if len(title) > 60:
        title = primary["text"]

    second_height = candidates[1]["height"] if len(candidates) > 1 else primary["height"] * 0.45
    size_score = min(1.0, primary["height_ratio"] / 0.055)
    dominance = min(1.0, primary["height"] / max(1.0, second_height) / 1.8)
    confidence = round(min(0.99, 0.52 + 0.33 * size_score + 0.15 * dominance), 3)
    return {
        "title": title,
        "confidence": confidence,
        "score": round(float(primary["score"]), 3),
        "primary": primary,
        "candidates": candidates[:8],
        "image_width": width,
        "image_height": height,
    }


def _consensus_title_choice(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    choices = [attempt for attempt in attempts if attempt.get("selected")]
    if not choices:
        return None
    titles = [str(attempt["selected"].get("title") or "") for attempt in choices]

    def consensus_score(attempt: dict[str, Any]) -> float:
        selected = attempt["selected"]
        title = str(selected.get("title") or "")
        agreement = sum(SequenceMatcher(None, title, other).ratio() for other in titles)
        length_bonus = min(len(title), 24) * 0.025
        geometry_tiebreaker = float(selected.get("score") or 0.0) * 0.0001
        return agreement + length_bonus + geometry_tiebreaker

    return max(choices, key=consensus_score)


class CoverTitleRecognizer:
    """Read a cover/early video frame and save only its OCR title metadata."""

    def __init__(self, storage: Storage):
        self.storage = storage

    def recognize_title(self, video_id: int, *, force: bool = False) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        current = self.storage.get_video_title(video_id)
        if current and current.get("active_title") and not force:
            return {
                "video_id": video_id,
                "recognized": False,
                "skipped": True,
                "reason": "title already exists",
                "title_info": current,
            }

        asset = self._primary_visual_asset(video_id)
        source_path = Path(str(asset.get("local_path") or "")).resolve()
        if not source_path.is_file():
            raise ValueError("video or cover file does not exist")

        images: list[tuple[float, Path]] = []
        if str(asset.get("mime_type") or "").startswith("image/"):
            images.append((0.0, self._prepare_ocr_frame(source_path)))
        else:
            ffmpeg = self._ffmpeg_path()
            work_dir = RUNTIME_DIR / "cover_titles" / f"video_{video_id}"
            work_dir.mkdir(parents=True, exist_ok=True)
            for index, timestamp in enumerate(FRAME_TIMESTAMPS):
                frame_path = work_dir / f"frame_{index}_{str(timestamp).replace('.', '_')}.png"
                self._extract_frame(ffmpeg, source_path, frame_path, timestamp)
                if frame_path.is_file():
                    images.append((timestamp, self._prepare_ocr_frame(frame_path)))

        attempts: list[dict[str, Any]] = []
        for timestamp, image_path in images:
            payload = ocr_image_payload(image_path)
            with Image.open(image_path) as image:
                selected = select_cover_title(payload, image.size)
            attempts.append(
                {
                    "timestamp": timestamp,
                    "image_path": str(image_path),
                    "ocr_text": str(payload.get("text") or ""),
                    "selected": selected,
                }
            )
        best = _consensus_title_choice(attempts)

        if best is None:
            return {
                "video_id": video_id,
                "recognized": False,
                "skipped": False,
                "reason": "no reliable title text found on cover frames",
                "attempts": attempts,
                "title_info": current,
            }

        selected = best["selected"]
        title_info = self.storage.save_ocr_title(
            video_id,
            selected["title"],
            confidence=float(selected["confidence"]),
            frame_timestamp=float(best["timestamp"]),
            frame_path=str(best["image_path"]),
            raw={
                "engine": "windows-media-ocr",
                "asset_id": asset.get("id"),
                "source_path": str(source_path),
                "attempts": attempts,
            },
        )
        return {
            "video_id": video_id,
            "recognized": True,
            "skipped": False,
            "ocr_title": selected["title"],
            "confidence": selected["confidence"],
            "frame_timestamp": best["timestamp"],
            "title_info": title_info,
        }

    def recognize_all(self, *, force: bool = False, limit: int = 200) -> dict[str, Any]:
        results = []
        for video in self.storage.list_videos(limit=limit):
            try:
                results.append(self.recognize_title(int(video["id"]), force=force))
            except Exception as exc:
                results.append(
                    {
                        "video_id": int(video["id"]),
                        "recognized": False,
                        "skipped": False,
                        "error": str(exc),
                    }
                )
        return {
            "count": len(results),
            "recognized": sum(1 for item in results if item.get("recognized")),
            "skipped": sum(1 for item in results if item.get("skipped")),
            "failed": sum(1 for item in results if item.get("error")),
            "items": results,
        }

    def _primary_visual_asset(self, video_id: int) -> dict[str, Any]:
        assets = self.storage.list_assets(video_id)
        for asset in assets:
            if str(asset.get("mime_type") or "").startswith("video/") and asset.get("local_path"):
                return asset
        for asset in assets:
            mime = str(asset.get("mime_type") or "")
            asset_type = str(asset.get("asset_type") or "")
            source = str(asset.get("source") or "")
            if (
                mime.startswith("image/")
                and asset.get("local_path")
                and asset_type not in {"comment_screenshot"}
                and "comment" not in source.casefold()
            ):
                return asset
        raise ValueError("this record has no local video or cover image")

    def _ffmpeg_path(self) -> Path:
        return find_ffmpeg()

    @staticmethod
    def _prepare_ocr_frame(frame_path: Path) -> Path:
        with Image.open(frame_path) as image:
            if image.width <= 720:
                return frame_path
            target = frame_path.with_name(f"{frame_path.stem}_ocr.png")
            width = 576
            height = max(1, round(image.height * width / image.width))
            resized = image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
            resized.save(target, format="PNG")
            return target

    @staticmethod
    def _extract_frame(ffmpeg: Path, source_path: Path, target: Path, timestamp: float) -> None:
        command = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            str(target),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "cover frame extraction failed").strip())
