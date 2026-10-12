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
from urllib.parse import parse_qs, urlparse

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
SHORT_LINK_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/?", re.I)
ANY_DOUYIN_RE = re.compile(
    r"https?://(?:(?:www\.)?douyin\.com/(?:video/\d+|[A-Za-z0-9_?&=./%-]+)"
    r"|www\.iesdouyin\.com/share/video/\d+/?[A-Za-z0-9_?&=./%-]*)",
    re.I,
)
VIDEO_ID_RE = re.compile(
    r"/(?:video|note|article)/(\d{15,22})(?:[/?#]|$)",
    re.I,
)
IES_SHARE_ID_RE = re.compile(r"/share/video/(\d{15,22})(?:[/?#]|$)", re.I)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DECORATION_HOST_PARTS = (
    "douyinstatic.com",
    "douyinpic.com",
    "byteimg.com",
    "pstatp.com",
    "ibytedtos.com",
)
MODEL_VIDEO_SCALE_FILTER = (
    "scale=trunc(max(iw/2\\,240)/2)*2:-2:flags=lanczos"
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


def douyin_work_id_from_url(url: str) -> str:
    """Return an exact work ID from a public path or Douyin modal query.

    Creator profile cards sometimes open the work in the same single-page app
    with ``modal_id`` instead of replacing the path with ``/video/<id>``.
    Both representations identify the selected work; unrelated recommendation
    and ``/jingxuan`` pages intentionally return an empty value.
    """

    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    if host != "douyin.com" and not host.endswith(".douyin.com"):
        return ""
    path_match = VIDEO_ID_RE.search(parsed.path)
    if path_match:
        return path_match.group(1)
    for key in ("modal_id", "aweme_id", "item_id"):
        values = parse_qs(parsed.query).get(key) or []
        if values and re.fullmatch(r"\d{15,22}", str(values[0])):
            return str(values[0])
    return ""


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
        "fast_start": 0 <= head.find(b"moov") < head.find(b"mdat"),
        "has_video": has_video,
        "has_audio": has_audio,
    }


def probe_media_streams(path: Path) -> dict[str, dict[str, object]]:
    """Return the first video and audio stream signatures from a media file.

    The source-quality re-download path uses this strict probe both to prove
    that stream copy did not change the selected video and to prevent an
    equal-or-lower-resolution result from replacing the existing compact copy.
    """

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ParseError("服务器缺少 ffprobe，无法核验高清原视频。")
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        rows = json.loads(completed.stdout or "{}").get("streams", [])
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        raise ParseError(f"无法核验媒体流信息：{exc}") from exc

    signatures: dict[str, dict[str, object]] = {}
    for row in rows:
        stream_type = str(row.get("codec_type") or "")
        if stream_type not in {"video", "audio"} or stream_type in signatures:
            continue
        signatures[stream_type] = {
            "codec_name": str(row.get("codec_name") or ""),
            "width": int(row.get("width") or 0),
            "height": int(row.get("height") or 0),
            "pix_fmt": str(row.get("pix_fmt") or ""),
        }
    return signatures


def video_dimensions(path: Path) -> tuple[int, int]:
    video = probe_media_streams(path).get("video", {})
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ParseError("无法取得有效视频分辨率，已拒绝替换现有文件。")
    return width, height


def source_quality_improves(
    previous: tuple[int, int],
    replacement: tuple[int, int],
) -> bool:
    """Require a strictly larger image without shrinking either dimension."""

    old_width, old_height = previous
    new_width, new_height = replacement
    if min(old_width, old_height, new_width, new_height) <= 0:
        return False
    return (
        new_width >= old_width
        and new_height >= old_height
        and new_width * new_height > old_width * old_height
    )


