from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests

from .douyin_core import (
    CDPConnection,
    CHROME_PATH,
    DownloadCancelled,
    ParseError,
    chromium_runtime_flags,
    close_stale_profile_browser,
)


PROFILE_WORK_RE = re.compile(r"/(video|note)/(\d{15,22})(?:[/?#]|$)")
METRIC_ONLY_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:万|亿)?|置顶)$")
MIN_STABLE_PROFILE_CARDS = 1


@dataclass(frozen=True)
class ProfileVideo:
    video_id: str
    url: str
    title: str
    created_at: datetime
    work_type: str = "video"


class ProfileScanError(RuntimeError):
    pass


def video_created_at(video_id: str) -> datetime:
    """Douyin aweme IDs store their Unix timestamp in the upper 32 bits."""
    try:
        timestamp = int(video_id) >> 32
        if 1_500_000_000 <= timestamp <= 2_500_000_000:
            return datetime.fromtimestamp(timestamp)
    except (ValueError, OverflowError, OSError):
        pass
    return datetime.fromtimestamp(0)


TITLE_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[年./-]?([01]\d)[月./-]?([0-3]\d)日?(?!\d)"
)


def refine_created_at_from_title(created_at: datetime, title: str) -> datetime:
    """Prefer an explicit public-title date over the aweme-ID draft time."""
    match = TITLE_DATE_RE.search(str(title or ""))
    if not match:
        return created_at
    try:
        candidate = created_at.replace(
            year=int(match.group(1)),
            month=int(match.group(2)),
            day=int(match.group(3)),
        )
    except ValueError:
        return created_at
    if abs(candidate.date() - created_at.date()) > timedelta(days=31):
        return created_at
    if candidate.date() > datetime.now().date() + timedelta(days=1):
        return created_at
    return candidate


