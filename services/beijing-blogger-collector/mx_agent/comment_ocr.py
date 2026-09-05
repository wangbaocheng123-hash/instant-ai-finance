from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance


ROOT_DIR = Path(__file__).resolve().parent.parent
WINDOWS_OCR_SCRIPT = ROOT_DIR / "tools" / "windows_ocr.ps1"
DEFAULT_AUTHOR_NAME = "模型先生"
KNOWN_AUTHOR_NAMES = ("模型先生", "模型哥看世界")


def hidden_process_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ocr_image_payload(image_path: Path) -> dict[str, Any]:
    if not WINDOWS_OCR_SCRIPT.exists():
        raise RuntimeError("Windows OCR script is missing")
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_OCR_SCRIPT),
            "-ImagePath",
            str(image_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        creationflags=hidden_process_flags(),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "OCR failed").strip())
    return json.loads(result.stdout)


def ocr_image_lines(image_path: Path) -> list[str]:
    payload = ocr_image_payload(image_path)
    return [str(item.get("text") or "").strip() for item in payload.get("lines", [])]


def ocr_image_comments(
    image_path: Path,
    author_name: str = DEFAULT_AUTHOR_NAME,
) -> tuple[list[str], list[dict[str, Any]]]:
    payload = ocr_image_payload(image_path)
    lines = insert_visual_author_markers(image_path, payload, author_name=author_name)
    positioned = parse_positioned_comments(image_path, payload, author_name=author_name)
    return lines, positioned or parse_ocr_comments(lines, author_name=author_name)


def parse_positioned_comments(
    image_path: Path,
    payload: dict[str, Any],
    author_name: str = DEFAULT_AUTHOR_NAME,
) -> list[dict[str, Any]]:
    """Parse Douyin comment screenshots without losing reply indentation.

    Windows OCR returns line coordinates.  Douyin uses those coordinates to
    distinguish a top-level comment from replies, so flattening the payload to
    strings first loses essential information.
    """
    line_items: list[dict[str, Any]] = []
    for source in payload.get("lines", []):
        bounds = line_bounds(source)
        text = positioned_line_text(source)
        if bounds and text:
            line_items.append({"text": text, "bounds": bounds})
    line_items.sort(key=lambda item: (item["bounds"][1], item["bounds"][0]))

    time_items = [item for item in line_items if is_time_line(item["text"])]
    if not time_items:
        return []

    image = Image.open(image_path).convert("RGB")
    red_tags = red_author_tag_boxes(image)
    comments: list[dict[str, Any]] = []
    previous_time_bottom = 0

    for time_item in time_items:
        time_bounds = time_item["bounds"]
        region_start = previous_time_bottom
        region_items = [
            item
            for item in line_items
            if item["bounds"][1] >= region_start
            and item["bounds"][1] < time_bounds[1]
            and not is_author_liked_line(item["text"])
        ]
        content_items = positioned_content_items(region_items, author_name=author_name)
        previous_time_bottom = time_bounds[3]
        if not content_items:
            continue

        # An emoji-only author reply is invisible to Windows OCR.  In that
        # case the only OCR line before the timestamp is the author name; it
        # must not be stored as the reply body.
        if len(content_items) == 1 and clean_author(content_items[0]["text"]) == author_name:
            continue

        first_content = content_items[0]
        content_left = first_content["bounds"][0]
        author_items = [
            item
            for item in region_items
            if item["bounds"][3] <= first_content["bounds"][1] - 3
            and not should_skip_line(item["text"])
            and not is_time_line(item["text"])
            and not is_metric_line(item["text"])
        ]
        author = clean_author(author_items[-1]["text"]) if author_items else ""
        enlarged_author = recognize_nickname(image, first_content["bounds"])
        if plausible_nickname(enlarged_author) and (
            not plausible_nickname(author) or len(enlarged_author) >= len(author)
        ):
            author = enlarged_author

        author_item = author_items[-1] if author_items else None
        has_author_tag = bool(
            author_item
            and clean_author(author_item["text"]) == author_name
            and any(
                author_tag_matches_line(tag, author_item["bounds"])
                for tag in red_tags
            )
        )
        if has_author_tag:
            author = author_name

        text = "".join(item["text"] for item in content_items).strip()
        text = re.sub(r"[0@]{2,}(?=[，。！？]|$)", "", text)
        text = text.replace("不稔", "不稳")
        if not text:
            continue
        comments.append(
            {
                "author": author or "识图用户",
                "published_at": format_time_line(time_item["text"]),
                "like_count": positioned_like_count(time_item, line_items),
                "text": text,
                "kind": "author_reply" if has_author_tag and author == author_name else "user_comment",
                "content_left": content_left,
                "_timestamp_bottom": time_bounds[3],
            }
        )

    if not comments:
        return []

    root_left = min(comment["content_left"] for comment in comments)
    last_root_index: int | None = None
    for index, comment in enumerate(comments):
        reply_depth = 1 if comment["content_left"] >= root_left + 22 and last_root_index is not None else 0
        comment["reply_depth"] = reply_depth
        if reply_depth:
            comment["parent_index"] = last_root_index
            if comment["kind"] != "author_reply":
                comment["kind"] = "user_reply"
        else:
            last_root_index = index

    for item in line_items:
        if not is_author_liked_line(item["text"]):
            continue
        preceding = [
            (index, comment)
            for index, comment in enumerate(comments)
            if comment["_timestamp_bottom"] <= item["bounds"][1]
        ]
        if preceding:
            comments[preceding[-1][0]]["author_liked"] = True

    for comment in comments:
        comment.pop("content_left", None)
        comment.pop("_timestamp_bottom", None)
    return comments