def compact_video_command(
    ffmpeg: str,
    video_input: Path,
    output: Path,
    *,
    audio_input: Path | None = None,
) -> list[str]:
    """Build the fixed Model Mr phone-video compression command.

    New Beijing videos keep their aspect ratio while width and height are
    normally halved (720x1280 becomes 360x640).  The 240-pixel width floor
    prevents an already-small source from becoming unreadable.  H.264/AAC,
    yuv420p and fast-start preserve the existing iPhone playback contract.
    """

    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        str(video_input),
    ]
    if audio_input is not None:
        command.extend(["-i", str(audio_input)])
    command.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0" if audio_input is not None else "0:a:0",
            "-vf",
            MODEL_VIDEO_SCALE_FILTER,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "27",
            "-profile:v",
            "main",
            "-level:v",
            "3.1",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return command


def source_quality_video_command(
    ffmpeg: str,
    video_input: Path,
    output: Path,
    *,
    audio_input: Path | None = None,
) -> list[str]:
    """Build a lossless remux command for one explicit HD re-download.

    This command intentionally contains no scale, codec, pixel-format or
    bitrate conversion.  The selected highest-quality Douyin video stream and
    its audio are copied as-is; ffmpeg only rebuilds the MP4 container so its
    index is at the front for mobile playback.
    """

    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        str(video_input),
    ]
    if audio_input is not None:
        command.extend(["-i", str(audio_input)])
    command.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0" if audio_input is not None else "0:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return command


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
    description: str
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
        self.debug_port: int | None = None

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
        if os.name != "nt":
            args[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
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
                    self.debug_port = port
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

    def _discard_pending_events(self) -> None:
        """Discard network events produced before the exact work is opened."""

        assert self.cdp
        while True:
            try:
                self.cdp.events.get_nowait()
            except queue.Empty:
                return

    def _switch_to_exact_page_target(self, work_id: str) -> bool:
        """Attach to a newly opened exact-ID page after one profile click."""

        debug_port = getattr(self, "debug_port", None)
        if not debug_port:
            return False
        try:
            targets = requests.get(
                f"http://127.0.0.1:{debug_port}/json/list",
                timeout=1,
            ).json()
        except Exception:
            return False
        if not isinstance(targets, list):
            return False
        target = next(
            (
                row
                for row in targets
                if isinstance(row, dict)
                and row.get("type") == "page"
                and douyin_work_id_from_url(str(row.get("url") or ""))
                == work_id
                and row.get("webSocketDebuggerUrl")
            ),
            None,
        )
        if target is None:
            return False

        previous = self.cdp
        replacement: CDPConnection | None = None
        try:
            replacement = CDPConnection(str(target["webSocketDebuggerUrl"]))
            replacement.call(
                "Network.enable",
                {
                    "maxTotalBufferSize": 1000000,
                    "maxResourceBufferSize": 500000,
                },
            )
            replacement.call("Page.enable")
            replacement.call("Runtime.enable")
            replacement.call(
                "Network.setCacheDisabled",
                {"cacheDisabled": True},
            )
        except Exception:
            if replacement is not None:
                try:
                    replacement.close()
                except Exception:
                    pass
            return False
        self.cdp = replacement
        if previous is not None:
            previous.close()
        self.log(f"已切换到新窗口中的精确作品 {work_id}。")
        return True

    def _exact_page_is_active(self, work_id: str) -> bool:
        try:
            current_url = str(self._evaluate("location.href") or "")
        except Exception:
            current_url = ""
        if douyin_work_id_from_url(current_url) == work_id:
            return True
        return self._switch_to_exact_page_target(work_id)

    def _arm_exact_media_capture(
        self,
        work_id: str,
        *,
        deadline: float,
    ) -> bool:
        """Create a post-navigation event barrier and reload target media.

        The main resolver additionally accepts only response request IDs whose
        ``requestWillBeSent`` event occurs after this barrier.  A late response
        from a creator-grid preview therefore cannot become a candidate even
        if it arrives after the queue was cleared.
        """

        while time.monotonic() < deadline:
            self._check_cancel()
            if not self._exact_page_is_active(work_id):
                time.sleep(0.2)
                continue
            try:
                has_video = bool(
                    self._evaluate(
                        """(()=>{
                          const visible=v=>{
                            const r=v.getBoundingClientRect();
                            const s=getComputedStyle(v);
                            return r.width>120 && r.height>120 &&
                              s.display!=='none' && s.visibility!=='hidden';
                          };
                          return [...document.querySelectorAll('video')]
                            .some(visible);
                        })()"""
                    )
                )
            except Exception:
                has_video = False
            if not has_video:
                time.sleep(0.2)
                continue

            # This is the event barrier: it is deliberately after exact page
            # identity is established.  Disabling Network creates a protocol
            # ordering fence before the queue clear.  Reload/play below then
            # generates new target requests, which the caller correlates by
            # their fresh request IDs.
            self.cdp.call("Network.disable")
            self._discard_pending_events()
            self.cdp.call(
                "Network.enable",
                {
                    "maxTotalBufferSize": 1000000,
                    "maxResourceBufferSize": 500000,
                },
            )
            self.cdp.call(
                "Network.setCacheDisabled",
                {"cacheDisabled": True},
            )
            self._discard_pending_events()
            try:
                triggered = bool(
                    self._evaluate(
                        """(()=>{
                          const videos=[...document.querySelectorAll('video')];
                          const visible=v=>{
                            const r=v.getBoundingClientRect();
                            const s=getComputedStyle(v);
                            return r.width>120 && r.height>120 &&
                              s.display!=='none' && s.visibility!=='hidden';
                          };
                          const video=videos.filter(visible).sort((a,b)=>{
                            const ar=a.getBoundingClientRect();
                            const br=b.getBoundingClientRect();
                            return br.width*br.height-ar.width*ar.height;
                          })[0];
                          if(!video) return false;
                          try { video.pause(); } catch (_) {}
                          video.preload='auto';
                          try { video.load(); } catch (_) {}
                          video.muted=false;
                          video.autoplay=true;
                          video.play().catch(()=>{});
                          return true;
                        })()"""
                    )
                )
            except Exception:
                triggered = False
            if triggered:
                self.log(
                    f"精确作品 {work_id} 已在事件屏障后重新触发媒体加载。"
                )
                return True
            raise ParseError(
                "精确作品页未能重新触发媒体加载；已停止且不会重复点击。"
            )
        return False

    def _open_exact_work_from_profile(
        self,
        profile_url: str,
        work_id: str,
        *,
        timeout: float,
    ) -> bool:
        """Open one exact creator card inside the authenticated profile SPA.

        Douyin can redirect a cold direct ``/video/<id>`` navigation to
        ``/jingxuan`` even though the same persistent browser can still read
        the creator profile.  Entering through the exact profile card preserves
        that authenticated SPA context.  After the exact page or new window is
        confirmed, a protocol event fence and a fresh video load ensure profile
        preview responses cannot become candidates for the requested work.
        """

        assert self.cdp
        if not re.fullmatch(r"\d{15,22}", str(work_id or "")):
            raise ParseError("从主页打开作品时缺少精确作品 ID。")
        parsed_profile = urlparse(str(profile_url or ""))
        if (
            parsed_profile.scheme != "https"
            or (parsed_profile.hostname or "").lower()
            not in {"douyin.com", "www.douyin.com"}
            or not parsed_profile.path.startswith("/user/")
        ):
            raise ParseError("模型先生主页地址不是受支持的抖音 HTTPS 用户页。")

        self.cdp.call("Page.navigate", {"url": profile_url})
        self.log(f"直达作品页可能受限，正在从模型先生主页定位作品 {work_id}…")
        started = time.monotonic()
        deadline = started + max(1.0, float(timeout))
        reload_at = started + min(14.0, max(4.0, float(timeout) / 2))
        reloaded = False
        click_attempted = False
        while time.monotonic() < deadline:
            self._check_cancel()
            try:
                raw = self._evaluate(
                    f"""JSON.stringify((()=>{{
                      const target={json.dumps(work_id)};
                      const links=[...document.querySelectorAll(
                        'a[href*="/video/"],a[href*="/note/"],a[href*="/article/"]'
                      )];
                      const link=links.find(a=>{{
                        try {{
                          const u=new URL(a.href,location.href);
                          return new RegExp('/(?:video|note|article)/'+target+'(?:/|$)').test(u.pathname);
                        }} catch (_) {{ return false; }}
                      }});
                      return {{
                        url:location.href,
                        href:link ? link.href : '',
                        text:(document.body&&document.body.innerText||'').slice(0,500)
                      }};
                    }})())"""
                )
                snapshot = json.loads(str(raw or "{}"))
            except Exception:
                snapshot = {}

            href = str(snapshot.get("href") or "")
            if douyin_work_id_from_url(href) == work_id:
                if click_attempted:
                    raise ParseError(
                        "精确作品卡片已经点击一次，已拒绝重复点击。"
                    )
                click_attempted = True
                self._discard_pending_events()
                clicked = bool(
                    self._evaluate(
                        f"""(()=>{{
                          const target={json.dumps(work_id)};
                          const link=[...document.querySelectorAll(
                            'a[href*="/video/"],a[href*="/note/"],a[href*="/article/"]'
                          )].find(a=>{{
                            try {{
                              const u=new URL(a.href,location.href);
                              return new RegExp('/(?:video|note|article)/'+target+'(?:/|$)').test(u.pathname);
                            }} catch (_) {{ return false; }}
                          }});
                          if(!link) return false;
                          link.removeAttribute('target');
                          link.scrollIntoView({{block:'center'}});
                          link.click();
                          return true;
                        }})()"""
                    )
                )
                if not clicked:
                    raise ParseError(
                        "精确作品卡片在点击前消失；已停止且不会重复点击。"
                    )
                route_deadline = min(deadline, time.monotonic() + 12)
                if self._arm_exact_media_capture(
                    work_id,
                    deadline=route_deadline,
                ):
                    self.log(
                        f"已从模型先生主页打开精确作品 {work_id}。"
                    )
                    return True
                raise ParseError(
                    "点击精确作品卡片后未进入对应页面或窗口；"
                    "已停止且不会重复点击。"
                )

            body_text = str(snapshot.get("text") or "")
            if (
                not reloaded
                and (
                    "服务异常，重新刷新拉取数据" in body_text
                    or time.monotonic() >= reload_at
                )
                and time.monotonic() + 5 < deadline
            ):
                reloaded = True
                self.log("主页暂未出现精确作品卡片，正在刷新后再定位一次…")
                self.cdp.call("Page.reload", {"ignoreCache": True})
                time.sleep(2)
                continue
            time.sleep(0.5)

        self.log(
            f"主页当前可见卡片未能打开精确作品 {work_id}，"
            "将以已刷新登录态再试一次原作品地址。"
        )
        return False

    def resolve(
        self,
        share_text: str,
        timeout: float = 30,
        *,
        profile_entry_url: str | None = None,
        expected_video_id: str | None = None,
    ) -> ParseResult:
        share_url = extract_douyin_url(share_text)
        self.log(f"已提取链接：{share_url}")
        expected_id = str(expected_video_id or "")
        if expected_id and not re.fullmatch(r"\d{15,22}", expected_id):
            raise ValueError("expected_video_id 必须是 15–22 位抖音作品 ID。")
        share_work_id = douyin_work_id_from_url(share_url)
        if expected_id and share_work_id and share_work_id != expected_id:
            raise ParseError(
                "请求链接与精确目标作品 ID 不一致，已拒绝打开页面。"
            )
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
        opened_from_profile = False
        if profile_entry_url and expected_id:
            opened_from_profile = self._open_exact_work_from_profile(
                profile_entry_url,
                expected_id,
                timeout=min(32.0, max(12.0, float(timeout) * 0.64)),
            )
        if not opened_from_profile:
            # No profile-grid media response may leak into the direct-page
            # candidate set when the exact card was not found.
            self._discard_pending_events()
            self.cdp.call("Page.navigate", {"url": navigation_url})
            self.log("正在打开公开作品页并监听媒体请求…")
        else:
            self.log("正在监听精确作品页的最高画质媒体请求…")

        candidates: dict[str, VideoCandidate] = {}
        request_urls: dict[str, str] = {}
        rejected = 0
        final_url = share_url
        deadline = time.monotonic() + timeout
        last_new = time.monotonic()
        last_play_attempt = 0.0
        media_hosts_seen: set[str] = set()
        canonical_redirect_done = navigation_url != share_url
        fresh_request_ids: set[str] = set()
        while time.monotonic() < deadline:
            self._check_cancel()
            if time.monotonic() - last_play_attempt > 1.5:
                last_play_attempt = time.monotonic()
                try:
                    if opened_from_profile:
                        self._evaluate(
                            """(()=>{
                              const visible=v=>{
                                const r=v.getBoundingClientRect();
                                const s=getComputedStyle(v);
                                return r.width>120 && r.height>120 &&
                                  s.display!=='none' && s.visibility!=='hidden';
                              };
                              const videos=[...document.querySelectorAll('video')]
                                .filter(visible).sort((a,b)=>{
                                  const ar=a.getBoundingClientRect();
                                  const br=b.getBoundingClientRect();
                                  return br.width*br.height-ar.width*ar.height;
                                });
                              const v=videos[0];
                              if(!v) return 'no-video';
                              v.muted=false; v.autoplay=true;
                              v.play().catch(()=>{});
                              return v.currentSrc||v.src||'video-found';
                            })()"""
                        )
                    else:
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
            if method == "Network.requestWillBeSent":
                if opened_from_profile:
                    request_id = str(params.get("requestId") or "")
                    if request_id:
                        fresh_request_ids.add(request_id)
            elif method == "Network.responseReceived":
                request_id = str(params.get("requestId") or "")
                if (
                    opened_from_profile
                    and request_id not in fresh_request_ids
                ):
                    # A profile preview response may complete after the queue
                    # barrier.  It is accepted only when its request also
                    # started after the exact-page media reload.
                    continue
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
            diagnosed_url = ""
            try:
                diagnosis = self._evaluate("JSON.stringify({url:location.href,title:document.title,videoCount:document.querySelectorAll('video').length,text:(document.body&&document.body.innerText||'').slice(0,240)})")
                self.log(f"页面诊断：{diagnosis}")
                diagnosed_url = str(
                    json.loads(str(diagnosis or "{}")).get("url") or ""
                )
            except Exception:
                pass
            if (
                expected_id
                and douyin_work_id_from_url(diagnosed_url) != expected_id
            ):
                raise ParseError(
                    "抖音没有打开精确目标作品页，已拒绝精选页或推荐页媒体；"
                    "现有文件保持不变。"
                )
            raise ParseError("解析超时：没有捕获到可下载的作品 MP4。请确认作品公开且链接有效。")

        # Obtain page metadata and the temporary browser session before probing URLs.
        try:
            page_meta = self._evaluate("JSON.stringify({url:location.href,title:document.title,description:(document.querySelector('meta[name=description]')||{}).content||'',og:(document.querySelector('meta[property=\"og:title\"]')||{}).content||''})")
            meta = json.loads(page_meta or "{}")
            final_url = meta.get("url") or final_url
        except Exception:
            meta = {}
        final_work_id = douyin_work_id_from_url(final_url)
        if expected_id and final_work_id != expected_id:
            raise ParseError(
                "抖音最终页面不是精确目标作品，已拒绝精选页或推荐页媒体；"
                "现有文件保持不变。"
            )
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

        video_id = final_work_id
        if not video_id:
            id_match = re.search(
                r"(?:aweme_id|item_id)[=/](\d{15,22})",
                best.url,
            )
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
        self.log("正在打开公开图文页并读取正文和原图列表…")

        deadline = time.monotonic() + timeout
        response_requests: set[str] = set()
        image_urls: list[str] = []
        description = ""
        resolved_title = ""
        article_seen = False
        final_url = share_url
        last_page_probe = 0.0
        while time.monotonic() < deadline:
            self._check_cancel()
            if time.monotonic() - last_page_probe > 1:
                last_page_probe = time.monotonic()
                page_result = self._image_meta_from_page(work_id)
                if page_result:
                    description = (
                        _image_description_from_aweme(page_result)
                        or description
                    )
                    resolved_title = (
                        _image_title_from_aweme(page_result)
                        or resolved_title
                    )
                    article_seen = bool(_article_info_from_aweme(page_result))
                    image_urls = _image_urls_from_aweme(page_result) or image_urls
            if image_urls and (description or not article_seen):
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
                    description = (
                        _image_description_from_aweme(aweme)
                        or description
                    )
                    resolved_title = (
                        _image_title_from_aweme(aweme)
                        or resolved_title
                    )
                    article_seen = bool(_article_info_from_aweme(aweme))
                    image_urls = _image_urls_from_aweme(aweme)

        if not image_urls or (article_seen and not description):
            raise ParseError("图文解析超时：没有取得作品正文和原图，请稍后重试。")
        cookie_rows = self.cdp.call("Network.getAllCookies").get("cookies", [])
        cookies = {row["name"]: row["value"] for row in cookie_rows if row.get("name")}
        user_agent = str(self._evaluate("navigator.userAgent") or "Mozilla/5.0")
        try:
            page_title = str(self._evaluate("document.title") or "")
        except Exception:
            page_title = ""
        fallback = page_title.split(" - 抖音")[0].strip() or f"抖音图文_{work_id}"
        title = resolved_title or _image_title(description, fallback)
        self.log(
            f"图文作品 {work_id} 已识别正文和 {len(image_urls)} 张原图。"
        )
        return ImageParseResult(
            share_url=share_url,
            final_page_url=final_url,
            work_id=work_id,
            title=title,
            description=description,
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
            const article=value.article_info;
            if(id===target && ((Array.isArray(images) && images.length) || article)){{
              const video=value.video||{{}};
              return JSON.stringify({{
                aweme_id:id,
                desc:value.desc||value.title||'',
                images,
                article_info:article||null,
                video:{{origin_cover:video.origin_cover||null,cover:video.cover||null}}
              }});
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
        self.debug_port = None
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


def _image_title(description: str, fallback: str) -> str:
    for line in str(description or "").splitlines():
        cleaned = " ".join(line.split()).strip()
        if cleaned:
            return cleaned[:120]
    return str(fallback or "抖音图文").strip()[:120]


def _json_mapping(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _article_info_from_aweme(aweme: dict) -> dict:
    value = aweme.get("article_info") or aweme.get("articleInfo") or {}
    return value if isinstance(value, dict) else {}


def _article_content_from_aweme(aweme: dict) -> dict:
    article_info = _article_info_from_aweme(aweme)
    return _json_mapping(
        article_info.get("article_content")
        or article_info.get("articleContent")
    )


def _image_description_from_aweme(aweme: dict) -> str:
    article_info = _article_info_from_aweme(aweme)
    if article_info:
        content = _article_content_from_aweme(aweme)
        fe_data = _json_mapping(article_info.get("fe_data"))
        raw_content = article_info.get("article_content")
        values = (
            content.get("markdown"),
            content.get("long_article_abstract"),
            fe_data.get("description"),
            raw_content if isinstance(raw_content, str) and not content else "",
            aweme.get("desc"),
            aweme.get("title"),
        )
    else:
        values = (aweme.get("desc"), aweme.get("title"))
    return next(
        (
            str(value).strip()
            for value in values
            if isinstance(value, (str, int, float)) and str(value).strip()
        ),
        "",
    )


def _image_title_from_aweme(aweme: dict) -> str:
    article_info = _article_info_from_aweme(aweme)
    values = (
        article_info.get("article_title"),
        article_info.get("articleTitle"),
        aweme.get("item_title"),
        aweme.get("title"),
    )
    return next(
        (
            " ".join(str(value).split()).strip()[:120]
            for value in values
            if isinstance(value, (str, int, float)) and str(value).strip()
        ),
        "",
    )


def _article_image_entries(aweme: dict) -> list[object]:
    article_info = _article_info_from_aweme(aweme)
    if not article_info:
        return []
    content = _article_content_from_aweme(aweme)
    fe_data = _json_mapping(article_info.get("fe_data"))
    for container in (article_info, content, fe_data):
        for key in ("images", "image_list", "imageList"):
            values = container.get(key)
            if isinstance(values, list) and values:
                return list(values)

    markdown = str(content.get("markdown") or "")
    markdown_urls = re.findall(
        r"!\[[^\]]*\]\(\s*<?(https?://[^\s)>]+)>?(?:\s+[^)]*)?\)",
        markdown,
        flags=re.I,
    )
    if markdown_urls:
        return [{"url_list": [url]} for url in markdown_urls]

    # Text-only long articles still expose their public square origin cover.
    # Keeping that platform-provided cover lets the existing private gallery
    # transport carry the complete article text without fabricating an image.
    video = aweme.get("video") or {}
    if isinstance(video, dict):
        for key in ("origin_cover", "cover"):
            cover = video.get(key)
            if isinstance(cover, dict) and _image_urls_from_entry(cover):
                return [cover]
    return []


def _image_urls_from_entry(image: object) -> list[str]:
    if isinstance(image, str):
        return [image] if image.startswith(("http://", "https://")) else []
    if not isinstance(image, dict):
        return []
    candidates: list[str] = []
    for key in ("download_url_list", "url_list"):
        values = image.get(key) or []
        if isinstance(values, list):
            candidates.extend(
                str(item)
                for item in values
                if str(item).startswith(("http://", "https://"))
            )
    for key in ("download_url", "url", "src", "image_url"):
        value = str(image.get(key) or "")
        if value.startswith(("http://", "https://")):
            candidates.append(value)
    for nested_key in (
        "display_image",
        "owner_watermark_image",
        "thumbnail",
        "origin_image",
    ):
        nested = image.get(nested_key)
        if isinstance(nested, dict) and nested:
            candidates.extend(_image_urls_from_entry(nested))
    return list(dict.fromkeys(candidates))


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
            if current_id == work_id and (
                _image_urls_from_aweme(current)
                or (
                    _article_info_from_aweme(current)
                    and _image_description_from_aweme(current)
                )
            ):
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
        or _article_image_entries(aweme)
        or []
    )
    urls: list[str] = []
    for image in images:
        candidates = _image_urls_from_entry(image)
        if candidates and candidates[0] not in urls:
            urls.append(candidates[0])
    return urls


def image_mime_type(path: Path) -> str:
    try:
        with Path(path).open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return ""
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return ""


def download_images(
    result: ImageParseResult,
    destination: Path,
    custom_prefix: str,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    cancel_event = cancel_event or threading.Event()
    prefix = sanitize_filename(custom_prefix, fallback=result.work_id)
    headers = {
        "User-Agent": result.user_agent,
        "Referer": result.referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    outputs: list[Path] = []
    created_outputs: list[Path] = []
    try:
        for index, url in enumerate(result.image_urls, start=1):
            if cancel_event.is_set():
                raise DownloadCancelled("下载已取消。")
            existing = next(
                (
                    path
                    for path in destination.glob(f"{prefix}_{index:02}.*")
                    if path.is_file()
                    and path.stat().st_size > 1024
                    and image_mime_type(path)
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
                        if cancel_event.is_set():
                            raise DownloadCancelled("下载已取消。")
                        if chunk:
                            handle.write(chunk)
                detected_mime = image_mime_type(temporary)
                if temporary.stat().st_size <= 1024 or not detected_mime:
                    temporary.unlink(missing_ok=True)
                    raise ParseError(f"第 {index} 张原图不是有效的 JPEG、PNG 或 WebP。")
                actual_extension = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                }[detected_mime]
                output = destination / f"{prefix}_{index:02}{actual_extension}"
                temporary.replace(output)
                outputs.append(output)
                created_outputs.append(output)
    except Exception:
        for temporary in destination.glob(f"{prefix}_*.download"):
            temporary.unlink(missing_ok=True)
        for output in created_outputs:
            output.unlink(missing_ok=True)
        raise
    return outputs


def download_video(
    result: ParseResult,
    destination: Path,
    progress: Callable[[int, int, float], None] | None = None,
    cancel_event: threading.Event | None = None,
    custom_filename: str | None = None,
    *,
    source_quality: bool = False,
    minimum_video_dimensions: tuple[int, int] | None = None,
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
        separate_audio: Path | None = None
        if not inspection["has_audio"]:
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
            separate_audio = audio_part
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            purpose = "高清原视频" if source_quality else "手机轻量视频"
            raise ParseError(f"服务器缺少 ffmpeg，无法生成{purpose}。")
        source_video_streams: dict[str, dict[str, object]] = {}
        source_audio_streams: dict[str, dict[str, object]] = {}
        if source_quality:
            source_video_streams = probe_media_streams(part)
            source_audio_streams = (
                probe_media_streams(separate_audio)
                if separate_audio is not None
                else source_video_streams
            )
            source_video = source_video_streams.get("video", {})
            source_audio = source_audio_streams.get("audio", {})
            if not (
                source_video.get("codec_name")
                and int(source_video.get("width") or 0) > 0
                and int(source_video.get("height") or 0) > 0
                and source_video.get("pix_fmt")
                and source_audio.get("codec_name")
            ):
                raise ParseError(
                    "最高画质候选的编解码器、像素格式或分辨率不完整，"
                    "已拒绝替换现有文件。"
                )
        command = (
            source_quality_video_command(
                ffmpeg,
                part,
                mux_part,
                audio_input=separate_audio,
            )
            if source_quality
            else compact_video_command(
                ffmpeg,
                part,
                mux_part,
                audio_input=separate_audio,
            )
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "未知错误").strip()[-500:]
            purpose = "高清原视频无损封装" if source_quality else "手机轻量视频生成"
            raise ParseError(f"{purpose}失败：{detail}")
        merged = inspect_mp4(mux_part)
        if not merged["has_video"] or not merged["has_audio"]:
            purpose = "高清 MP4" if source_quality else "轻量 MP4"
            raise ParseError(f"{purpose} 未同时包含画面和声音。")
        if source_quality:
            output_streams = probe_media_streams(mux_part)
            source_video = source_video_streams["video"]
            source_audio = source_audio_streams["audio"]
            output_video = output_streams.get("video", {})
            output_audio = output_streams.get("audio", {})
            video_fields = ("codec_name", "width", "height", "pix_fmt")
            if any(
                output_video.get(field) != source_video.get(field)
                for field in video_fields
            ):
                raise ParseError(
                    "高清封装改变了源视频编解码器、像素格式或分辨率，"
                    "已拒绝替换现有文件。"
                )
            if output_audio.get("codec_name") != source_audio.get("codec_name"):
                raise ParseError(
                    "高清封装改变了源音频编解码器，已拒绝替换现有文件。"
                )
            replacement_dimensions = (
                int(output_video.get("width") or 0),
                int(output_video.get("height") or 0),
            )
            if minimum_video_dimensions is not None and not source_quality_improves(
                minimum_video_dimensions,
                replacement_dimensions,
            ):
                old_width, old_height = minimum_video_dimensions
                new_width, new_height = replacement_dimensions
                raise ParseError(
                    "最高画质候选没有高于现有压缩版："
                    f"现有 {old_width}x{old_height}，候选 {new_width}x{new_height}；"
                    "已保留现有文件。"
                )
            if not bool(merged["fast_start"]):
                raise ParseError("高清 MP4 未生成网页快启索引，已拒绝替换现有文件。")
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
