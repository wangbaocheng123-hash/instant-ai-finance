from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, build_opener

from PIL import Image

from .comment_ocr import ocr_image_payload
from .cover_title import RUNTIME_DIR, clean_cover_title
from .douyin import comment_signal
from .downloader_engine.profile_monitor import refine_created_at_from_title
from .settings import DATA_DIR
from .storage import Storage


AWEME_ID_RE = re.compile(r"(?<!\d)(\d{18,20})(?!\d)")
DOUYIN_VIDEO_URL = "https://www.douyin.com/video/{aweme_id}"
SYNC_CONFIG_PATH = DATA_DIR / "douyin_comment_sync.json"
SYNC_PREVIEW_DIR = DATA_DIR / "comment_sync_previews"
COMMENT_BROWSER_PROFILE = RUNTIME_DIR / "douyin-comment-browser"
COMMENT_CARD_SCREENSHOTS = RUNTIME_DIR / "douyin-comment-matches"
CHINA_TZ = timezone(timedelta(hours=8))
MAX_COMMENT_CSV_BYTES = 8 * 1024 * 1024
MAX_COMMENT_CSV_ROWS = 10_000
COMMENT_CSV_REQUIRED_HEADERS = {"评论ID", "用户昵称", "评论内容"}
REPLY_EXPAND_TEXT_PATTERNS = (
    r"^展开\s*\d+\s*条回复",
    r"^展开(?:更多|剩余).*回复",
    r"^查看(?:全部|更多|剩余).*回复",
    r"^更多回复",
    r"^展开更多$",
)
CHROME_EXECUTABLE_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Google"
    / "Chrome"
    / "Application"
    / "chrome.exe",
)


def chrome_executable_path() -> Path:
    for candidate in CHROME_EXECUTABLE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "没有找到 Google Chrome。请先安装谷歌 Chrome，评论采集器不会改用 Microsoft Edge。"
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def extract_aweme_id(*values: Any) -> str:
    for value in values:
        match = AWEME_ID_RE.search(str(value or ""))
        if match:
            return match.group(1)
    return ""


def canonical_video_url(aweme_id: str) -> str:
    return DOUYIN_VIDEO_URL.format(aweme_id=aweme_id)


def normalize_title(value: str) -> str:
    text = clean_cover_title(str(value or ""))
    text = text.replace("占", "点")
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text).casefold()


def title_similarity(left: str, right: str) -> float:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def average_hash(image: Image.Image, size: int = 16) -> tuple[int, ...]:
    gray = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    average = sum(pixels) / max(1, len(pixels))
    return tuple(1 if value >= average else 0 for value in pixels)


def hash_similarity(left: Image.Image, right: Image.Image) -> float:
    first = average_hash(left)
    second = average_hash(right)
    differences = sum(1 for a, b in zip(first, second) if a != b)
    return 1.0 - differences / max(1, len(first))


def _comment_id(item: dict[str, Any]) -> str:
    return str(item.get("cid") or item.get("comment_id") or "").strip()


def _comment_text(item: dict[str, Any]) -> str:
    return str(item.get("text") or item.get("content") or "").strip()


def _comment_author(item: dict[str, Any]) -> tuple[str, str]:
    user = item.get("user") or item.get("comment_user") or {}
    if not isinstance(user, dict):
        user = {}
    name = str(
        user.get("nickname")
        or user.get("nick_name")
        or item.get("nick_name")
        or item.get("nickname")
        or ""
    ).strip()
    uid = str(
        user.get("sec_uid")
        or user.get("uid")
        or item.get("comment_user_id")
        or ""
    ).strip()
    return name, uid


def _published_at(item: dict[str, Any]) -> str:
    value = item.get("create_time") or item.get("create_timestamp")
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat()


def _csv_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "y",
        "是",
        "作者",
    }


def _csv_int(value: Any) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return 0


def _csv_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:80]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.isoformat()


def _decode_comment_csv(content: bytes) -> str:
    if not content:
        raise ValueError("CSV文件为空。")
    if len(content) > MAX_COMMENT_CSV_BYTES:
        raise ValueError("CSV文件不能超过8MB。")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV编码无法识别，请使用UTF-8或GB18030编码。")