def launch_dedicated_login_browser(
    profile_url: str,
    profile_dir: Path,
    chrome_path: Path = CHROME_PATH,
) -> subprocess.Popen:
    """Open a visible Chrome using only this application's persistent profile."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            str(chrome_path),
            f"--user-data-dir={profile_dir}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            profile_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class ProfileScanner:
    def __init__(
        self,
        profile_dir: Path,
        log: Callable[[str], None] | None = None,
        cancel_event=None,
        chrome_path: Path = CHROME_PATH,
    ):
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.log = log or (lambda _: None)
        self.cancel_event = cancel_event
        self.chrome_path = Path(chrome_path)
        self.chrome: subprocess.Popen | None = None
        self.cdp: CDPConnection | None = None

    def _check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelled("任务已取消。")

    def _start(self):
        if not self.chrome_path.exists():
            raise ProfileScanError(f"未找到 Chrome：{self.chrome_path}")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        active_port = self.profile_dir / "DevToolsActivePort"
        close_stale_profile_browser(self.profile_dir, self.log)
        try:
            active_port.unlink(missing_ok=True)
        except OSError:
            pass

        args = [
            str(self.chrome_path),
            "--headless=new",
            "--remote-debugging-port=0",
            "--remote-allow-origins=http://localhost",
            f"--user-data-dir={self.profile_dir}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-notifications",
            "--disable-features=Translate,OptimizationHints,MediaRouter",
            "--window-size=1365,900",
            "about:blank",
        ]
        args[1:1] = chromium_runtime_flags()
        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            popen_kwargs.update(
                startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self.chrome = subprocess.Popen(args, **popen_kwargs)

        deadline = time.monotonic() + 15
        port = None
        while time.monotonic() < deadline:
            self._check_cancel()
            if self.chrome.poll() is not None:
                raise ProfileScanError(
                    "专用浏览器配置正在被占用。请先关闭“博主智能体专用浏览器”，再重新检查。"
                )
            if active_port.exists():
                try:
                    port = int(active_port.read_text(encoding="utf-8").splitlines()[0])
                    break
                except (OSError, ValueError, IndexError):
                    pass
            time.sleep(0.1)
        if port is None:
            raise ProfileScanError("等待专用 Chrome 调试端口超时。")

        deadline = time.monotonic() + 8
        last_error = None
        while time.monotonic() < deadline:
            try:
                targets = requests.get(
                    f"http://127.0.0.1:{port}/json/list", timeout=1
                ).json()
                page = next(row for row in targets if row.get("type") == "page")
                self.cdp = CDPConnection(page["webSocketDebuggerUrl"])
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.15)
        raise ProfileScanError(f"连接专用 Chrome 失败：{last_error}")

    def _evaluate(self, expression: str):
        if not self.cdp:
            raise ProfileScanError("专用 Chrome 尚未连接。")
        result = self.cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    def scan(
        self,
        profile_url: str,
        timeout: float = 90,
        limit: int = 500,
    ) -> tuple[str, list[ProfileVideo]]:
        self._start()
        assert self.cdp
        self.cdp.call("Network.enable")
        self.cdp.call("Page.enable")
        self.cdp.call("Runtime.enable")
        self.cdp.call("Page.navigate", {"url": profile_url})
        self.log("正在刷新博主主页…")

        deadline = time.monotonic() + timeout
        last_snapshot: dict = {}
        reloaded = False
        all_by_id: dict[str, ProfileVideo] = {}
        stable_rounds = 0
        previous_count = 0
        creator = "新博主"
        while time.monotonic() < deadline:
            self._check_cancel()
            time.sleep(1)
            try:
                raw = self._evaluate(
                    """JSON.stringify({
                      url: location.href,
                      title: document.title || '',
                      text: (document.body && document.body.innerText || '').slice(0, 1600),
                      cards: [...document.querySelectorAll('a[href*="/video/"],a[href*="/note/"]')]
                        .map(a => ({
                          href: a.href,
                          classCount: a.classList.length,
                          hasImage: !!a.querySelector('img'),
                          inListItem: !!a.closest('li'),
                          title: (a.innerText || a.getAttribute('aria-label') ||
                                  a.getAttribute('title') ||
                                  a.parentElement?.innerText || '').trim().slice(0, 240)
                        }))
                    })"""
                )
                last_snapshot = json.loads(raw or "{}")
            except Exception:
                continue

            body_text = last_snapshot.get("text", "")
            cards = last_snapshot.get("cards", [])
            if "服务异常，重新刷新拉取数据" in body_text:
                if not reloaded and time.monotonic() + 8 < deadline:
                    reloaded = True
                    self.log("主页返回服务异常，正在自动刷新一次…")
                    self.cdp.call("Page.reload", {"ignoreCache": True})
                    time.sleep(3)
                    continue
                # Anonymous crawler fallback contains unrelated Baidu links; never
                # treat those as the monitored creator's works.
                continue

            by_id: dict[str, ProfileVideo] = {}
            for card in cards:
                href = card.get("href", "")
                # Douyin renders a "热门" SEO block before the creator's work
                # grid has loaded.  Those unrelated links carry
                # source=Baiduspider; accepting them would report another
                # creator's videos during a fast scan.
                source = parse_qs(urlparse(href).query).get("source", [""])[0]
                if source.lower().startswith("baiduspider"):
                    continue
                # Real profile-grid works are thumbnail cards in list items
                # with multiple presentation classes.  Search/SEO/recommend
                # links are plain text anchors and must never enter the feed.
                if (
                    not card.get("hasImage")
                    or not card.get("inListItem")
                    or int(card.get("classCount") or 0) < 2
                ):
                    continue
                match = PROFILE_WORK_RE.search(href)
                if not match:
                    continue
                path_type = match.group(1).lower()
                video_id = match.group(2)
                title = " ".join((card.get("title") or "").split())
                # The profile card usually exposes only its like/play count
                # (for example "1.1万") as innerText.  That is not a caption;
                # leave a fallback here so the video page resolver can supply
                # the real work title during download.
                if METRIC_ONLY_RE.fullmatch(title.replace(" ", "")):
                    title = ""
                by_id[video_id] = ProfileVideo(
                    video_id=video_id,
                    url=f"https://www.douyin.com/{path_type}/{video_id}",
                    title=title or (
                        f"抖音图文_{video_id}"
                        if path_type == "note"
                        else f"抖音作品_{video_id}"
                    ),
                    created_at=video_created_at(video_id),
                    work_type="image" if path_type == "note" else "video",
                )
            # A single unrelated recommendation can appear during hydration.
            # This dedicated monitor targets a creator with hundreds of works,
            # so wait for a small grid before accepting the page as stable.
            if len(by_id) >= MIN_STABLE_PROFILE_CARDS:
                all_by_id.update(by_id)
                creator = (last_snapshot.get("title") or "新博主").split("的抖音")[0]
                if len(all_by_id) == previous_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    previous_count = len(all_by_id)
                if len(all_by_id) < max(1, int(limit)) and stable_rounds < 3:
                    try:
                        self._evaluate(
                            "window.scrollTo(0, document.body.scrollHeight); true"
                        )
                    except Exception:
                        pass
                    continue
                videos = sorted(
                    all_by_id.values(),
                    key=lambda row: int(row.video_id),
                    reverse=True,
                )[: max(1, int(limit))]
                image_count = sum(1 for item in videos if item.work_type == "image")
                self.log(
                    f"主页扫描成功：发现 {len(videos)} 个当前可见作品"
                    f"（视频 {len(videos) - image_count}，图文 {image_count}）"
                )
                return creator, videos

        if all_by_id:
            videos = sorted(
                all_by_id.values(),
                key=lambda row: int(row.video_id),
                reverse=True,
            )[: max(1, int(limit))]
            image_count = sum(1 for item in videos if item.work_type == "image")
            self.log(
                f"主页扫描结束：发现 {len(videos)} 个作品"
                f"（视频 {len(videos) - image_count}，图文 {image_count}）"
            )
            return creator, videos

        text = last_snapshot.get("text", "")
        if "登录" in text or "服务异常" in text:
            raise ProfileScanError(
                "公开主页未返回真实作品列表。请点击“登录专用浏览器”，"
                "在独立浏览器中登录抖音后将其完全关闭，再重新检查。"
            )
        raise ProfileScanError("主页扫描超时，没有取得真实作品列表。")

    def close(self):
        if self.cdp:
            self.cdp.close()
            self.cdp = None
        if self.chrome and self.chrome.poll() is None:
            try:
                self.chrome.terminate()
                self.chrome.wait(timeout=3)
            except Exception:
                try:
                    self.chrome.kill()
                except Exception:
                    pass
        self.chrome = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
