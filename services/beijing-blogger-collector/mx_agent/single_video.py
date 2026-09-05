"""Validate and expand one public Douyin video link without starting Chromium."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

import requests


class VideoLinkError(ValueError):
    pass


def normalize_video_link(text: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > 4096:
        raise VideoLinkError("请粘贴一条视频的抖音链接或分享文案（最多 4096 字）。")
    links = re.findall(r'https?://[^\s<>"，。！？（）()\[\]{}]+', text.replace("\\_", "_"))
    normalized = set()
    for link in links:
        try:
            parsed = urlsplit(link.rstrip(".,;；！!？?）"))
            if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
                raise ValueError
            host = (parsed.hostname or "").lower()
            path = parsed.path.rstrip("/")
            if host == "v.douyin.com" and re.fullmatch(r"/[A-Za-z0-9_-]{1,128}", path):
                normalized.add(f"https://v.douyin.com{path}/")
                continue
            video_id = ""
            if host in {"douyin.com", "www.douyin.com"}:
                match = re.fullmatch(r"/video/(\d{15,22})", path)
                video_id = match.group(1) if match else parse_qs(parsed.query).get("modal_id", [""])[0]
            elif host == "www.iesdouyin.com":
                match = re.fullmatch(r"/share/video/(\d{15,22})", path)
                video_id = match.group(1) if match else ""
            if not re.fullmatch(r"\d{15,22}", video_id):
                raise ValueError
            normalized.add(f"https://www.douyin.com/video/{video_id}")
        except ValueError as exc:
            raise VideoLinkError("请填写抖音视频链接，不能填写博主主页、图文或其他网站。") from exc
    if len(normalized) != 1:
        raise VideoLinkError("每次只能指定一条视频，请粘贴它的抖音链接或完整分享文案。")
    return normalized.pop()


def resolve_video_link(text: str) -> dict[str, str]:
    url = normalize_video_link(text)
    for _ in range(5):
        if url.startswith("https://www.douyin.com/video/"):
            return {"video_url": url, "video_id": url.rsplit("/", 1)[1]}
        try:
            with requests.get(
                url, headers={"User-Agent": "Mozilla/5.0"}, stream=True,
                allow_redirects=False, timeout=(5, 10),
            ) as response:
                if response.status_code not in {301, 302, 303, 307, 308}:
                    raise VideoLinkError("短链接暂时无法识别，请检查作品是否公开，或粘贴浏览器中的完整视频地址。")
                location = response.headers.get("Location", "")
                if not location:
                    raise VideoLinkError("短链接没有返回视频地址，请重新复制分享链接。")
                # Validate every redirect before making another network request.
                url = normalize_video_link(urljoin(url, location))
        except requests.RequestException as exc:
            raise VideoLinkError("连接抖音超时或失败，请稍后重试，或粘贴完整视频地址。") from exc
    raise VideoLinkError("短链接跳转次数过多，请粘贴完整视频地址。")