def parse_comments_csv(
    content: bytes,
    *,
    account_author: str = "模型先生",
    source_filename: str = "comments.csv",
) -> dict[str, Any]:
    """Parse a cloud-downloader CSV into the existing comment-sync shape."""
    text = _decode_comment_csv(content)
    csv.field_size_limit(1_000_000)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    raw_headers = list(reader.fieldnames or [])
    headers = [str(value or "").lstrip("\ufeff").strip() for value in raw_headers]
    if not headers:
        raise ValueError("CSV缺少表头。")
    missing_headers = sorted(COMMENT_CSV_REQUIRED_HEADERS - set(headers))
    if missing_headers:
        raise ValueError(f"CSV缺少必要字段：{'、'.join(missing_headers)}")
    header_map = dict(zip(raw_headers, headers))

    by_id: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    skipped_count = 0
    for row_number, source_row in enumerate(reader, start=1):
        if row_number > MAX_COMMENT_CSV_ROWS:
            raise ValueError(f"CSV评论不能超过{MAX_COMMENT_CSV_ROWS}条。")
        row = {
            normalized: str(source_row.get(raw, "") or "").strip()
            for raw, normalized in header_map.items()
        }
        if not any(row.values()):
            continue
        source_comment_id = row.get("评论ID", "")
        comment_text = row.get("评论内容", "")
        if not source_comment_id or not comment_text:
            skipped_count += 1
            continue

        author_label = row.get("公开标签", "")
        is_model = (
            _csv_bool(row.get("是否模型先生本人"))
            or row.get("用户昵称", "") == account_author
            or "作者" in author_label
        )
        author = account_author if is_model else (row.get("用户昵称", "") or "抖音用户")
        reply_target = row.get("回复目标评论ID", "")
        parent_id = row.get("父评论ID", "")
        if not parent_id and reply_target not in {"", "0", source_comment_id}:
            parent_id = reply_target

        actual_reply_user = row.get("实际被回复用户", "")
        replied_original = row.get("被回复的原评论", "")
        if not parent_id and is_model and (actual_reply_user or replied_original):
            context_key = f"{actual_reply_user}|{replied_original}"
            parent_id = "csv-context-" + hashlib.sha256(
                context_key.encode("utf-8")
            ).hexdigest()[:20]

        item = {
            "source_comment_id": source_comment_id,
            "author": author,
            "author_uid": row.get("用户标识", ""),
            "text": comment_text,
            "like_count": _csv_int(row.get("点赞数")),
            "reply_count": _csv_int(row.get("回复数")),
            "published_at": _csv_datetime(row.get("发布时间")),
            "captured_at": _csv_datetime(
                row.get("采集时间")
                or row.get("最后发现时间")
                or row.get("首次发现时间")
            ),
            "parent_source_comment_id": parent_id,
            "reply_depth": 1 if parent_id else 0,
            "kind": (
                "author_reply"
                if parent_id and is_model
                else ("user_reply" if parent_id else "user_comment")
            ),
            "author_liked": _csv_bool(row.get("是否博主点赞")),
            "ip_label": row.get("IP属地", ""),
            "public_label": author_label,
            "actual_reply_user": actual_reply_user,
            "replied_original_comment": replied_original,
            "reply_object": row.get("接口返回的回复对象", ""),
            "first_seen_at": _csv_datetime(row.get("首次发现时间")),
            "last_seen_at": _csv_datetime(row.get("最后发现时间")),
            "source_filename": source_filename,
            "display_order": row_number * 10,
            "synthetic_context": False,
        }
        if source_comment_id in by_id:
            duplicate_count += 1
        by_id[source_comment_id] = item

    if not by_id:
        raise ValueError("CSV中没有可导入的评论。")

    synthetic_items: list[dict[str, Any]] = []
    known_ids = set(by_id)
    for item in list(by_id.values()):
        parent_id = str(item.get("parent_source_comment_id") or "")
        if not parent_id or parent_id in known_ids:
            continue
        replied_original = str(item.get("replied_original_comment") or "").strip()
        if not replied_original:
            continue
        synthetic_items.append(
            {
                "source_comment_id": parent_id,
                "author": str(item.get("actual_reply_user") or "被回复用户"),
                "author_uid": "",
                "text": replied_original,
                "like_count": 0,
                "reply_count": 1,
                "published_at": "",
                "captured_at": str(item.get("captured_at") or ""),
                "parent_source_comment_id": "",
                "reply_depth": 0,
                "kind": "user_comment",
                "author_liked": False,
                "ip_label": "",
                "public_label": "",
                "actual_reply_user": "",
                "replied_original_comment": "",
                "reply_object": "",
                "first_seen_at": "",
                "last_seen_at": "",
                "source_filename": source_filename,
                "display_order": max(0, int(item["display_order"]) - 1),
                "synthetic_context": True,
            }
        )
        known_ids.add(parent_id)

    ordered = sorted(
        [*by_id.values(), *synthetic_items],
        key=lambda item: (
            int(item.get("display_order") or 0),
            int(item.get("reply_depth") or 0),
        ),
    )
    items = classify_comment_sections(ordered, account_author)
    for item in items:
        # A structured CSV is already the detailed source record. Import all
        # rows by default, then let the existing UI hide low-value content.
        item["include"] = True

    unresolved_parent_count = sum(
        1
        for item in items
        if item.get("parent_source_comment_id")
        and item.get("parent_source_comment_id") not in known_ids
    )
    author_interactions = sum(
        1
        for item in items
        if item.get("section") == "author_interaction"
        and not item.get("parent_source_comment_id")
    )
    fan_comments = sum(
        1
        for item in items
        if item.get("section") == "fan_comment"
        and not item.get("parent_source_comment_id")
    )
    return {
        "items": items,
        "aweme_id": extract_aweme_id(source_filename),
        "summary": {
            "captured": len(items),
            "csv_rows": len(by_id),
            "author_interactions": author_interactions,
            "fan_comments": fan_comments,
            "model_comments": sum(
                1 for item in items if item.get("author") == account_author
            ),
            "synthetic_contexts": len(synthetic_items),
            "duplicates_in_file": duplicate_count,
            "skipped_rows": skipped_count,
            "unresolved_parents": unresolved_parent_count,
        },
    }


def extract_comments_from_payload(
    payload: Any,
    *,
    response_url: str = "",
    account_author: str = "模型先生",
) -> list[dict[str, Any]]:
    """Extract Douyin comment objects from web response JSON.

    The web payload shape changes periodically.  This intentionally detects
    comment objects by their stable semantic fields instead of depending on one
    response envelope.
    """

    found: dict[str, dict[str, Any]] = {}
    query = parse_qs(urlparse(response_url).query)
    request_parent = str(
        (query.get("comment_id") or query.get("comment_id_str") or [""])[0]
    ).strip()

    def walk(value: Any, inherited_parent: str = "") -> None:
        if isinstance(value, list):
            for child in value:
                walk(child, inherited_parent)
            return
        if not isinstance(value, dict):
            return

        cid = _comment_id(value)
        text = _comment_text(value)
        current_parent = inherited_parent
        if cid and text:
            raw_parent = str(
                value.get("reply_id")
                or value.get("reply_comment_id")
                or value.get("reply_to_reply_id")
                or ""
            ).strip()
            if raw_parent and raw_parent not in {"0", cid}:
                current_parent = raw_parent
            elif request_parent and request_parent != cid:
                current_parent = request_parent

            author, author_uid = _comment_author(value)
            found[cid] = {
                "source_comment_id": cid,
                "author": author or "抖音用户",
                "author_uid": author_uid,
                "text": text,
                "like_count": int(
                    value.get("digg_count")
                    or value.get("like_count")
                    or 0
                ),
                "reply_count": int(
                    value.get("reply_comment_total")
                    or value.get("reply_count")
                    or 0
                ),
                "published_at": _published_at(value),
                "parent_source_comment_id": current_parent,
                "thread_root_source_comment_id": request_parent,
                "reply_depth": 1 if current_parent else 0,
                "kind": (
                    "author_reply"
                    if current_parent and author == account_author
                    else ("user_reply" if current_parent else "user_comment")
                ),
                "author_liked": bool(value.get("is_author_digged")),
                "ip_label": str(value.get("ip_label") or ""),
                "remote_total": int(value.get("item_comment_total") or 0),
                "response_url": response_url,
            }

        for key, child in value.items():
            if key in {"user", "comment_user", "aweme", "author"}:
                continue
            child_parent = cid if cid and key in {
                "reply_comment",
                "reply_comments",
                "replies",
                "reply_list",
            } else inherited_parent
            walk(child, child_parent)

    walk(payload)
    return list(found.values())


