from __future__ import annotations

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
import websocket


def _default_chrome_path() -> Path:
    configured = os.environ.get("MODEL_DOWNLOADER_CHROME")
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    discovered = next(
        (
            path
            for command in (
                "google-chrome-stable",
                "google-chrome",
                "chromium",
                "chromium-browser",
            )
            if (path := shutil.which(command))
        ),
        None,
    )
    return Path(discovered or "/usr/bin/google-chrome-stable")


CHROME_PATH = _default_chrome_path()
LINUX_CHROMIUM_RUNTIME_FLAGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-crashpad-for-testing",
)


def chromium_runtime_flags(platform_name: str | None = None) -> list[str]:
    """Return Linux server flags shared by every Chromium launch path."""
    return (
        []
        if (platform_name or os.name) == "nt"
        else list(LINUX_CHROMIUM_RUNTIME_FLAGS)
    )


SHORT_LINK_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/?", re.I)
ANY_DOUYIN_RE = re.compile(
    r"https?://(?:(?:www\.)?douyin\.com/(?:video/\d+|[A-Za-z0-9_?&=./%-]+)"
    r"|www\.iesdouyin\.com/share/video/\d+/?[A-Za-z0-9_?&=./%-]*)",
    re.I,
)
VIDEO_ID_RE = re.compile(r"/(?:video|note)/(\d{15,22})(?:[/?#]|$)", re.I)
IES_SHARE_ID_RE = re.compile(r"/share/video/(\d{15,22})(?:[/?#]|$)", re.I)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DECORATION_HOST_PARTS = (
    "douyinstatic.com",
    "douyinpic.com",
    "byteimg.com",
    "pstatp.com",
    "ibytedtos.com",
)


class DownloadCancelled(Exception):
    pass


class ParseError(RuntimeError):
    pass


def extract_douyin_url(text: str) -> str:
    """Extract a supported Douyin URL from an entire share message."""
    match = SHORT_LINK_RE.search(text or "")
    if match:
        return match.group(0)
    match = ANY_DOUYIN_RE.search(text or "")
    if match:
        return match.group(0).rstrip("，。！？!?,.;；）)]}")
    raise ValueError("未找到有效的抖音分享链接，请粘贴完整分享文案或链接。")


