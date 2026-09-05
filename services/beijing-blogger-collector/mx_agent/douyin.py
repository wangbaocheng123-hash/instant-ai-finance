from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class DouyinAPIError(RuntimeError):
    pass


def iso_from_timestamp(value: Any) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def comment_signal(text: str) -> dict[str, str]:
    lowered = text.lower()
    negative_words = ["假", "骗", "割韭菜", "没用", "垃圾", "失望", "坑", "拉黑"]
    positive_words = ["有用", "感谢", "厉害", "学到了", "支持", "清楚", "靠谱"]
    risk_words = ["投诉", "举报", "诈骗", "侵权", "删了", "退钱", "违规"]
    if any(word in text or word in lowered for word in risk_words):
        risk_level = "high"
    elif any(word in text or word in lowered for word in negative_words):
        risk_level = "watch"
    else:
        risk_level = "normal"
    if any(word in text or word in lowered for word in positive_words):
        sentiment = "positive"
    elif risk_level in {"high", "watch"}:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    return {"sentiment": sentiment, "risk_level": risk_level}


class DouyinOfficialSource:
    """Official Douyin Open Platform client.

    It only uses documented OAuth OpenAPI endpoints. Credentials and scopes must
    come from the user's own Douyin Open Platform application.
    """

    name = "douyin-official"

    def __init__(
        self,
        access_token: str | None = None,
        open_id: str | None = None,
        base_url: str | None = None,
        video_endpoint: str | None = None,
        comment_endpoint: str | None = None,
        count: int | None = None,
    ):
        self.access_token = access_token or os.getenv("DOUYIN_ACCESS_TOKEN")
        self.open_id = open_id or os.getenv("DOUYIN_OPEN_ID")
        self.base_url = (base_url or os.getenv("DOUYIN_BASE_URL") or "https://open.douyin.com").rstrip("/")
        self.video_endpoint = video_endpoint or os.getenv("DOUYIN_VIDEO_ENDPOINT") or "/video/list/"
        self.comment_endpoint = comment_endpoint or os.getenv("DOUYIN_COMMENT_ENDPOINT") or "/item/comment/list/"
        self.count = int(count or os.getenv("DOUYIN_PAGE_SIZE") or "20")

    def is_configured(self) -> bool:
        return bool(self.access_token)

    def status(self) -> dict[str, Any]:
        missing = []
        if not self.access_token:
            missing.append("DOUYIN_ACCESS_TOKEN")
        return {
            "configured": self.is_configured(),
            "missing": missing,
            "base_url": self.base_url,
            "video_endpoint": self.video_endpoint,
            "comment_endpoint": self.comment_endpoint,
            "requires": [
                "video.list scope",
                "item.comment 或 video.comment scope",
                "当前博主账号授权",
            ],
        }

    def fetch_videos(self, cursor: int | str | None = None) -> dict[str, Any]:
        if not self.is_configured():
            raise DouyinAPIError("未配置 DOUYIN_ACCESS_TOKEN，无法调用抖音官方视频列表。")
        params: dict[str, Any] = {"cursor": cursor or 0, "count": self.count}
        if self.open_id:
            params["open_id"] = self.open_id
        payload = self._get(self.video_endpoint, params)
        data = payload.get("data", {})
        self._raise_for_api_error(payload)
        items = data.get("list") or data.get("videos") or []
        return {
            "items": [normalize_official_video(item) for item in items],
            "raw_items": items,
            "cursor": data.get("cursor"),
            "has_more": bool(data.get("has_more")),
            "raw": payload,
        }

    def fetch_latest(self) -> list[dict[str, Any]]:
        return self.fetch_videos(cursor=0)["items"]

    def fetch_comments(self, item_id: str, cursor: int | str | None = None) -> dict[str, Any]:
        if not self.is_configured():
            raise DouyinAPIError("未配置 DOUYIN_ACCESS_TOKEN，无法调用抖音官方评论列表。")
        if not item_id:
            return {"items": [], "cursor": cursor or 0, "has_more": False, "raw": {}}
        params: dict[str, Any] = {"item_id": item_id, "cursor": cursor or 0, "count": self.count}
        if self.open_id:
            params["open_id"] = self.open_id
        payload = self._get(self.comment_endpoint, params)
        data = payload.get("data", {})
        self._raise_for_api_error(payload)
        items = data.get("list") or data.get("comments") or []
        return {
            "items": items,
            "cursor": data.get("cursor"),
            "has_more": bool(data.get("has_more")),
            "raw": payload,
        }

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Content-Type": "application/json",
                "access-token": self.access_token or "",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise DouyinAPIError(f"抖音官方接口调用失败: {exc}") from exc

    def _raise_for_api_error(self, payload: dict[str, Any]) -> None:
        data = payload.get("data") or {}
        extra = payload.get("extra") or {}
        error_code = data.get("error_code", extra.get("error_code", 0))
        if error_code not in (0, "0", None):
            description = data.get("description") or extra.get("description") or "unknown error"
            sub_description = extra.get("sub_description") or ""
            raise DouyinAPIError(f"抖音官方接口返回错误 {error_code}: {description} {sub_description}".strip())