def is_low_value_comment(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    value = re.sub(r"\[[^\]]{1,12}\]", "", value)
    value = re.sub(r"@\S+", "", value)
    compact = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", value).casefold()
    if not compact:
        return True
    if re.fullmatch(r"(哈){1,12}", compact):
        return True
    if re.fullmatch(r"(呵){1,12}", compact):
        return True
    if re.fullmatch(r"(嘿){1,12}", compact):
        return True
    if re.fullmatch(r"(嘻){1,12}", compact):
        return True
    if re.fullmatch(r"6{1,12}", compact):
        return True
    return compact in {
        "嗯",
        "哦",
        "啊",
        "好",
        "赞",
        "点赞",
        "支持",
        "收到",
        "路过",
        "来了",
        "笑死",
        "笑死我了",
        "呵呵",
        "嘿嘿",
        "嘻嘻",
        "谢谢",
        "感谢",
        "学习了",
        "关注了",
        "蹲",
        "蹲一个",
    }


def classify_comment_sections(
    comments: list[dict[str, Any]],
    account_author: str,
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("source_comment_id") or ""): item
        for item in comments
        if item.get("source_comment_id")
    }
    interaction_roots: set[str] = set()

    def root_id(item: dict[str, Any]) -> str:
        current = item
        seen: set[str] = set()
        while current.get("parent_source_comment_id"):
            parent_id = str(current.get("parent_source_comment_id") or "")
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if not parent:
                return parent_id
            current = parent
        return str(current.get("source_comment_id") or "")

    for item in comments:
        if str(item.get("author") or "") == account_author:
            interaction_roots.add(root_id(item))

    result: list[dict[str, Any]] = []
    for index, source in enumerate(comments):
        item = dict(source)
        root = root_id(item)
        item["root_source_comment_id"] = root
        item["section"] = (
            "author_interaction"
            if root in interaction_roots
            else "fan_comment"
        )
        item["preview_index"] = index
        is_thread_context = (
            item["section"] == "author_interaction"
            and (
                str(item.get("source_comment_id") or "") == root
                or item.get("kind") == "author_reply"
            )
        )
        item["low_value"] = (
            is_low_value_comment(str(item.get("text") or ""))
            and not is_thread_context
        )
        item["include"] = not item["low_value"]
        result.append(item)
    return result