def positioned_content_items(
    region_items: list[dict[str, Any]],
    author_name: str = DEFAULT_AUTHOR_NAME,
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in region_items
        if not should_skip_line(item["text"])
        and not is_time_line(item["text"])
        and not is_metric_line(item["text"])
        and not is_author_liked_line(item["text"])
        and not is_avatar_ocr_noise(item)
    ]
    if not candidates:
        return []

    max_height = max(item["bounds"][3] - item["bounds"][1] for item in candidates)
    first_index = None
    for index, item in enumerate(candidates):
        height = item["bounds"][3] - item["bounds"][1]
        text = item["text"]
        if height >= max(17, max_height * 0.82) and (len(text) >= 3 or re.search(r"[，。！？?]", text)):
            first_index = index
            break
    if first_index is None:
        first_index = 1 if len(candidates) > 1 and plausible_nickname(candidates[0]["text"]) else 0

    content = candidates[first_index:]
    if len(content) > 1 and plausible_nickname(content[0]["text"]):
        first_height = content[0]["bounds"][3] - content[0]["bounds"][1]
        next_height = content[1]["bounds"][3] - content[1]["bounds"][1]
        first_left = content[0]["bounds"][0]
        next_left = content[1]["bounds"][0]
        next_text = content[1]["text"]
        next_looks_like_body = (
            len(next_text) >= max(8, len(content[0]["text"]) + 3)
            or bool(re.search(r"[，。！？?]", next_text))
        )
        if (
            first_height + 2 < next_height
            or first_left + 18 < next_left
            or clean_author(content[0]["text"]) == author_name
            or next_looks_like_body
        ):
            content = content[1:]
    return content