def sanitize_filename(value: str, fallback: str = "抖音视频", max_length: int = 120) -> str:
    value = INVALID_FILENAME_RE.sub("_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    if not value:
        value = fallback
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if value.upper() in reserved:
        value = f"_{value}"
    return value[:max_length].rstrip(" .")


def canonical_public_video_url(url: str) -> tuple[str, str | None]:
    """Convert the new empty iesdouyin share landing page to a playable public page."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "www.iesdouyin.com" or host.endswith(".iesdouyin.com"):
        match = IES_SHARE_ID_RE.search(parsed.path)
        if match:
            video_id = match.group(1)
            return f"https://www.douyin.com/video/{video_id}", video_id
    return url, None


def human_size(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def inspect_mp4(path: Path) -> dict[str, object]:
    """Inspect MP4 integrity, duration and audio/video tracks."""
    path = Path(path)
    size = path.stat().st_size
    if size < 32:
        raise ValueError("文件过小，不是有效 MP4。")
    with path.open("rb") as handle:
        head = handle.read(min(size, 8 * 1024 * 1024))
        tail = b""
        if size > len(head):
            handle.seek(max(0, size - 8 * 1024 * 1024))
            tail = handle.read()
    data = head + tail
    if b"ftyp" not in head[:64]:
        raise ValueError("缺少 MP4 ftyp 文件头。")
    duration = None
    pos = data.find(b"mvhd")
    if pos >= 0 and pos + 24 < len(data):
        version = data[pos + 4]
        try:
            if version == 1:
                timescale = int.from_bytes(data[pos + 24:pos + 28], "big")
                duration_units = int.from_bytes(data[pos + 28:pos + 36], "big")
            else:
                timescale = int.from_bytes(data[pos + 16:pos + 20], "big")
                duration_units = int.from_bytes(data[pos + 20:pos + 24], "big")
            if timescale:
                duration = duration_units / timescale
        except (ValueError, IndexError):
            duration = None
    has_video = b"vide" in data
    has_audio = b"soun" in data
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            completed = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            probe = json.loads(completed.stdout or "{}")
            stream_types = {
                str(stream.get("codec_type") or "")
                for stream in probe.get("streams", [])
            }
            has_video = "video" in stream_types
            has_audio = "audio" in stream_types
            probe_duration = probe.get("format", {}).get("duration")
            if probe_duration not in (None, "N/A"):
                duration = float(probe_duration)
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
        ):
            pass
    return {
        "size": size,
        "duration_seconds": duration,
        "has_moov": b"moov" in data,
        "has_mdat": b"mdat" in data,
        "has_video": has_video,
        "has_audio": has_audio,
    }


@dataclass
class VideoCandidate:
    url: str
    mime_type: str
    media_kind: str = "unknown"
    response_headers: dict[str, str] = field(default_factory=dict)
    encoded_length: int = 0
    probed_length: int = 0
    content_range_total: int = 0

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").lower()

    @property
    def score(self) -> tuple[int, int, int]:
        trusted = int("douyinvod.com" in self.host or "douyinvod" in self.host)
        size = max(self.probed_length, self.content_range_total, self.encoded_length)
        bitrate_hint = 0
        for pattern in (r"(?:bitrate|br)[=_/-]?(\d{4,9})", r"video_(\d{4,9})"):
            m = re.search(pattern, self.url, re.I)
            if m:
                bitrate_hint = max(bitrate_hint, int(m.group(1)))
        return trusted, size, bitrate_hint


@dataclass
class ParseResult:
    share_url: str
    final_page_url: str
    video_id: str
    title: str
    video_url: str
    content_length: int
    referer: str
    user_agent: str
    cookies: dict[str, str]
    candidate_count: int
    rejected_count: int
    audio_url: str = ""
    audio_content_length: int = 0


@dataclass
class ImageParseResult:
    share_url: str
    final_page_url: str
    work_id: str
    title: str
    image_urls: list[str]
    referer: str
    user_agent: str
    cookies: dict[str, str]


class CDPConnection:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=2, origin="http://localhost")
        self.next_id = 0
        self.pending: dict[int, queue.Queue] = {}
        self.events: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read_loop, name="CDPReader", daemon=True)
        self.reader.start()

    def _read_loop(self):
        while not self.closed.is_set():
            try:
                raw = self.ws.recv()
                if not raw:
                    break
                message = json.loads(raw)
                if "id" in message:
                    waiter = self.pending.get(message["id"])
                    if waiter:
                        waiter.put(message)
                elif "method" in message:
                    self.events.put(message)
            except (websocket.WebSocketTimeoutException, TimeoutError):
                continue
            except Exception:
                break
        self.closed.set()

    def call(self, method: str, params: dict | None = None, timeout: float = 8) -> dict:
        self.next_id += 1
        call_id = self.next_id
        waiter: queue.Queue = queue.Queue(maxsize=1)
        self.pending[call_id] = waiter
        try:
            self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
            message = waiter.get(timeout=timeout)
            if "error" in message:
                raise RuntimeError(f"CDP {method}: {message['error'].get('message', '未知错误')}")
            return message.get("result", {})
        finally:
            self.pending.pop(call_id, None)

    def close(self):
        self.closed.set()
        try:
            self.ws.close()
        except Exception:
            pass


def close_stale_profile_browser(
    profile_dir: Path,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Close a leaked headless Chrome that still owns this dedicated profile."""
    active_port = Path(profile_dir) / "DevToolsActivePort"
    try:
        lines = active_port.read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
        expected_path = lines[1].strip() if len(lines) > 1 else ""
    except (OSError, ValueError, IndexError):
        return False
    try:
        version = requests.get(
            f"http://127.0.0.1:{port}/json/version",
            timeout=1,
        ).json()
        websocket_url = str(version.get("webSocketDebuggerUrl") or "")
    except Exception:
        return False
    if not websocket_url or (
        expected_path and urlparse(websocket_url).path != expected_path
    ):
        return False

    reporter = log or (lambda _message: None)
    reporter("发现上次未正常退出的专用后台浏览器，正在自动清理后重试。")
    connection: CDPConnection | None = None
    try:
        connection = CDPConnection(websocket_url)
        try:
            connection.call("Browser.close", timeout=2)
        except Exception:
            # Chrome commonly closes the websocket before acknowledging the
            # command, which still means the requested cleanup succeeded.
            pass
    except Exception:
        return False
    finally:
        if connection:
            connection.close()
    for _ in range(20):
        try:
            requests.get(f"http://127.0.0.1:{port}/json/version", timeout=0.2)
        except Exception:
            return True
        time.sleep(0.1)
    return True


class DouyinResolver:
    def __init__(
        self,
        chrome_path: Path = CHROME_PATH,
        log: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        profile_dir: Path | None = None,
    ):
        self.chrome_path = Path(chrome_path)
        self.log = log or (lambda _: None)
        self.cancel_event = cancel_event or threading.Event()
        self.persistent_profile_dir = (
            Path(profile_dir) if profile_dir is not None else None
        )
        self.profile_dir: Path | None = None
        self.owns_profile_dir = False
        self.chrome: subprocess.Popen | None = None
        self.cdp: CDPConnection | None = None

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise DownloadCancelled("任务已取消。")

    def _start_chrome(self):
        if not self.chrome_path.exists():
            raise ParseError(f"未找到 Chrome：{self.chrome_path}")
        if self.persistent_profile_dir is not None:
            self.profile_dir = self.persistent_profile_dir
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.owns_profile_dir = False
            close_stale_profile_browser(self.profile_dir, self.log)
            (self.profile_dir / "DevToolsActivePort").unlink(missing_ok=True)
        else:
            self.profile_dir = Path(
                tempfile.mkdtemp(prefix="douyin_downloader_chrome_")
            )
            self.owns_profile_dir = True
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
            "--disable-popup-blocking",
            "--disable-notifications",
            "--disable-features=Translate,OptimizationHints,MediaRouter",
            "--autoplay-policy=no-user-gesture-required",
            "--mute-audio",
            "--window-size=1280,900",
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
        active_port = self.profile_dir / "DevToolsActivePort"
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self._check_cancel()
            if self.chrome.poll() is not None:
                raise ParseError("独立 Chrome 启动失败。")
            if active_port.exists():
                lines = active_port.read_text(encoding="utf-8").splitlines()
                if lines:
                    port = int(lines[0])
                    break
            time.sleep(0.1)
        else:
            raise ParseError("等待 Chrome 调试端口超时。")

        self.log(f"独立 Chrome 已启动（临时端口 {port}）")
        deadline = time.monotonic() + 8
        last_error = None
        while time.monotonic() < deadline:
            try:
                targets = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=1).json()
                page = next(t for t in targets if t.get("type") == "page")
                self.cdp = CDPConnection(page["webSocketDebuggerUrl"])
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.15)
        raise ParseError(f"连接 Chrome 调试接口失败：{last_error}")

    @staticmethod
    def _is_video_response(response: dict) -> bool:
        url = response.get("url", "")
        mime = response.get("mimeType", "").lower()
        host = (urlparse(url).hostname or "").lower()
        if any(part in host for part in DECORATION_HOST_PARTS):
            return False
        if url.startswith("blob:") or url.startswith("data:"):
            return False
        return (
            mime.startswith(("video/mp4", "audio/mp4"))
            or "douyinvod" in host
        )

    @staticmethod
    def _media_kind(url: str, mime: str) -> str:
        mime = (mime or "").lower()
        lowered_url = url.lower()
        if mime.startswith("audio/"):
            return "audio"
        if re.search(
            r"audio(?:_|%5f)?mp4|"
            r"(?:mime_type|media_type|type)(?:=|%3d|[/:_-])?(?:audio|audio_mp4)|"
            r"(?:^|[?&/_-])audio(?:[?&/_=-]|$)|mp4a",
            lowered_url,
        ):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        if re.search(
            r"(?:mime_type|media_type|type)[=/:%_-]?(?:video|video_mp4)|"
            r"(?:^|[?&/_-])video(?:[?&/_=-]|$)|avc1|h264|h265|hevc",
            lowered_url,
        ):
            return "video"
        return "unknown"

    def _evaluate(self, expression: str) -> object:
        assert self.cdp
        result = self.cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    def resolve(self, share_text: str, timeout: float = 30) -> ParseResult:
        share_url = extract_douyin_url(share_text)
        self.log(f"已提取链接：{share_url}")
        navigation_url = share_url
        # Since July 2026 some v.douyin.com links redirect to an empty
        # iesdouyin application/json landing response. Expand the redirect
        # cheaply, extract the public work ID, and navigate Chrome to the
        # canonical public video page instead.
        if (urlparse(share_url).hostname or "").lower() == "v.douyin.com":
            try:
                expanded = requests.get(
                    share_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    allow_redirects=True,
                    timeout=(5, 10),
                ).url
                canonical, expanded_id = canonical_public_video_url(expanded)
                if expanded_id:
                    navigation_url = canonical
                    self.log(f"检测到新版分享落地页，已识别作品 {expanded_id}")
                    self.log(f"自动切换到公开作品页：{canonical}")
            except requests.RequestException:
                # Chrome will still follow the short link; frame navigation below
                # provides the same canonicalization fallback.
                pass
        self._start_chrome()
        assert self.cdp
        self.cdp.call("Network.enable", {"maxTotalBufferSize": 1000000, "maxResourceBufferSize": 500000})
        self.cdp.call("Page.enable")
        self.cdp.call("Runtime.enable")
        self.cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        self.cdp.call("Page.navigate", {"url": navigation_url})
        self.log("正在打开公开作品页并监听媒体请求…")

        candidates: dict[str, VideoCandidate] = {}
        request_urls: dict[str, str] = {}
        rejected = 0
        final_url = share_url
        deadline = time.monotonic() + timeout
        last_new = time.monotonic()
        last_play_attempt = 0.0
        media_hosts_seen: set[str] = set()
        canonical_redirect_done = navigation_url != share_url
        while time.monotonic() < deadline:
            self._check_cancel()
            if time.monotonic() - last_play_attempt > 1.5:
                last_play_attempt = time.monotonic()
                try:
                    self._evaluate("(()=>{const v=document.querySelector('video');if(v){v.muted=false;v.autoplay=true;v.play().catch(()=>{});return v.currentSrc||v.src||'video-found'}return 'no-video'})()")
                except Exception:
                    pass
            try:
                event = self.cdp.events.get(timeout=0.25)
            except queue.Empty:
                if candidates and time.monotonic() - last_new > 8:
                    break
                continue
            method = event.get("method")
            params = event.get("params", {})
            if method == "Network.responseReceived":
                response = params.get("response", {})
                url = response.get("url", "")
                mime = response.get("mimeType", "")
                host = (urlparse(url).hostname or "").lower()
                if params.get("type") == "Media" and host and host not in media_hosts_seen:
                    media_hosts_seen.add(host)
                    self.log(f"发现媒体请求：{host}（{mime or '未知类型'}）")
                if mime.lower().startswith(("video/mp4", "audio/mp4")) or "douyinvod" in host:
                    if self._is_video_response(response):
                        headers = {str(k).lower(): str(v) for k, v in response.get("headers", {}).items()}
                        length = int(float(headers.get("content-length", "0") or 0))
                        media_kind = self._media_kind(url, mime)
                        candidates[url] = VideoCandidate(
                            url=url,
                            mime_type=mime,
                            media_kind=media_kind,
                            response_headers=headers,
                            encoded_length=length,
                        )
                        request_id = str(params.get("requestId") or "")
                        if request_id:
                            request_urls[request_id] = url
                        last_new = time.monotonic()
                        kind_label = {
                            "audio": "音频",
                            "video": "视频",
                            "unknown": "媒体",
                        }[media_kind]
                        self.log(f"捕获作品{kind_label}候选：{urlparse(url).hostname}（{human_size(length) if length else '大小待检测'}）")
                    else:
                        rejected += 1
                        self.log(f"已排除页面装饰视频：{urlparse(url).hostname or '未知来源'}")
            elif method == "Network.loadingFinished":
                request_id = str(params.get("requestId") or "")
                encoded = int(params.get("encodedDataLength", 0))
                candidate_url = request_urls.get(request_id)
                if encoded and candidate_url in candidates:
                    candidate = candidates[candidate_url]
                    candidate.encoded_length = max(candidate.encoded_length, encoded)
            elif method == "Page.frameNavigated":
                frame = params.get("frame", {})
                if not frame.get("parentId") and frame.get("url", "").startswith("http"):
                    final_url = frame["url"]
                    canonical, redirect_id = canonical_public_video_url(final_url)
                    if redirect_id and not canonical_redirect_done:
                        canonical_redirect_done = True
                        self.log(f"检测到新版分享落地页，已识别作品 {redirect_id}")
                        self.log(f"自动切换到公开作品页：{canonical}")
                        self.cdp.call("Page.navigate", {"url": canonical})
                        final_url = canonical

        if not candidates:
            try:
                diagnosis = self._evaluate("JSON.stringify({url:location.href,title:document.title,videoCount:document.querySelectorAll('video').length,text:(document.body&&document.body.innerText||'').slice(0,240)})")
                self.log(f"页面诊断：{diagnosis}")
            except Exception:
                pass
            raise ParseError("解析超时：没有捕获到可下载的作品 MP4。请确认作品公开且链接有效。")

        # Obtain page metadata and the temporary browser session before probing URLs.
        try:
            page_meta = self._evaluate("JSON.stringify({url:location.href,title:document.title,description:(document.querySelector('meta[name=description]')||{}).content||'',og:(document.querySelector('meta[property=\"og:title\"]')||{}).content||''})")
            meta = json.loads(page_meta or "{}")
            final_url = meta.get("url") or final_url
        except Exception:
            meta = {}
        cookie_rows = self.cdp.call("Network.getAllCookies").get("cookies", [])
        cookies = {row["name"]: row["value"] for row in cookie_rows if row.get("name")}
        ua = str(self._evaluate("navigator.userAgent") or "Mozilla/5.0")
        headers = {"User-Agent": ua, "Referer": final_url, "Accept": "*/*", "Range": "bytes=0-1"}

        self.log(f"共捕获 {len(candidates)} 个有效候选，正在比较清晰度…")
        for candidate in candidates.values():
            self._check_cancel()
            try:
                with requests.get(candidate.url, headers=headers, cookies=cookies, stream=True, timeout=(8, 12), allow_redirects=True) as response:
                    if response.status_code not in (200, 206):
                        continue
                    candidate.probed_length = int(response.headers.get("Content-Length", "0") or 0)
                    content_range = response.headers.get("Content-Range", "")
                    match = re.search(r"/(\d+)$", content_range)
                    if match:
                        candidate.content_range_total = int(match.group(1))
            except requests.RequestException:
                continue
        video_candidates = [
            candidate
            for candidate in candidates.values()
            if candidate.media_kind != "audio"
        ]
        audio_candidates = [
            candidate
            for candidate in candidates.values()
            if candidate.media_kind == "audio"
        ]
        if not video_candidates:
            raise ParseError("只捕获到音频流，没有捕获到作品画面，请稍后重试。")
        best = max(video_candidates, key=lambda c: c.score)
        best_audio = (
            max(audio_candidates, key=lambda c: c.score)
            if audio_candidates
            else None
        )

        id_match = VIDEO_ID_RE.search(final_url)
        if not id_match:
            id_match = re.search(r"(?:aweme_id|item_id)[=/](\d{15,22})", best.url)
        video_id = id_match.group(1) if id_match else str(int(time.time()))
        # document.title is normally the concise work caption; og/description often
        # append author, date and engagement statistics and make poor filenames.
        title = meta.get("title") or meta.get("og") or meta.get("description") or "抖音视频"
        title = re.sub(r"\s*[-—_]?\s*抖音(?:短视频)?\s*$", "", title).strip()
        title = title.split(" - 抖音")[0].strip()
        self.log(f"已选择最高质量资源：{best.host}，预计 {human_size(max(best.content_range_total, best.probed_length, best.encoded_length))}")
        if best_audio:
            self.log(
                "已同时捕获独立音频流，下载后将自动合并声音。"
            )
        return ParseResult(
            share_url=share_url,
            final_page_url=final_url,
            video_id=video_id,
            title=title or "抖音视频",
            video_url=best.url,
            content_length=max(best.content_range_total, best.probed_length, best.encoded_length),
            referer=final_url,
            user_agent=ua,
            cookies=cookies,
            candidate_count=len(candidates),
            rejected_count=rejected,
            audio_url=best_audio.url if best_audio else "",
            audio_content_length=(
                max(
                    best_audio.content_range_total,
                    best_audio.probed_length,
                    best_audio.encoded_length,
                )
                if best_audio
                else 0
            ),
        )

    def resolve_images(
        self,
        share_text: str,
        timeout: float = 35,
    ) -> ImageParseResult:
        share_url = extract_douyin_url(share_text)
        id_match = VIDEO_ID_RE.search(share_url)
        work_id = id_match.group(1) if id_match else ""
        if not work_id:
            raise ParseError("图文链接中没有找到作品 ID。")
        self.log(f"已提取图文链接：{share_url}")
        self._start_chrome()
        assert self.cdp
        self.cdp.call("Network.enable", {"maxTotalBufferSize": 50000000})
        self.cdp.call("Page.enable")
        self.cdp.call("Runtime.enable")
        self.cdp.call("Page.navigate", {"url": share_url})
        self.log("正在打开公开图文页并读取原图列表…")

        deadline = time.monotonic() + timeout
        response_requests: set[str] = set()
        image_urls: list[str] = []
        title = ""
        final_url = share_url
        last_page_probe = 0.0
        while time.monotonic() < deadline:
            self._check_cancel()
            if time.monotonic() - last_page_probe > 1:
                last_page_probe = time.monotonic()
                page_result = self._image_meta_from_page(work_id)
                if page_result:
                    title = str(
                        page_result.get("desc")
                        or page_result.get("title")
                        or title
                    )
                    image_urls = _image_urls_from_aweme(page_result) or image_urls
            if image_urls:
                break
            try:
                event = self.cdp.events.get(timeout=0.25)
            except queue.Empty:
                continue
            method = event.get("method")
            params = event.get("params", {})
            if method == "Page.frameNavigated":
                frame = params.get("frame", {})
                if not frame.get("parentId") and frame.get("url", "").startswith("http"):
                    final_url = str(frame["url"])
            elif method == "Network.responseReceived":
                response = params.get("response", {})
                response_url = str(response.get("url") or "")
                mime = str(response.get("mimeType") or "").lower()
                if (
                    "douyin.com" in (urlparse(response_url).hostname or "")
                    and ("json" in mime or "aweme" in response_url)
                    and any(
                        marker in response_url
                        for marker in ("aweme/detail", "aweme/post", "aweme/multi")
                    )
                ):
                    request_id = str(params.get("requestId") or "")
                    if request_id:
                        response_requests.add(request_id)
            elif method == "Network.loadingFinished":
                request_id = str(params.get("requestId") or "")
                if request_id not in response_requests:
                    continue
                response_requests.discard(request_id)
                try:
                    body = self.cdp.call(
                        "Network.getResponseBody",
                        {"requestId": request_id},
                    ).get("body", "")
                    payload = json.loads(body or "{}")
                except Exception:
                    continue
                aweme = _find_aweme_with_images(payload, work_id)
                if aweme:
                    title = str(aweme.get("desc") or aweme.get("title") or title)
                    image_urls = _image_urls_from_aweme(aweme)

        if not image_urls:
            raise ParseError("图文解析超时：没有取得作品原图，请稍后重试。")
        cookie_rows = self.cdp.call("Network.getAllCookies").get("cookies", [])
        cookies = {row["name"]: row["value"] for row in cookie_rows if row.get("name")}
        user_agent = str(self._evaluate("navigator.userAgent") or "Mozilla/5.0")
        try:
            page_title = str(self._evaluate("document.title") or "")
        except Exception:
            page_title = ""
        title = (title or page_title or f"抖音图文_{work_id}").split(" - 抖音")[0].strip()
        self.log(f"图文作品 {work_id} 已识别 {len(image_urls)} 张原图。")
        return ImageParseResult(
            share_url=share_url,
            final_page_url=final_url,
            work_id=work_id,
            title=title,
            image_urls=image_urls,
            referer=final_url,
            user_agent=user_agent,
            cookies=cookies,
        )

    def _image_meta_from_page(self, work_id: str) -> dict | None:
        expression = f"""(()=>{{
          const target={json.dumps(work_id)};
          const roots=[window._ROUTER_DATA,window.__INITIAL_STATE__,window.__NEXT_DATA__].filter(Boolean);
          const queue=[...roots]; const seen=new Set(); let steps=0;
          while(queue.length && steps<20000){{
            const value=queue.shift(); steps+=1;
            if(!value || typeof value!=='object' || seen.has(value)) continue;
            seen.add(value);
            const id=String(value.aweme_id||value.awemeId||value.item_id||value.itemId||'');
            const images=value.images || value.image_list || (value.image_post_info&&value.image_post_info.images);
            if(id===target && Array.isArray(images) && images.length){{
              return JSON.stringify({{aweme_id:id,desc:value.desc||value.title||'',images}});
            }}
            for(const child of Object.values(value)){{
              if(child && typeof child==='object') queue.push(child);
            }}
          }}
          return '';
        }})()"""
        try:
            raw = self._evaluate(expression)
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) and value else None
        except Exception:
            return None

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
        if self.profile_dir and self.owns_profile_dir:
            for _ in range(5):
                try:
                    shutil.rmtree(self.profile_dir, ignore_errors=False)
                    break
                except OSError:
                    time.sleep(0.3)
        self.profile_dir = None
        self.owns_profile_dir = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def download_video(
    result: ParseResult,
    destination: Path,
    progress: Callable[[int, int, float], None] | None = None,
    cancel_event: threading.Event | None = None,
    custom_filename: str | None = None,
) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    cancel_event = cancel_event or threading.Event()
    custom_stem = (custom_filename or "").strip()
    if custom_stem.lower().endswith(".mp4"):
        custom_stem = custom_stem[:-4].strip()
    stem = sanitize_filename(custom_stem, fallback=result.title) if custom_stem else sanitize_filename(result.title)
    # Keep the work ID for reliable deduplication unless the user already put it in the name.
    suffix = "" if result.video_id in stem else f"_{result.video_id}"
    filename = f"{stem}{suffix}.mp4"
    output = destination / filename
    part = output.with_suffix(".mp4.part")
    if output.exists() and output.stat().st_size > 1024:
        return output

    expected = result.content_length + result.audio_content_length
    if expected:
        free = shutil.disk_usage(destination).free
        required = expected * 2 + 50 * 1024 * 1024
        if free < required:
            raise OSError(f"磁盘空间不足：至少还需要 {human_size(required)}。")
    headers = {
        "User-Agent": result.user_agent,
        "Referer": result.referer,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    audio_part = output.with_suffix(".audio.part.m4a")
    mux_part = output.with_suffix(".mux.part.mp4")

    def fetch_resource(
        url: str,
        target: Path,
        expected_size: int,
        *,
        report_progress: bool,
    ) -> int:
        started = time.monotonic()
        downloaded = 0
        with requests.get(
            url,
            headers=headers,
            cookies=result.cookies,
            stream=True,
            timeout=(10, 30),
            allow_redirects=True,
        ) as response:
            if response.status_code not in (200, 206):
                raise ParseError(
                    f"媒体资源请求失败（HTTP {response.status_code}），"
                    "资源可能已过期，请重新解析。"
                )
            total = (
                int(response.headers.get("Content-Length", "0") or 0)
                or expected_size
            )
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    if cancel_event.is_set():
                        raise DownloadCancelled("下载已取消。")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress and report_progress:
                        elapsed = max(time.monotonic() - started, 0.001)
                        progress(downloaded, total, downloaded / elapsed)
        if downloaded < 1024:
            raise ParseError("下载内容过小，不是有效媒体文件。")
        return downloaded

    try:
        fetch_resource(
            result.video_url,
            part,
            result.content_length,
            report_progress=True,
        )
        inspection = inspect_mp4(part)
        if not inspection["has_video"]:
            raise ParseError("捕获的资源没有视频画面，已取消保存并等待重试。")
        if inspection["has_audio"]:
            os.replace(part, output)
            return output
        if not result.audio_url:
            raise ParseError(
                "捕获到的是无声视频流，但没有捕获到配套音频，"
                "已取消保存并等待重试。"
            )
        fetch_resource(
            result.audio_url,
            audio_part,
            result.audio_content_length,
            report_progress=False,
        )
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ParseError("服务器缺少 ffmpeg，无法把画面和声音合并。")
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-i",
                str(part),
                "-i",
                str(audio_part),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(mux_part),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "未知错误").strip()[-500:]
            raise ParseError(f"音视频合并失败：{detail}")
        merged = inspect_mp4(mux_part)
        if not merged["has_video"] or not merged["has_audio"]:
            raise ParseError("合并后的 MP4 未同时包含画面和声音。")
        os.replace(mux_part, output)
        return output
    except Exception:
        raise
    finally:
        for temporary in (part, audio_part, mux_part):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _find_aweme_with_images(value: object, work_id: str) -> dict | None:
    queue_values: list[object] = [value]
    visited = 0
    while queue_values and visited < 50000:
        current = queue_values.pop()
        visited += 1
        if isinstance(current, dict):
            current_id = str(
                current.get("aweme_id")
                or current.get("awemeId")
                or current.get("item_id")
                or current.get("itemId")
                or ""
            )
            image_post_info = current.get("image_post_info") or {}
            images = (
                current.get("images")
                or current.get("image_list")
                or (
                    image_post_info.get("images")
                    if isinstance(image_post_info, dict)
                    else None
                )
            )
            if current_id == work_id and isinstance(images, list) and images:
                return current
            queue_values.extend(current.values())
        elif isinstance(current, list):
            queue_values.extend(current)
    return None