def comment_reply_completeness(
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare visible reply rows with each root comment's advertised total."""

    by_id = {
        str(item.get("source_comment_id") or ""): item
        for item in comments
        if item.get("source_comment_id")
    }
    roots = {
        comment_id: item
        for comment_id, item in by_id.items()
        if not item.get("parent_source_comment_id")
        and int(item.get("reply_count") or 0) > 0
    }
    captured_by_root = {comment_id: 0 for comment_id in roots}
    orphan_replies = 0

    def resolve_root(item: dict[str, Any]) -> str:
        declared_root = str(item.get("thread_root_source_comment_id") or "")
        if declared_root in roots:
            return declared_root
        current = item
        seen: set[str] = set()
        while current.get("parent_source_comment_id"):
            parent_id = str(current.get("parent_source_comment_id") or "")
            if not parent_id or parent_id in seen:
                return ""
            if parent_id in roots:
                return parent_id
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if not parent:
                return ""
            current = parent
        current_id = str(current.get("source_comment_id") or "")
        return current_id if current_id in roots else ""

    for item in comments:
        if not item.get("parent_source_comment_id"):
            continue
        root_id = resolve_root(item)
        if root_id:
            captured_by_root[root_id] += 1
        else:
            orphan_replies += 1

    incomplete_groups: list[dict[str, Any]] = []
    expected_replies = 0
    captured_replies = 0
    for root_id, root in roots.items():
        expected = max(0, int(root.get("reply_count") or 0))
        captured = captured_by_root.get(root_id, 0)
        expected_replies += expected
        captured_replies += captured
        if captured < expected:
            incomplete_groups.append(
                {
                    "source_comment_id": root_id,
                    "author": str(root.get("author") or ""),
                    "text": str(root.get("text") or "")[:80],
                    "expected": expected,
                    "captured": captured,
                    "missing": expected - captured,
                }
            )

    return {
        "reply_groups": len(roots),
        "reply_groups_complete": len(roots) - len(incomplete_groups),
        "reply_groups_incomplete": len(incomplete_groups),
        "expected_replies": expected_replies,
        "captured_replies": captured_replies,
        "missing_replies": sum(item["missing"] for item in incomplete_groups),
        "orphan_replies": orphan_replies,
        "incomplete_groups": incomplete_groups,
    }


@dataclass
class BrowserJob:
    operation: Callable[[], Any]
    result_queue: queue.Queue[Any] = field(default_factory=lambda: queue.Queue(maxsize=1))


class DouyinCommentSyncService:
    def __init__(self, storage: Storage, *, execution_lock: Any | None = None) -> None:
        self.storage = storage
        self._execution_lock = execution_lock
        self._config_lock = threading.Lock()
        self._jobs: queue.Queue[BrowserJob] = queue.Queue()
        self._worker = threading.Thread(
            target=self._browser_worker,
            name="douyin-comment-browser",
            daemon=True,
        )
        self._worker.start()
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

    def status(self, video_id: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        raw = self._video_raw(video)
        aweme_id = extract_aweme_id(
            raw.get("douyin_aweme_id"),
            video.get("url"),
            video.get("title"),
            raw.get("source_path"),
        )
        account = self._account_config(str(video.get("author") or "模型先生"))
        return {
            "bound": bool(aweme_id),
            "aweme_id": aweme_id,
            "url": canonical_video_url(aweme_id) if aweme_id else str(video.get("url") or ""),
            "match_source": str(raw.get("douyin_match_source") or ""),
            "match_confidence": float(raw.get("douyin_match_confidence") or 0),
            "profile_url": str(account.get("profile_url") or ""),
            "browser_profile": str(COMMENT_BROWSER_PROFILE),
            "browser_name": "Google Chrome",
            "browser_executable": str(chrome_executable_path()),
            "single_browser_lock": self._execution_lock is not None,
        }

    def bind_link(self, video_id: int, link: str) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        resolved = self._resolve_share_link(link)
        aweme_id = extract_aweme_id(resolved, link)
        if not aweme_id:
            raise ValueError("没有从链接中找到抖音作品ID")
        updated = self.storage.bind_video_remote_source(
            video_id,
            url=canonical_video_url(aweme_id),
            aweme_id=aweme_id,
            match_source="manual-share-link",
            match_confidence=1.0,
            metadata={"submitted_link": link, "resolved_link": resolved},
        )
        return {
            "video": updated,
            "comment_sync": self.status(video_id),
            "message": "抖音作品链接已绑定。",
        }

    def auto_match(self, video_id: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        detail = self.storage.get_video_detail(video_id) or {}
        raw = self._video_raw(video)
        asset_values: list[str] = []
        for asset in detail.get("assets") or []:
            asset_values.extend(
                [
                    str(asset.get("original_name") or ""),
                    str(asset.get("local_path") or ""),
                    json.dumps(asset.get("raw_json") or {}, ensure_ascii=False),
                ]
            )
        aweme_id = extract_aweme_id(
            video.get("url"),
            raw.get("douyin_aweme_id"),
            video.get("title"),
            raw.get("source_path"),
            *asset_values,
        )
        if aweme_id:
            updated = self.storage.bind_video_remote_source(
                video_id,
                url=canonical_video_url(aweme_id),
                aweme_id=aweme_id,
                match_source="embedded-aweme-id",
                match_confidence=1.0,
                metadata={
                    "title": str(video.get("title") or ""),
                    "published_at": str(video.get("published_at") or ""),
                },
            )
            return {
                "matched": True,
                "requires_selection": False,
                "confidence": 1.0,
                "aweme_id": aweme_id,
                "url": canonical_video_url(aweme_id),
                "source": "embedded-aweme-id",
                "video": updated,
            }

        account = self._account_config(str(video.get("author") or "模型先生"))
        profile_url = str(account.get("profile_url") or "")
        if not profile_url:
            return {
                "matched": False,
                "requires_profile": True,
                "message": "尚未获得模型先生账号主页，请先为一条作品绑定分享链接。",
            }
        return self._submit_browser_job(
            lambda: self._match_from_profile(video_id, profile_url),
            timeout=120,
        )

    def collect_preview(self, video_id: int, limit: int = 1500) -> dict[str, Any]:
        match = self.auto_match(video_id)
        if not match.get("matched"):
            return match
        return self._submit_browser_job(
            lambda: self._collect_from_video(video_id, max(20, min(int(limit), 3000))),
            timeout=600,
        )

    def preview_csv(
        self,
        video_id: int,
        *,
        content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        safe_filename = Path(str(filename or "comments.csv")).name
        if Path(safe_filename).suffix.casefold() != ".csv":
            raise ValueError("请选择CSV评论文件。")

        account_author = str(video.get("author") or "模型先生")
        parsed = parse_comments_csv(
            content,
            account_author=account_author,
            source_filename=safe_filename,
        )
        csv_aweme_id = str(parsed.get("aweme_id") or "")
        raw = self._video_raw(video)
        asset_values: list[str] = []
        for asset in self.storage.list_assets(video_id):
            asset_values.extend(
                [
                    str(asset.get("original_name") or ""),
                    str(asset.get("local_path") or ""),
                    json.dumps(asset.get("raw_json") or {}, ensure_ascii=False),
                ]
            )
        bound_aweme_id = extract_aweme_id(
            raw.get("douyin_aweme_id"),
            video.get("url"),
            video.get("title"),
            raw.get("source_path"),
            *asset_values,
        )
        if csv_aweme_id and bound_aweme_id and csv_aweme_id != bound_aweme_id:
            raise ValueError(
                "CSV作品ID与当前作品不一致："
                f"CSV={csv_aweme_id}，当前作品={bound_aweme_id}。"
            )
        aweme_id = csv_aweme_id or bound_aweme_id
        items = list(parsed["items"])
        source_ids = [str(item["source_comment_id"]) for item in items]
        existing_ids: set[str] = set()
        if source_ids:
            with self.storage.connect() as conn:
                stored_ids = {
                    str(row["source_comment_id"])
                    for row in conn.execute(
                        """
                        SELECT source_comment_id
                        FROM comments
                        WHERE video_id = ?
                          AND source = 'douyin-web'
                        """,
                        (video_id,),
                    ).fetchall()
                }
                existing_ids = stored_ids.intersection(source_ids)

        preview_id = os.urandom(16).hex()
        url = canonical_video_url(aweme_id) if aweme_id else str(video.get("url") or "")
        summary = dict(parsed["summary"])
        summary.update(
            {
                "already_in_database": len(existing_ids),
                "new_comments": len(source_ids) - len(existing_ids),
            }
        )
        record = {
            "preview_id": preview_id,
            "video_id": video_id,
            "source_type": "manual-cloud-csv",
            "comment_source": "douyin-web",
            "source_filename": safe_filename,
            "aweme_id": aweme_id,
            "url": url,
            "created_at": now_iso(),
            "items": items,
            "summary": summary,
            "bind_aweme_on_confirm": bool(csv_aweme_id and not bound_aweme_id),
        }
        SYNC_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        (SYNC_PREVIEW_DIR / f"{preview_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "matched": True,
            "needs_login": False,
            "preview_id": preview_id,
            "requires_confirmation": True,
            "source_type": "manual-cloud-csv",
            "source_filename": safe_filename,
            "aweme_id": aweme_id,
            "url": url,
            "items": items,
            "summary": summary,
            "message": (
                f"已读取CSV中的{summary['csv_rows']}条有效评论，"
                f"跳过{summary['skipped_rows']}条空正文或无ID记录；"
                "请确认后导入数据库。"
            ),
        }

    def confirm_preview(
        self,
        video_id: int,
        preview_id: str,
        selected_indexes: list[int] | None = None,
    ) -> dict[str, Any]:
        preview_id = str(preview_id or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{32}", preview_id):
            raise ValueError("invalid preview id")
        path = SYNC_PREVIEW_DIR / f"{preview_id}.json"
        if not path.is_file():
            raise ValueError("评论采集预览已失效，请重新采集")
        preview = json.loads(path.read_text(encoding="utf-8"))
        if int(preview.get("video_id") or 0) != video_id:
            raise ValueError("preview does not belong to this video")

        selected = set(selected_indexes or [])
        use_selection = selected_indexes is not None
        comment_source = str(preview.get("comment_source") or "douyin-web")
        created = 0
        updated = 0
        imported: list[dict[str, Any]] = []
        for item in preview.get("items") or []:
            preview_index = int(item.get("preview_index") or 0)
            if use_selection and preview_index not in selected:
                continue
            if not use_selection and item.get("low_value"):
                continue
            text = str(item.get("text") or "").strip()
            source_comment_id = str(item.get("source_comment_id") or "").strip()
            if not text or not source_comment_id:
                continue
            signal = comment_signal(text)
            raw = {
                "kind": str(item.get("kind") or "user_comment"),
                "section": str(item.get("section") or "fan_comment"),
                "reply_depth": int(item.get("reply_depth") or 0),
                "parent_source_comment_id": str(
                    item.get("parent_source_comment_id") or ""
                ),
                "root_source_comment_id": str(
                    item.get("root_source_comment_id") or ""
                ),
                "author_uid": str(item.get("author_uid") or ""),
                "author_liked": bool(item.get("author_liked")),
                "ip_label": str(item.get("ip_label") or ""),
                "low_value": bool(item.get("low_value")),
                "aweme_id": str(preview.get("aweme_id") or ""),
                "source_url": str(preview.get("url") or ""),
                "confirmed_by_user": True,
                "preview_id": preview_id,
                "display_order": int(
                    item.get("display_order") or (preview_index + 1)
                ),
                "public_label": str(item.get("public_label") or ""),
                "actual_reply_user": str(item.get("actual_reply_user") or ""),
                "replied_original_comment": str(
                    item.get("replied_original_comment") or ""
                ),
                "reply_object": str(item.get("reply_object") or ""),
                "first_seen_at": str(item.get("first_seen_at") or ""),
                "last_seen_at": str(item.get("last_seen_at") or ""),
                "source_filename": str(
                    preview.get("source_filename")
                    or item.get("source_filename")
                    or ""
                ),
                "import_method": str(preview.get("source_type") or "douyin-web"),
                "synthetic_context": bool(item.get("synthetic_context")),
            }
            comment_id, was_created = self.storage.upsert_comment(
                {
                    "video_id": video_id,
                    "source": comment_source,
                    "source_comment_id": source_comment_id,
                    "author": str(item.get("author") or "抖音用户"),
                    "text": text,
                    "like_count": int(item.get("like_count") or 0),
                    "reply_count": int(item.get("reply_count") or 0),
                    "sentiment": signal["sentiment"],
                    "risk_level": signal["risk_level"],
                    "published_at": str(item.get("published_at") or ""),
                    "captured_at": str(item.get("captured_at") or "") or now_iso(),
                    "raw_json": raw,
                }
            )
            imported.append({"comment_id": comment_id, "created": was_created})
            if was_created:
                created += 1
            else:
                updated += 1
        if not imported:
            raise ValueError("没有选择可导入的评论")
        binding = None
        binding_error = ""
        if preview.get("bind_aweme_on_confirm") and preview.get("aweme_id"):
            aweme_id = str(preview["aweme_id"])
            try:
                binding = self.storage.bind_video_remote_source(
                    video_id,
                    url=canonical_video_url(aweme_id),
                    aweme_id=aweme_id,
                    match_source="manual-comment-csv",
                    match_confidence=1.0,
                    metadata={
                        "source_filename": str(preview.get("source_filename") or ""),
                        "preview_id": preview_id,
                    },
                )
            except Exception as exc:
                binding_error = str(exc)
        path.unlink(missing_ok=True)
        comments = self.storage.list_comments(video_id, limit=5000)
        return {
            "created": created,
            "updated": updated,
            "comments": comments,
            "total_comments": len(comments),
            "video": binding,
            "binding_error": binding_error,
        }

    def _collect_from_video(self, video_id: int, limit: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        published_at = str(video.get("published_at") or "").strip()
        try:
            previous_time = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
        except ValueError:
            previous_time = None
        if previous_time is not None:
            refined_time = refine_created_at_from_title(
                previous_time,
                str(video.get("title") or ""),
            )
            if refined_time.date() != previous_time.date():
                self.storage.update_video_published_at_detected(
                    video_id,
                    refined_time.isoformat(),
                    source="douyin-title-date",
                )
                video = self.storage.get_video(video_id) or video
        status = self.status(video_id)
        aweme_id = str(status.get("aweme_id") or "")
        if not aweme_id:
            raise ValueError("video is not bound to a Douyin work")
        url = canonical_video_url(aweme_id)
        page = self._ensure_page()
        captured: dict[str, dict[str, Any]] = {}
        account_author = str(video.get("author") or "模型先生")
        root_seed_url = ""
        reply_seed_url = ""
        direct_reply_attempts = 0
        direct_reply_completed = 0
        direct_errors = 0
        attempted_reply_roots: set[str] = set()
        completed_reply_roots: set[str] = set()
        direct_pagination_complete = False
        signer_available = False

        def on_response(response: Any) -> None:
            nonlocal root_seed_url, reply_seed_url
            response_url = str(response.url or "")
            if "comment/list" not in response_url:
                return
            if "/comment/list/reply/" in response_url:
                reply_seed_url = reply_seed_url or response_url
            elif "/comment/list/" in response_url:
                root_seed_url = root_seed_url or response_url
            try:
                payload = response.json()
            except Exception:
                return
            for item in extract_comments_from_payload(
                payload,
                response_url=response_url,
                account_author=account_author,
            ):
                captured[str(item["source_comment_id"])] = item

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            try:
                page.wait_for_function(
                    """
                    () => window.byted_acrawler &&
                      typeof window.byted_acrawler.frontierSign === 'function'
                    """,
                    timeout=10_000,
                )
                signer_available = True
            except Exception:
                # The visible-page fallback can still capture comments even
                # when Douyin temporarily withholds the request signer.
                direct_errors += 1
            profile_url = self._discover_profile_url(page, account_author)
            if profile_url:
                self._save_account_config(
                    account_author,
                    {"profile_url": profile_url, "updated_at": now_iso()},
                )

            if signer_available and root_seed_url:
                try:
                    direct_pagination_complete = self._collect_signed_pages(
                        page,
                        root_seed_url,
                        captured,
                        account_author=account_author,
                        limit=limit,
                    )
                except Exception:
                    direct_pagination_complete = False

                reply_template = reply_seed_url or self._reply_url_template(
                    root_seed_url,
                    aweme_id,
                )
                roots = [
                    item
                    for item in list(captured.values())
                    if not item.get("parent_source_comment_id")
                    and int(item.get("reply_count") or 0) > 0
                ]
                for root in roots:
                    if len(captured) >= limit:
                        break
                    root_id = str(root.get("source_comment_id") or "")
                    attempted_reply_roots.add(root_id)
                    try:
                        reply_completed = self._collect_signed_pages(
                            page,
                            reply_template,
                            captured,
                            account_author=account_author,
                            limit=limit,
                            parent_comment_id=root_id,
                        )
                        if reply_completed:
                            completed_reply_roots.add(root_id)
                    except Exception:
                        direct_errors += 1
                        continue

            stable_rounds = 0
            no_expand_rounds = 0
            last_count = 0
            max_rounds = (
                60
                if direct_pagination_complete
                else min(320, max(60, limit // 8 + 24))
            )
            for _ in range(max_rounds):
                expanded = self._expand_reply_buttons(page)
                self._scroll_comments(page)
                page.wait_for_timeout(600)
                current_count = len(captured)
                if current_count >= limit:
                    break
                if current_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = current_count
                no_expand_rounds = 0 if expanded else no_expand_rounds + 1
                if stable_rounds >= 30 and no_expand_rounds >= 10:
                    break

            # The root comment request is often created only after the first
            # visible-page scroll. Retry signed root pagination here; without
            # this pass, large threads stopped after a few hundred comments.
            if signer_available and root_seed_url and len(captured) < limit:
                try:
                    direct_pagination_complete = self._collect_signed_pages(
                        page,
                        root_seed_url,
                        captured,
                        account_author=account_author,
                        limit=limit,
                    )
                except Exception:
                    direct_errors += 1

            if signer_available and root_seed_url and len(captured) < limit:
                reply_template = reply_seed_url or self._reply_url_template(
                    root_seed_url,
                    aweme_id,
                )
                completeness = comment_reply_completeness(list(captured.values()))
                incomplete_root_ids = {
                    str(item.get("source_comment_id") or "")
                    for item in completeness["incomplete_groups"]
                }
                all_roots = [
                    item
                    for item in list(captured.values())
                    if str(item.get("source_comment_id") or "")
                    in incomplete_root_ids
                ]
                for root in all_roots:
                    if len(captured) >= limit:
                        break
                    root_id = str(root.get("source_comment_id") or "")
                    attempted_reply_roots.add(root_id)
                    try:
                        reply_completed = self._collect_signed_pages(
                            page,
                            reply_template,
                            captured,
                            account_author=account_author,
                            limit=limit,
                            parent_comment_id=root_id,
                        )
                        if reply_completed:
                            completed_reply_roots.add(root_id)
                    except Exception:
                        direct_errors += 1
                        continue
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        direct_reply_attempts = len(attempted_reply_roots)
        direct_reply_completed = len(completed_reply_roots)
        items = classify_comment_sections(
            list(captured.values())[:limit],
            account_author,
        )
        if not items:
            body = ""
            try:
                body = page.locator("body").inner_text(timeout=3_000)
            except Exception:
                pass
            needs_login = "登录" in body or "扫码" in body
            return {
                "matched": True,
                "needs_login": needs_login,
                "browser_open": True,
                "aweme_id": aweme_id,
                "url": url,
                "message": (
                    "专用Chrome已打开，请完成抖音登录后再次点击“继续采集”。"
                    if needs_login
                    else "页面已打开，但尚未读取到评论。请确认评论区可见后再次采集。"
                ),
            }

        completeness = comment_reply_completeness(items)
        preview_id = os.urandom(16).hex()
        SYNC_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "preview_id": preview_id,
            "video_id": video_id,
            "aweme_id": aweme_id,
            "url": url,
            "created_at": now_iso(),
            "items": items,
            "collection_diagnostics": {
                "direct_root_complete": direct_pagination_complete,
                "direct_reply_attempts": direct_reply_attempts,
                "direct_reply_completed": direct_reply_completed,
                "direct_errors": direct_errors,
                **completeness,
            },
        }
        (SYNC_PREVIEW_DIR / f"{preview_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        interaction_count = sum(
            1
            for item in items
            if item.get("section") == "author_interaction"
            and not item.get("parent_source_comment_id")
        )
        fan_count = sum(
            1
            for item in items
            if item.get("section") == "fan_comment"
            and not item.get("parent_source_comment_id")
        )
        model_comments = sum(
            1 for item in items if str(item.get("author") or "") == account_author
        )
        model_replies = sum(
            1
            for item in items
            if str(item.get("author") or "") == account_author
            and bool(item.get("parent_source_comment_id"))
        )
        return {
            "matched": True,
            "needs_login": False,
            "preview_id": preview_id,
            "requires_confirmation": True,
            "aweme_id": aweme_id,
            "url": url,
            "items": items,
            "summary": {
                "captured": len(items),
                "author_interactions": interaction_count,
                "fan_comments": fan_count,
                "model_comments": model_comments,
                "model_replies": model_replies,
                "remote_total": max(
                    (int(item.get("remote_total") or 0) for item in items),
                    default=0,
                ),
                "direct_root_complete": direct_pagination_complete,
                "direct_reply_attempts": direct_reply_attempts,
                "direct_reply_completed": direct_reply_completed,
                "direct_errors": direct_errors,
                **{
                    key: value
                    for key, value in completeness.items()
                    if key != "incomplete_groups"
                },
            },
            "message": "评论采集完成，确认后才会写入数据库。",
        }

    @staticmethod
    def _reply_url_template(root_url: str, aweme_id: str) -> str:
        parts = urlparse(root_url)
        query = parse_qs(parts.query, keep_blank_values=True)
        query.pop("aweme_id", None)
        query["item_id"] = [aweme_id]
        query["comment_id"] = [""]
        query["cut_version"] = ["1"]
        query["item_type"] = ["0"]
        path = parts.path.replace(
            "/comment/list/",
            "/comment/list/reply/",
        )
        return urlunparse(
            parts._replace(path=path, query=urlencode(query, doseq=True))
        )

    @staticmethod
    def _unsigned_page_url(
        seed_url: str,
        *,
        cursor: int,
        count: int,
        parent_comment_id: str = "",
    ) -> str:
        parts = urlparse(seed_url)
        query = parse_qs(parts.query, keep_blank_values=True)
        for key in ("a_bogus", "X-Bogus", "x-bogus"):
            query.pop(key, None)
        query["cursor"] = [str(max(0, int(cursor)))]
        query["count"] = [str(max(1, min(int(count), 50)))]
        if parent_comment_id:
            query["comment_id"] = [parent_comment_id]
        return urlunparse(parts._replace(query=urlencode(query, doseq=True)))

    @staticmethod
    def _fetch_signed_json(page: Any, unsigned_url: str) -> dict[str, Any]:
        result = page.evaluate(
            """
            async (url) => {
              const signer = window.byted_acrawler &&
                window.byted_acrawler.frontierSign;
              if (typeof signer !== 'function') {
                return { ok: false, error: 'Douyin signer is unavailable' };
              }
              const signature = signer({ url });
              const bogus = signature && signature['X-Bogus'];
              if (!bogus) {
                return { ok: false, error: 'Douyin signature failed' };
              }
              const signedUrl = new URL(url);
              signedUrl.searchParams.set('X-Bogus', bogus);
              const response = await fetch(signedUrl.href, {
                credentials: 'include'
              });
              let payload = {};
              try {
                payload = await response.json();
              } catch (error) {
                return {
                  ok: false,
                  status: response.status,
                  error: String(error)
                };
              }
              return {
                ok: response.ok,
                status: response.status,
                payload,
                responseUrl: signedUrl.href
              };
            }
            """,
            unsigned_url,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(
                str((result or {}).get("error") or "Douyin page request failed")
            )
        payload = result.get("payload")
        return payload if isinstance(payload, dict) else {}

    def _collect_signed_pages(
        self,
        page: Any,
        seed_url: str,
        captured: dict[str, dict[str, Any]],
        *,
        account_author: str,
        limit: int,
        parent_comment_id: str = "",
    ) -> bool:
        cursor = 0
        seen_cursors: set[int] = set()
        completed = False
        for _ in range(180):
            if len(captured) >= limit or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            request_url = self._unsigned_page_url(
                seed_url,
                cursor=cursor,
                count=20,
                parent_comment_id=parent_comment_id,
            )
            payload = self._fetch_signed_json(page, request_url)
            extracted = extract_comments_from_payload(
                payload,
                response_url=request_url,
                account_author=account_author,
            )
            for item in extracted:
                captured[str(item["source_comment_id"])] = item

            has_more = bool(payload.get("has_more"))
            if not has_more:
                completed = True
                break
            next_cursor = int(payload.get("cursor") or 0)
            if next_cursor <= cursor:
                next_cursor = cursor + max(1, len(extracted))
            cursor = next_cursor
        return completed

    def _match_from_profile(self, video_id: int, profile_url: str) -> dict[str, Any]:
        detail = self.storage.get_video_detail(video_id) or {}
        video = detail.get("video") or {}
        title_info = detail.get("title_info") or {}
        local_title = str(
            title_info.get("active_title")
            or video.get("title")
            or ""
        )
        local_cover = self._local_cover_image(title_info)
        page = self._ensure_page()
        page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_000)
        for _ in range(8):
            page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 900))")
            page.wait_for_timeout(550)

        links = page.locator('a[href*="/video/"]')
        candidates: list[dict[str, Any]] = []
        total = min(links.count(), 36)
        COMMENT_CARD_SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        for index in range(total):
            link = links.nth(index)
            href = str(link.get_attribute("href") or "")
            aweme_id = extract_aweme_id(href)
            if not aweme_id or any(
                item.get("aweme_id") == aweme_id for item in candidates
            ):
                continue
            text = ""
            try:
                text = link.inner_text(timeout=1_500)
            except Exception:
                pass
            title_score = title_similarity(local_title, text)
            image_score = 0.0
            ocr_title = ""
            try:
                screenshot = link.screenshot(timeout=4_000)
                candidate_image = Image.open(io.BytesIO(screenshot)).convert("RGB")
                if local_cover is not None:
                    image_score = hash_similarity(local_cover, candidate_image)
                if title_score < 0.80:
                    shot_path = COMMENT_CARD_SCREENSHOTS / f"{video_id}_{aweme_id}.png"
                    shot_path.write_bytes(screenshot)
                    payload = ocr_image_payload(shot_path)
                    ocr_title = "".join(
                        str(item.get("text") or "")
                        for item in payload.get("lines") or []
                    )
                    title_score = max(
                        title_score,
                        title_similarity(local_title, ocr_title),
                    )
            except Exception:
                pass
            confidence = round(0.72 * title_score + 0.28 * image_score, 4)
            candidates.append(
                {
                    "aweme_id": aweme_id,
                    "url": canonical_video_url(aweme_id),
                    "visible_text": text[:160],
                    "ocr_title": ocr_title[:100],
                    "title_similarity": round(title_score, 4),
                    "cover_similarity": round(image_score, 4),
                    "confidence": confidence,
                }
            )

        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        best = candidates[0] if candidates else None
        if best and float(best["confidence"]) >= 0.92:
            updated = self.storage.bind_video_remote_source(
                video_id,
                url=str(best["url"]),
                aweme_id=str(best["aweme_id"]),
                match_source="profile-cover-match",
                match_confidence=float(best["confidence"]),
                metadata={"candidate": best, "profile_url": profile_url},
            )
            return {
                "matched": True,
                "requires_selection": False,
                "source": "profile-cover-match",
                "video": updated,
                **best,
            }
        return {
            "matched": False,
            "requires_selection": bool(candidates),
            "candidates": candidates[:5],
            "message": (
                "找到多个相似视频，请人工选择。"
                if candidates
                else "没有从账号主页找到可匹配的视频。"
            ),
        }

    def _ensure_page(self) -> Any:
        if self._context is not None:
            try:
                pages = self._context.pages
                if self._page is None or self._page.is_closed():
                    self._page = pages[0] if pages else self._context.new_page()
                return self._page
            except Exception as exc:
                # Closing the visible Chrome window also closes Playwright's
                # persistent context. Discard the stale handles so the next
                # collection click can transparently start a fresh Chrome.
                if "closed" not in str(exc).casefold():
                    raise
                self._page = None
                self._context = None

        if self._context is None:
            from playwright.sync_api import sync_playwright

            COMMENT_BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(COMMENT_BROWSER_PROFILE),
                executable_path=str(chrome_executable_path()),
                headless=False,
                no_viewport=True,
                args=["--start-maximized"],
            )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        return self._page

    def _close_browser_resources(self) -> None:
        """Release Chromium before yielding the shared browser execution lock."""

        context = self._context
        playwright = self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        error: BaseException | None = None
        if context is not None:
            try:
                context.close()
            except BaseException as exc:
                error = exc
        if playwright is not None:
            try:
                playwright.stop()
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error

    @staticmethod
    def _expand_reply_buttons(page: Any) -> int:
        try:
            return int(
                page.evaluate(
                    """
                    (patterns) => {
                      const matchers = patterns.map((pattern) => new RegExp(pattern));
                      const nodes = [...document.querySelectorAll('button, [role="button"], div, span')];
                      const targets = nodes.filter((node) => {
                        const text = (node.innerText || '').trim();
                        if (!text || text.length > 28) return false;
                        if (!matchers.some((matcher) => matcher.test(text))) return false;
                        const style = getComputedStyle(node);
                        if (style.display === 'none' || style.visibility === 'hidden') return false;
                        const lastClick = Number(node.dataset.mxReplyLastClicked || 0);
                        if (Date.now() - lastClick < 1200) return false;
                        return ![...node.children].some((child) => {
                          const childText = (child.innerText || '').trim();
                          return childText === text;
                        });
                      }).slice(0, 48);
                      targets.forEach((node) => {
                        node.dataset.mxReplyLastClicked = String(Date.now());
                        node.scrollIntoView({ block: 'center', inline: 'nearest' });
                        node.click();
                      });
                      return targets.length;
                    }
                    """,
                    list(REPLY_EXPAND_TEXT_PATTERNS),
                )
                or 0
            )
        except Exception:
            return 0

    @staticmethod
    def _scroll_comments(page: Any) -> None:
        position = page.evaluate(
            """
            () => {
              const all = [...document.querySelectorAll('*')];
              const candidates = all
                .filter((node) => {
                  const style = getComputedStyle(node);
                  const scrollable = /(auto|scroll)/.test(style.overflowY || '');
                  const text = node.innerText || '';
                  const rect = node.getBoundingClientRect();
                  return scrollable &&
                    node.scrollHeight > node.clientHeight + 120 &&
                    rect.height > 240 &&
                    rect.right > window.innerWidth * 0.62 &&
                    (text.includes('\\u8bc4\\u8bba') || text.includes('\\u56de\\u590d'));
                });
              if (candidates.length) {
                const target = candidates
                  .map((node) => {
                    const text = node.innerText || '';
                    const rect = node.getBoundingClientRect();
                    const score =
                      (text.includes('\\u5168\\u90e8\\u8bc4\\u8bba') ? 1000000 : 0) +
                      Math.min(node.scrollHeight, 100000) +
                      Math.max(0, rect.left);
                    return { node, score, rect };
                  })
                  .sort((a, b) => b.score - a.score)[0].node;
                target.scrollTop = Math.min(
                  target.scrollHeight,
                  target.scrollTop + Math.max(700, target.clientHeight * 0.85)
                );
                target.dispatchEvent(new Event('scroll', { bubbles: true }));
                const rect = target.getBoundingClientRect();
                return {
                  x: Math.max(1, Math.min(window.innerWidth - 2, rect.left + rect.width / 2)),
                  y: Math.max(1, Math.min(window.innerHeight - 2, rect.top + rect.height / 2))
                };
              } else {
                window.scrollBy(0, Math.max(window.innerHeight, 900));
                return null;
              }
            }
            """
        )
        if isinstance(position, dict):
            page.mouse.move(
                float(position.get("x") or 0),
                float(position.get("y") or 0),
            )
            page.mouse.wheel(0, 900)

    @staticmethod
    def _discover_profile_url(page: Any, account_author: str) -> str:
        try:
            return str(
                page.evaluate(
                    """
                    (name) => {
                      const links = [...document.querySelectorAll('a[href*="/user/"]')];
                      const match = links.find((link) => {
                        const text = (link.innerText || link.getAttribute('aria-label') || '').trim();
                        return text.includes(name);
                      });
                      return match ? new URL(match.href, location.href).href : '';
                    }
                    """,
                    account_author,
                )
                or ""
            )
        except Exception:
            return ""

    @staticmethod
    def _video_raw(video: dict[str, Any]) -> dict[str, Any]:
        value = video.get("raw_json")
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _local_cover_image(title_info: dict[str, Any]) -> Image.Image | None:
        frame_path = Path(str(title_info.get("frame_path") or ""))
        if not frame_path.is_file():
            return None
        try:
            return Image.open(frame_path).convert("RGB")
        except OSError:
            return None

    def _resolve_share_link(self, link: str) -> str:
        value = str(link or "").strip()
        if not value:
            raise ValueError("抖音链接不能为空")
        if extract_aweme_id(value):
            return value
        if "v.douyin.com" not in value:
            raise ValueError("请输入抖音视频分享链接")
        request = Request(
            value,
            method="HEAD",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with build_opener().open(request, timeout=15) as response:
            return str(response.geturl() or value)

    def _account_config(self, account_author: str) -> dict[str, Any]:
        with self._config_lock:
            config = self._read_config()
            accounts = config.get("accounts") or {}
            value = accounts.get(account_author) or {}
            return dict(value) if isinstance(value, dict) else {}

    def _save_account_config(
        self,
        account_author: str,
        values: dict[str, Any],
    ) -> None:
        with self._config_lock:
            config = self._read_config()
            accounts = config.setdefault("accounts", {})
            current = accounts.setdefault(account_author, {})
            current.update(values)
            SYNC_CONFIG_PATH.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @staticmethod
    def _read_config() -> dict[str, Any]:
        if not SYNC_CONFIG_PATH.is_file():
            return {"accounts": {}}
        try:
            value = json.loads(SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"accounts": {}}
        except json.JSONDecodeError:
            return {"accounts": {}}

    def _submit_browser_job(
        self,
        operation: Callable[[], Any],
        *,
        timeout: int,
    ) -> Any:
        job = BrowserJob(operation)
        self._jobs.put(job)
        result = job.result_queue.get(timeout=timeout)
        if isinstance(result, BaseException):
            raise result
        return result

    def _browser_worker(self) -> None:
        while True:
            job = self._jobs.get()
            if self._execution_lock is None:
                try:
                    result = job.operation()
                except BaseException as exc:
                    result = exc
            else:
                with self._execution_lock:
                    try:
                        result = job.operation()
                    except BaseException as exc:
                        result = exc
                    try:
                        self._close_browser_resources()
                    except BaseException as exc:
                        if not isinstance(result, BaseException):
                            result = exc
            job.result_queue.put(result)