def normalize_official_video(item: dict[str, Any]) -> dict[str, Any]:
    source_video_id = item.get("item_id") or item.get("video_id") or _stable_id(repr(item))
    statistics = item.get("statistics") or {}
    title = item.get("title") or item.get("desc") or "抖音视频"
    return {
        "source": "douyin-official",
        "source_video_id": str(source_video_id),
        "author": item.get("nickname") or item.get("author") or "模型先生",
        "title": title,
        "description": item.get("description") or item.get("desc") or "",
        "url": item.get("share_url") or item.get("url") or "",
        "cover_url": item.get("cover") or item.get("cover_url") or "",
        "published_at": iso_from_timestamp(item.get("create_time")),
        "raw_json": {
            **item,
            "statistics": statistics,
            "local_archive_note": "官方视频列表通常只返回元数据和分享链接；原视频文件需通过授权媒体能力或手动上传归档。",
        },
    }


def normalize_official_comment(video_id: int, item: dict[str, Any]) -> dict[str, Any]:
    text = item.get("content") or item.get("text") or ""
    signal = comment_signal(text)
    source_comment_id = item.get("comment_id") or _stable_id(repr(item))
    return {
        "video_id": video_id,
        "source": "douyin-official",
        "source_comment_id": str(source_comment_id),
        "author": item.get("nickname") or item.get("comment_user_id") or "抖音用户",
        "text": text,
        "like_count": item.get("digg_count", item.get("like_count", 0)),
        "reply_count": item.get("reply_comment_total", item.get("reply_count", 0)),
        "sentiment": signal["sentiment"],
        "risk_level": signal["risk_level"],
        "published_at": iso_from_timestamp(item.get("create_time")),
        "raw_json": item,
    }


def normalize_manual_video(payload: dict[str, Any]) -> dict[str, Any]:
    url = (payload.get("url") or "").strip()
    title = (payload.get("title") or "未命名视频").strip()
    source_video_id = (
        payload.get("source_video_id")
        or payload.get("item_id")
        or _stable_id(url or title + datetime.now(UTC).isoformat())
    )
    return {
        "source": payload.get("source", "manual"),
        "source_video_id": str(source_video_id),
        "author": payload.get("author", "模型先生"),
        "title": title,
        "description": payload.get("description", ""),
        "url": url,
        "cover_url": payload.get("cover_url", ""),
        "published_at": payload.get("published_at"),
        "raw_json": payload,
    }


def normalize_douyin_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") or payload.get("event_type") or "douyin_webhook"
    item_id = payload.get("item_id") or payload.get("video_id") or payload.get("data", {}).get("item_id")
    title = payload.get("title") or payload.get("data", {}).get("title") or "抖音新视频"
    return {
        "source": "douyin-webhook",
        "source_video_id": str(item_id or _stable_id(repr(payload))),
        "author": payload.get("author", "模型先生"),
        "title": title,
        "description": f"Webhook event: {event}",
        "url": payload.get("share_url") or payload.get("url") or "",
        "cover_url": payload.get("cover_url") or "",
        "published_at": payload.get("create_time") or payload.get("published_at"),
        "raw_json": payload,
    }


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