def _image_urls_from_aweme(aweme: dict) -> list[str]:
    image_post_info = aweme.get("image_post_info") or {}
    images = (
        aweme.get("images")
        or aweme.get("image_list")
        or (
            image_post_info.get("images")
            if isinstance(image_post_info, dict)
            else None
        )
        or []
    )
    urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        candidates: list[str] = []
        for key in ("download_url_list", "url_list"):
            values = image.get(key) or []
            if isinstance(values, list):
                candidates.extend(
                    str(item) for item in values if str(item).startswith("http")
                )
        for nested_key in ("display_image", "owner_watermark_image", "thumbnail"):
            nested = image.get(nested_key) or {}
            if isinstance(nested, dict):
                values = nested.get("url_list") or []
                if isinstance(values, list):
                    candidates.extend(
                        str(item) for item in values if str(item).startswith("http")
                    )
        if candidates:
            url = candidates[0]
            if url not in urls:
                urls.append(url)
    return urls


def download_images(
    result: ImageParseResult,
    destination: Path,
    custom_prefix: str,
) -> list[Path]:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    prefix = sanitize_filename(custom_prefix, fallback=result.work_id)
    headers = {
        "User-Agent": result.user_agent,
        "Referer": result.referer,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    outputs: list[Path] = []
    for index, url in enumerate(result.image_urls, start=1):
        existing = next(
            (
                path
                for path in destination.glob(f"{prefix}_{index:02}.*")
                if path.is_file() and path.stat().st_size > 1024
            ),
            None,
        )
        if existing:
            outputs.append(existing)
            continue
        with requests.get(
            url,
            headers=headers,
            cookies=result.cookies,
            stream=True,
            timeout=(10, 45),
        ) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "png" in content_type:
                extension = ".png"
            elif "webp" in content_type:
                extension = ".webp"
            else:
                extension = ".jpg"
            output = destination / f"{prefix}_{index:02}{extension}"
            temporary = output.with_suffix(output.suffix + ".download")
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(1024 * 256):
                    if chunk:
                        handle.write(chunk)
            if temporary.stat().st_size <= 1024:
                temporary.unlink(missing_ok=True)
                raise ParseError(f"第 {index} 张原图下载内容过小。")
            temporary.replace(output)
            outputs.append(output)
    return outputs