def positioned_line_text(source: dict[str, Any]) -> str:
    """Clean OCR line text while removing wide follow-badge artifacts."""
    words = []
    for word in source.get("words") or []:
        token = str(word.get("text") or "")
        try:
            width = float(word.get("width") or 0)
        except (TypeError, ValueError):
            width = 0.0
        words.append((token, width))
    normal_widths = [width for token, width in words if width > 0 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", token)]
    median_width = sorted(normal_widths)[len(normal_widths) // 2] if normal_widths else 0.0
    kept = [
        token
        for token, width in words
        if not (token in {"一", "—", "-"} and median_width > 0 and width > median_width * 2.2)
    ]
    value = clean_ocr_line("".join(kept) if kept else source.get("text") or "")
    value = re.sub(r"(你的关注|你已关注|已关注)$", "", value)
    return value


def is_avatar_ocr_noise(item: dict[str, Any]) -> bool:
    left, top, right, bottom = item["bounds"]
    return left < 45 and len(item["text"]) <= 2 and (bottom - top) >= 24


def plausible_nickname(value: str) -> bool:
    text = clean_author(value)
    if not text or len(text) > 20 or is_time_line(text) or is_author_liked_line(text):
        return False
    if "回复" in text or "分享" in text:
        return False
    if re.fullmatch(r"\d+", text):
        return False
    return not bool(re.search(r"[，。！？?]", text))


def recognize_nickname(
    image: Image.Image,
    content_bounds: tuple[int, int, int, int],
) -> str:
    left, top, _, _ = content_bounds
    crop_left = max(0, left - 10)
    crop_top = max(0, top - 45)
    crop_bottom = max(crop_top + 1, top - 3)
    crop_right = min(image.width, crop_left + 240)
    crop = image.crop((crop_left, crop_top, crop_right, crop_bottom))
    if crop.width < 4 or crop.height < 4:
        return ""
    scale = 5
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    crop = ImageEnhance.Contrast(crop).enhance(3.0)
    crop = ImageEnhance.Sharpness(crop).enhance(2.0)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            temp_path = Path(handle.name)
        crop.save(temp_path, format="PNG")
        nickname_payload = ocr_image_payload(temp_path)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)

    candidates = []
    for item in nickname_payload.get("lines", []):
        candidate = clean_author(item.get("text") or "")
        if plausible_nickname(candidate):
            candidates.append(candidate)
    return candidates[-1] if candidates else ""


def positioned_like_count(time_item: dict[str, Any], line_items: list[dict[str, Any]]) -> int:
    text = time_item["text"]
    tail = text.split("回复", 1)[-1] if "回复" in text else ""
    digits = re.sub(r"\D+", "", tail)
    counts = [int(digits)] if digits else []
    for item in line_items:
        if item is time_item or not overlaps_y(time_item["bounds"], item["bounds"]):
            continue
        if item["bounds"][0] <= time_item["bounds"][2] + 12:
            continue
        number = re.sub(r"\D+", "", item["text"])
        if number and len(number) <= 6:
            counts.append(int(number))
    # Douyin places reply/share/like metrics on the line immediately below
    # the timestamp.  Associate that line with the comment above it.
    for item in line_items:
        if item is time_item or not is_metric_line(item["text"]):
            continue
        vertical_gap = item["bounds"][1] - time_item["bounds"][3]
        if vertical_gap < 0 or vertical_gap > 45:
            continue
        tail = item["text"].split("分享", 1)[-1]
        numbers = [int(value) for value in re.findall(r"\d{1,6}", tail)]
        counts.extend(numbers)
    return max(counts, default=0)


def is_author_liked_line(line: str) -> bool:
    compact = clean_ocr_line(line)
    return compact.startswith("作者") and compact.endswith("过") and len(compact) <= 6


def parse_ocr_comments(
    lines: list[str],
    author_name: str = DEFAULT_AUTHOR_NAME,
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    cleaned_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = clean_ocr_line(lines[index])
        if is_metric_line(line) and index + 1 < len(lines):
            next_line = clean_ocr_line(lines[index + 1])
            if re.fullmatch(r"\d{1,6}", next_line):
                line = f"{line}{next_line}"
                index += 1
        cleaned_lines.append(line)
        index += 1

    def finish_current() -> None:
        nonlocal current
        if not current:
            return
        text = "".join(current.pop("text_lines", [])).strip()
        author = clean_author(current.get("author") or "")
        if text:
            current["author"] = author or "识图用户"
            current["text"] = text
            current["kind"] = "author_reply" if author == author_name else "user_comment"
            comments.append(current)
        current = None

    for line_index, line in enumerate(cleaned_lines):
        if should_skip_line(line):
            continue

        if is_metric_line(line):
            if current is not None:
                current["like_count"] = parse_like_count(line)
                finish_current()
            continue

        if is_time_line(line):
            if current is not None:
                current["published_at"] = format_time_line(line)
            continue

        if is_author_line(line, author_name=author_name):
            explicit_author = is_explicit_author_line(line, author_name=author_name)
            current_has_text = bool(current and current.get("text_lines"))
            next_line = next_meaningful_line(cleaned_lines, line_index + 1)

            # OCR often mistakes a short, punctuation-free comment sentence for
            # a username.  Once a username has just been opened, its first line
            # must be treated as comment text.  A line immediately followed by
            # the timestamp is also comment text, not another username.
            if not explicit_author and (
                (current is not None and not current_has_text)
                or (next_line and is_time_line(next_line))
            ):
                if current is None:
                    current = {
                        "author": "识图用户",
                        "text_lines": [],
                        "published_at": "",
                        "like_count": 0,
                    }
                current["text_lines"].append(line)
                continue

            finish_current()
            current = {
                "author": clean_author(line),
                "text_lines": [],
                "published_at": "",
                "like_count": 0,
            }
            continue

        if current is None:
            current = {
                "author": "识图用户",
                "text_lines": [],
                "published_at": "",
                "like_count": 0,
            }
        current["text_lines"].append(line)

    finish_current()
    return comments


def next_meaningful_line(lines: list[str], start: int) -> str:
    for line in lines[start:]:
        clean = clean_ocr_line(line)
        if not should_skip_line(clean):
            return clean
    return ""


def is_explicit_author_line(line: str, author_name: str = DEFAULT_AUTHOR_NAME) -> bool:
    return (
        (author_name in line and len(line) <= max(18, len(author_name) + 6))
        or any(mark in line for mark in ("▸", "卜", ">"))
    )


def insert_visual_author_markers(
    image_path: Path,
    payload: dict[str, Any],
    author_name: str = DEFAULT_AUTHOR_NAME,
) -> list[str]:
    line_items = []
    for line in payload.get("lines", []):
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        line_items.append({"text": text, "bounds": line_bounds(line)})

    if not line_items:
        return []

    image = Image.open(image_path).convert("RGB")
    red_tags = red_author_tag_boxes(image)
    if not red_tags:
        return [item["text"] for item in line_items]

    marker_indices: set[int] = set()
    for index, item in enumerate(line_items):
        clean = clean_ocr_line(item["text"])
        bounds = item.get("bounds")
        if (
            clean_author(clean) == author_name
            and bounds
            and any(author_tag_matches_line(tag, bounds) for tag in red_tags)
        ):
            marker_indices.add(index)

    result: list[str] = []
    for index, item in enumerate(line_items):
        if index in marker_indices:
            if not result or clean_author(result[-1]) != author_name:
                result.append(f"{author_name}作者")
        result.append(item["text"])
    return result


def line_bounds(line: dict[str, Any]) -> tuple[int, int, int, int] | None:
    words = line.get("words") or []
    boxes = []
    for word in words:
        try:
            x = float(word.get("x"))
            y = float(word.get("y"))
            width = float(word.get("width"))
            height = float(word.get("height"))
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            boxes.append((x, y, x + width, y + height))
    if not boxes:
        return None
    left = int(min(box[0] for box in boxes))
    top = int(min(box[1] for box in boxes))
    right = int(max(box[2] for box in boxes))
    bottom = int(max(box[3] for box in boxes))
    return left, top, right, bottom


def is_author_tag_red(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red >= 190 and 20 <= green <= 120 and 45 <= blue <= 150 and red - green >= 95 and red - blue >= 65


def has_red_pixels_near(image: Image.Image, bounds: tuple[int, int, int, int]) -> bool:
    width, height = image.size
    left, top, right, bottom = bounds
    scan_left = max(0, left - 4)
    scan_top = max(0, top - 8)
    scan_right = min(width, right + 96)
    scan_bottom = min(height, bottom + 8)
    count = 0
    for y in range(scan_top, scan_bottom):
        for x in range(scan_left, scan_right):
            if is_author_tag_red(image.getpixel((x, y))):
                count += 1
                if count >= 20:
                    return True
    return False


def red_author_tag_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    width, height = image.size
    visited: set[tuple[int, int]] = set()
    boxes: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            if (x, y) in visited or not is_author_tag_red(image.getpixel((x, y))):
                continue
            stack = [(x, y)]
            visited.add((x, y))
            xs: list[int] = []
            ys: list[int] = []
            while stack:
                cx, cy = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height or (nx, ny) in visited:
                        continue
                    if is_author_tag_red(image.getpixel((nx, ny))):
                        visited.add((nx, ny))
                        stack.append((nx, ny))
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            box_width = right - left + 1
            box_height = bottom - top + 1
            area = len(xs)
            density = area / max(1, box_width * box_height)
            aspect_ratio = box_width / max(1, box_height)
            # The Douyin author badge is a compact, filled red rounded
            # rectangle.  Small red emoji fragments and hearts previously
            # passed the broad size check and could mark an unrelated user as
            # the account author.
            if (
                20 <= box_width <= 90
                and 10 <= box_height <= 36
                and 1.2 <= aspect_ratio <= 4.8
                and density >= 0.18
                and area >= 80
            ):
                boxes.append((left, top, right, bottom))
    return boxes


def overlaps_y(first: tuple[int, int, int, int], second: tuple[int, int, int, int] | None) -> bool:
    if not second:
        return False
    return max(first[1], second[1]) <= min(first[3], second[3])


def author_tag_matches_line(
    tag: tuple[int, int, int, int],
    line: tuple[int, int, int, int] | None,
) -> bool:
    """Return true only when a red author badge belongs to this nickname row.

    The old parser accepted any red component between two timestamps.  That
    allowed the badge on the next reply (or another red UI element) to mark
    the preceding fan comment as an author reply.
    """
    if not line:
        return False
    tag_left, tag_top, tag_right, tag_bottom = tag
    line_left, line_top, line_right, line_bottom = line
    overlap = min(tag_bottom, line_bottom) - max(tag_top, line_top)
    if overlap <= 0:
        return False
    tag_height = max(1, tag_bottom - tag_top)
    line_height = max(1, line_bottom - line_top)
    if overlap / min(tag_height, line_height) < 0.45:
        return False

    # Windows OCR may include the badge itself in the nickname line bounds,
    # so allow a small horizontal overlap.  It must still sit in the right
    # half of that same row, never elsewhere in the comment block.
    tag_center_x = (tag_left + tag_right) / 2
    if tag_center_x < line_left + min(28, max(12, (line_right - line_left) * 0.18)):
        return False
    return tag_left <= line_right + 48


def text_line_after_tag(line_items: list[dict[str, Any]], tag: tuple[int, int, int, int]) -> int | None:
    tag_left, _, tag_right, tag_bottom = tag
    best_index = None
    best_distance = 10_000
    for index, item in enumerate(line_items):
        bounds = item.get("bounds")
        text = clean_ocr_line(item["text"])
        if not bounds or should_skip_line(text) or is_time_line(text) or is_metric_line(text):
            continue
        left, top, right, _ = bounds
        if top <= tag_bottom:
            continue
        distance = top - tag_bottom
        if distance > 70:
            continue
        if right < tag_left - 24 or left > tag_right + 240:
            continue
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def clean_ocr_line(value: str) -> str:
    line = unicodedata.normalize("NFKC", str(value or ""))
    line = re.sub(r"\s+", "", line)
    line = line.replace(",", "，").replace("?", "？").replace("!", "！")
    line = line.replace("．", "·").replace(".", "·")
    line = line.replace("巧分钟", "15分钟")
    line = line.replace("分针", "分钟")
    line = line.replace("视须", "视频")
    line = line.replace("没问恩", "没问题")
    line = line.replace("不稔", "不稳")
    line = re.sub(r"^[,，。、·:：;；!！?？0oO]+", "", line)
    line = re.sub(r"[,，。、·:：;；!！?？0oO]+$", "", line)
    line = re.sub(r"^[苤贮储]+[，,、·\s]*", "", line)
    for known_author in KNOWN_AUTHOR_NAMES:
        if re.fullmatch(rf"{re.escape(known_author)}(?:作者|作著)?", line):
            return known_author
    return line.strip()


def clean_author(value: str) -> str:
    author = clean_ocr_line(value)
    author = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff]+", "", author)
    author = re.sub(r"^[贮储]+", "", author)
    author = re.sub(r"(作者|作著)$", "", author)
    author = author.replace("卜", "▸").replace(">", "▸").replace("》", "▸")
    author = re.sub(r"\s*▸\s*", " ▸ ", author)
    # Stable correction for a small grey Douyin nickname that Windows OCR
    # repeatedly reads as “俯怖的妈咪”.
    if author == "俯怖的妈咪":
        author = "布布的妈咪"
    for known_author in KNOWN_AUTHOR_NAMES:
        if author.strip() == known_author:
            return known_author
        if "▸" not in author and author.startswith(known_author) and len(author) <= len(known_author) + 4:
            return known_author
    return author.strip()


def should_skip_line(line: str) -> bool:
    if not line:
        return True
    if re.fullmatch(r"\d{1,4}", line):
        return True
    if line.startswith("全部评论"):
        return True
    if line.startswith("展开") and "回复" in line:
        return True
    if line in {"回复", "分享", "作者"}:
        return True
    return False


def is_metric_line(line: str) -> bool:
    return "回复" in line and "分享" in line


def parse_like_count(line: str) -> int:
    text = line.split("分享", 1)[-1] if "分享" in line else line
    digits = re.sub(r"\D+", "", text)
    return int(digits or 0)


def is_time_line(line: str) -> bool:
    if len(line) > 24:
        return False
    if re.search(r"(刚刚|\d+分(?:钟)?前|\d+小时前|\d+天前|\d+月前|\d+年前)", line):
        return True
    # Absolute dates count as metadata only when the whole line has the
    # timestamp/location/reply shape.  “6月30号清仓” inside a comment is text.
    return bool(
        re.fullmatch(
            r"\d{1,2}(?:[-月/]\d{1,2})(?:日)?(?:·[^·]{1,10})?(?:·?回复)?",
            line,
        )
    )


def format_time_line(line: str) -> str:
    line = re.sub(r"回复[0-9oO]*$", "", line)
    line = line.replace("。", "·").replace("，", "·")
    line = line.replace("·", " · ")
    line = re.sub(r"\s+", " ", line)
    line = re.sub(r"(\d+)分前", r"\1分钟前", line)
    line = re.sub(r"回复$", "", line).strip()
    if line.endswith(" · 安"):
        line += "徽"
    return line.strip()


def is_author_line(line: str, author_name: str = DEFAULT_AUTHOR_NAME) -> bool:
    if "回复" in line or "分享" in line:
        return False
    if is_time_line(line):
        return False
    if len(line) < 2:
        return False
    if author_name in line and len(line) <= max(18, len(author_name) + 6):
        return True
    if any(mark in line for mark in ("▸", "卜", ">")) and len(line) <= 42:
        return True
    if len(line) > 24:
        return False
    if re.search(r"[，。！？?]", line):
        return False
    if re.search(r"^(我|你|他|她|这|那|说|不|一|老哥|兄)", line):
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", line))
