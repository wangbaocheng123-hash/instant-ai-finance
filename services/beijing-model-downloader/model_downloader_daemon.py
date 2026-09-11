from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import threading
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from comment_collector import CommentCollectError, CommentCollector
from douyin_core import (
    DouyinResolver,
    DownloadCancelled,
    ParseError,
    download_video,
    inspect_mp4,
)
from library_store import LibraryStore
from profile_monitor import ProfileScanError, ProfileScanner, ProfileVideo


APP_NAME = "模型下载器云端版"
TIMEZONE = ZoneInfo("Asia/Shanghai")
VIDEO_CHECK_INTERVAL_MINUTES = 3
VIDEO_DEADLINE_GUARD_SECONDS = 8
MAINTENANCE_MINIMUM_SECONDS = 20
COMMENT_SLICE_MAX_SECONDS = 35
DEFAULT_PROFILE_URL = (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAAK713M9d8PGNb_WiMYf7yKhOI5y60H4uELJK2guDjJT0"
    "?from_tab_name=main"
)

BASE_DIR = Path(
    os.environ.get("MODEL_DOWNLOADER_DATA", "/var/lib/model-downloader")
)
DOWNLOAD_ROOT = Path(
    os.environ.get("MODEL_DOWNLOADER_DOWNLOADS", "/srv/model-downloader/videos")
)
COMMENTS_ROOT = Path(
    os.environ.get("MODEL_DOWNLOADER_COMMENTS", "/srv/model-downloader/comments")
)
DATABASE_FILE = Path(
    os.environ.get(
        "MODEL_DOWNLOADER_DATABASE",
        "/var/lib/model-downloader/library.sqlite3",
    )
)
BROWSER_PROFILE = BASE_DIR / "chrome-profile"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "logs" / "model-downloader.log"
PROFILE_URL = os.environ.get("MODEL_DOWNLOADER_PROFILE_URL", DEFAULT_PROFILE_URL)
_PROFILE_UID_MATCH = re.search(r"/user/([^/?#]+)", PROFILE_URL)
CREATOR_UID = os.environ.get(
    "MODEL_DOWNLOADER_CREATOR_UID",
    _PROFILE_UID_MATCH.group(1) if _PROFILE_UID_MATCH else "",
)
INTERVAL_MINUTES = VIDEO_CHECK_INTERVAL_MINUTES
COMMENTS_ENABLED = os.environ.get(
    "MODEL_DOWNLOADER_COMMENTS_ENABLED",
    "1",
).strip().lower() not in {"0", "false", "no", "off"}
COMMENT_LIMIT = max(
    20,
    min(
        50000,
        int(os.environ.get("MODEL_DOWNLOADER_COMMENT_LIMIT", "10000")),
    ),
)
COMMENT_YOUNG_WINDOW_HOURS = 24
COMMENT_YOUNG_REFRESH_MINUTES = max(
    1,
    int(
        os.environ.get(
            "MODEL_DOWNLOADER_COMMENT_YOUNG_REFRESH_MINUTES",
            "15",
        )
    ),
)
COMMENT_MATURE_REFRESH_MINUTES = max(
    COMMENT_YOUNG_REFRESH_MINUTES,
    int(
        os.environ.get(
            "MODEL_DOWNLOADER_COMMENT_MATURE_REFRESH_MINUTES",
            os.environ.get("MODEL_DOWNLOADER_COMMENT_REFRESH_MINUTES", "60"),
        )
    ),
)


def now_china() -> datetime:
    return datetime.now(TIMEZONE)


def comment_refresh_interval_minutes(
    published_at: datetime,
    moment: datetime,
) -> int:
    published = published_at
    current = moment
    if published.tzinfo is None and current.tzinfo is not None:
        published = published.replace(tzinfo=current.tzinfo)
    elif published.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=published.tzinfo)
    if current - published < timedelta(hours=COMMENT_YOUNG_WINDOW_HOURS):
        return COMMENT_YOUNG_REFRESH_MINUTES
    return COMMENT_MATURE_REFRESH_MINUTES


def next_scheduled_check(
    scan_started_at: datetime,
    completed_at: datetime | None = None,
) -> datetime:
    """Keep the next video scan anchored to the previous scan start.

    A slow profile scan or a new-video download must never add another full
    interval of sleep.  If video work already overran the deadline, the next
    scan is due immediately after that work finishes.
    """
    deadline = scan_started_at + timedelta(minutes=INTERVAL_MINUTES)
    if completed_at is not None and completed_at > deadline:
        return completed_at
    return deadline


def load_state() -> set[str]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    if value.get("date") != now_china().date().isoformat():
        return set()
    return {
        str(video_id)
        for video_id in value.get("downloaded_ids", [])
        if str(video_id).isdigit()
    }


def save_state(downloaded_ids: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "date": now_china().date().isoformat(),
        "downloaded_ids": sorted(downloaded_ids, key=int, reverse=True),
        "updated_at": now_china().isoformat(timespec="seconds"),
    }
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_FILE)


def setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("model-downloader")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.handlers[:] = [stream, file_handler]
    return logger


class CloudMonitor:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.maintenance_cancel_event = threading.Event()
        self.downloaded_ids = load_state()
        self.library = LibraryStore(DATABASE_FILE, COMMENTS_ROOT)
        self.consecutive_scan_failures = 0

    def log(self, message: str, *args: object) -> None:
        self.logger.info(message, *args)

    def stop(self, *_args) -> None:
        self.log("收到停止信号，正在安全结束当前任务。")
        self.cancel_event.set()
        self.maintenance_cancel_event.set()
        self.stop_event.set()

    @staticmethod
    def today_rows(videos: list[ProfileVideo]) -> list[ProfileVideo]:
        today = now_china().date()
        rows = [
            row
            for row in videos
            if row.created_at.date() == today
        ]
        return sorted(rows, key=lambda row: int(row.video_id))

    def today_destination(self) -> Path:
        destination = DOWNLOAD_ROOT / now_china().date().isoformat()
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    def existing_file(
        self, destination: Path, video_id: str
    ) -> Path | None:
        existing = next(
            destination.glob(f"*_{video_id}.mp4"),
            None,
        )
        if existing and existing.stat().st_size > 1024:
            return existing
        return None

    def download_one(
        self,
        video: ProfileVideo,
        destination: Path,
        *,
        force: bool = False,
        known_file: Path | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Path:
        active_cancel_event = (
            cancel_event if cancel_event is not None else self.cancel_event
        )
        existing = self.existing_file(destination, video.video_id)
        if known_file and known_file.is_file():
            existing = known_file
        if existing and not force:
            self.log(f"作品 {video.video_id} 已存在，跳过重复下载。")
            inspection = inspect_mp4(existing)
            if not inspection["has_video"] or not inspection["has_audio"]:
                self.log(
                    "作品 %s 缺少画面或声音，自动进入修复下载。",
                    video.video_id,
                )
                force = True
            else:
                self.library.mark_downloaded(
                    video_id=video.video_id,
                    title=video.title,
                    file_path=existing,
                    file_size=int(inspection["size"]),
                    duration_seconds=inspection["duration_seconds"],
                    downloaded_at=now_china(),
                )
                return existing

        backup: Path | None = None
        if existing and force:
            backup = existing.with_name(
                f".{existing.name}.repair-backup"
            )
            backup.unlink(missing_ok=True)
            existing.replace(backup)

        try:
            output = self._download_fresh(
                video,
                destination,
                cancel_event=active_cancel_event,
            )
        except Exception:
            if backup and backup.exists():
                existing.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(existing)
            raise
        else:
            if backup:
                backup.unlink(missing_ok=True)
            return output

    def _download_fresh(
        self,
        video: ProfileVideo,
        destination: Path,
        *,
        cancel_event: threading.Event,
    ) -> Path:
        self.log(f"发现作品 {video.video_id}，开始解析并下载。")
        resolver = DouyinResolver(
            log=self.log,
            cancel_event=cancel_event,
            profile_dir=BROWSER_PROFILE,
        )
        try:
            result = resolver.resolve(video.url, timeout=50)
        finally:
            resolver.close()

        if video.title and not video.title.startswith("抖音作品_"):
            result.title = video.title
        # Keep the server file, browser download and database entry directly
        # matchable without relying on a mutable title.
        custom_name = f"{video.created_at:%Y%m%d_%H%M}_{video.video_id}"
        output = download_video(
            result,
            destination,
            cancel_event=cancel_event,
            custom_filename=custom_name,
        )
        inspection = inspect_mp4(output)
        if not (
            inspection["has_mdat"]
            and inspection["has_video"]
            and inspection["has_audio"]
        ):
            output.unlink(missing_ok=True)
            raise ParseError(
                "下载文件未同时包含画面和声音，已删除并等待重试。"
            )
        duration_text = (
            f"{inspection['duration_seconds']:.1f}"
            if inspection["duration_seconds"] is not None
            else "未知"
        )
        self.log(
            f"下载完成：{output}（{inspection['size']} 字节，"
            f"时长 {duration_text} 秒，已确认包含声音）"
        )
        self.library.mark_downloaded(
            video_id=video.video_id,
            title=result.title,
            file_path=output,
            file_size=int(inspection["size"]),
            duration_seconds=inspection["duration_seconds"],
            downloaded_at=now_china(),
        )
        return output

    def collect_comments(
        self,
        video: ProfileVideo,
        *,
        timeout: float = 180,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if not COMMENTS_ENABLED:
            return
        active_cancel_event = (
            cancel_event
            if cancel_event is not None
            else self.cancel_event
        )
        collected_at = now_china()
        refresh_minutes = comment_refresh_interval_minutes(
            video.created_at,
            collected_at,
        )
        if not self.library.should_refresh_comments(
            video.video_id,
            collected_at,
            refresh_minutes,
        ):
            return
        collector = CommentCollector(
            BROWSER_PROFILE,
            log=self.log,
            cancel_event=active_cancel_event,
        )
        try:
            comments = collector.collect(
                video.url,
                timeout=timeout,
                limit=COMMENT_LIMIT,
                creator_uid=CREATOR_UID,
            )
            collection_summary = dict(collector.last_summary)
        finally:
            collector.close()
        total = self.library.save_comments(
            video_id=video.video_id,
            published_date=video.created_at.date().isoformat(),
            comments=comments,
            collected_at=collected_at,
            summary=collection_summary,
        )
        self.log(
            "作品 %s 的评论库现有 %d 条记录。",
            video.video_id,
            total,
        )
        self.log(
            "评论互动识别：模型先生本人 %d 条（其中二级回复 %d 条），"
            "作者点赞 %d 条，主动展开 %d 次；%s。",
            int(collection_summary.get("creator_rows") or 0),
            int(collection_summary.get("creator_reply_rows") or 0),
            int(collection_summary.get("author_liked_rows") or 0),
            int(collection_summary.get("expand_clicks") or 0),
            (
                "本轮分页完整"
                if collection_summary.get("complete")
                else "本轮仍有平台未展开内容，下一小时继续累计"
            ),
        )

    @staticmethod
    def _stored_video(row) -> ProfileVideo:
        published = datetime.fromisoformat(str(row["published_at"]))
        if published.tzinfo is None:
            published = published.replace(tzinfo=TIMEZONE)
        return ProfileVideo(
            video_id=str(row["video_id"]),
            url=str(row["source_url"]),
            title=str(row["title"] or f"抖音作品_{row['video_id']}"),
            created_at=published,
        )

    def _comment_candidates(self):
        candidates = self.library.comment_refresh_candidates(
            now=now_china(),
            young_window_hours=COMMENT_YOUNG_WINDOW_HOURS,
            young_refresh_minutes=COMMENT_YOUNG_REFRESH_MINUTES,
            mature_refresh_minutes=COMMENT_MATURE_REFRESH_MINUTES,
        )
        # Manual requests are explicitly user-triggered maintenance and keep
        # priority over routine refreshes, while all maintenance remains below
        # the next-video deadline.
        return sorted(
            candidates,
            key=lambda row: not bool(row["comment_refresh_requested"]),
        )

    def _maintenance_budget(self, deadline: datetime) -> float:
        return max(0.0, (deadline - now_china()).total_seconds())

    def _arm_maintenance_deadline(
        self,
        deadline: datetime,
    ) -> threading.Timer:
        self.maintenance_cancel_event.clear()
        delay = max(
            0.01,
            self._maintenance_budget(deadline)
            - VIDEO_DEADLINE_GUARD_SECONDS,
        )
        timer = threading.Timer(delay, self.maintenance_cancel_event.set)
        timer.daemon = True
        timer.start()
        return timer

    def _repair_one(self, row, deadline: datetime) -> None:
        video = self._stored_video(row)
        destination = DOWNLOAD_ROOT / video.created_at.date().isoformat()
        destination.mkdir(parents=True, exist_ok=True)
        raw_path = str(row["file_path"] or "")
        known_file = Path(raw_path) if raw_path else None
        timer = self._arm_maintenance_deadline(deadline)
        try:
            self.download_one(
                video,
                destination,
                force=True,
                known_file=known_file,
                cancel_event=self.maintenance_cancel_event,
            )
        except DownloadCancelled:
            if self.stop_event.is_set():
                raise
            self.log(
                "作品 %s 的修复任务已暂停，为下一次新视频检查让路。",
                video.video_id,
            )
        except Exception:
            self.library.mark_repair_failed(video.video_id, now_china())
            self.logger.exception(
                "作品 %s 的音视频修复失败，可在管理页面再次重试。",
                video.video_id,
            )
        finally:
            timer.cancel()
            self.maintenance_cancel_event.clear()

    def _refresh_one_comment(self, row, deadline: datetime) -> None:
        video = self._stored_video(row)
        remaining = self._maintenance_budget(deadline)
        timeout = min(
            COMMENT_SLICE_MAX_SECONDS,
            max(
                1.0,
                remaining - VIDEO_DEADLINE_GUARD_SECONDS - 5,
            ),
        )
        timer = self._arm_maintenance_deadline(deadline)
        try:
            self.collect_comments(
                video,
                timeout=timeout,
                cancel_event=self.maintenance_cancel_event,
            )
        except DownloadCancelled:
            if self.stop_event.is_set():
                raise
            self.log(
                "作品 %s 的评论任务已暂停，为下一次新视频检查让路。",
                video.video_id,
            )
        except CommentCollectError:
            self.logger.exception(
                "作品 %s 评论采集失败，稍后自动重试。",
                video.video_id,
            )
        except Exception:
            self.logger.exception(
                "作品 %s 评论数据保存失败。",
                video.video_id,
            )
        finally:
            timer.cancel()
            self.maintenance_cancel_event.clear()

    def run_one_maintenance_task(self, deadline: datetime) -> bool:
        """Use one idle slice, but never delay the next video scan."""
        if self._maintenance_budget(deadline) < MAINTENANCE_MINIMUM_SECONDS:
            return False
        repairs = self.library.repair_requests()
        if repairs:
            self._repair_one(repairs[0], deadline)
            return True
        if not COMMENTS_ENABLED:
            return False
        candidates = self._comment_candidates()
        if not candidates:
            return False
        self._refresh_one_comment(candidates[0], deadline)
        return True

    def check_once(self) -> None:
        self.cancel_event.clear()
        run_id = self.library.start_scan(now_china())
        visible_today = 0
        downloaded = 0
        message = ""
        today = now_china().date()
        self.downloaded_ids = {
            video_id
            for video_id in self.downloaded_ids
            if (int(video_id) >> 32)
            and datetime.fromtimestamp(
                int(video_id) >> 32, TIMEZONE
            ).date()
            == today
        }

        try:
            scanner = ProfileScanner(
                BROWSER_PROFILE,
                log=self.log,
                cancel_event=self.cancel_event,
            )
            try:
                creator, videos = scanner.scan(PROFILE_URL, timeout=45)
            finally:
                scanner.close()

            rows = self.today_rows(videos)
            visible_today = len(rows)
            destination = self.today_destination()
            pending: list[ProfileVideo] = []
            for row in rows:
                accepted = self.library.upsert_video(
                    video_id=row.video_id,
                    creator=creator,
                    title=row.title,
                    source_url=row.url,
                    published_at=row.created_at,
                    now=now_china(),
                )
                if not accepted:
                    self.log(
                        "作品 %s 已被手动删除，跳过且不再重复下载。",
                        row.video_id,
                    )
                    continue
                existing = self.existing_file(destination, row.video_id)
                if existing:
                    inspection = inspect_mp4(existing)
                    if inspection["has_video"] and inspection["has_audio"]:
                        self.library.mark_downloaded(
                            video_id=row.video_id,
                            title=row.title,
                            file_path=existing,
                            file_size=int(inspection["size"]),
                            duration_seconds=inspection["duration_seconds"],
                            downloaded_at=now_china(),
                        )
                        self.downloaded_ids.add(row.video_id)
                        continue
                    self.log(
                        "作品 %s 的现有文件缺少声音，准备自动修复。",
                        row.video_id,
                    )
                self.downloaded_ids.discard(row.video_id)
                pending.append(row)

            if not rows:
                self.log(f"{creator}今天暂未发布作品；以前作品已忽略。")
            elif not pending:
                self.log(f"今天发现 {len(rows)} 个作品，均已下载。")

            for row in pending:
                if self.stop_event.is_set():
                    raise DownloadCancelled("服务正在停止。")
                try:
                    self.download_one(row, destination)
                except Exception:
                    self.library.mark_download_failed(
                        row.video_id,
                        now_china(),
                    )
                    raise
                self.downloaded_ids.add(row.video_id)
                downloaded += 1
                save_state(self.downloaded_ids)

            save_state(self.downloaded_ids)

            message = (
                f"本次检查完成：今日发现 {len(rows)} 个，"
                f"新下载 {downloaded} 个。"
            )
            self.log(message)
        except Exception as exc:
            message = str(exc)
            self.library.finish_scan(
                run_id,
                finished_at=now_china(),
                success=False,
                visible_today=visible_today,
                downloaded=downloaded,
                message=message,
            )
            raise
        else:
            self.library.finish_scan(
                run_id,
                finished_at=now_china(),
                success=True,
                visible_today=visible_today,
                downloaded=downloaded,
                message=message,
            )

    def run(self, once: bool = False) -> int:
        self.log(
            "%s 已启动；全天 24 小时持续检查新视频；"
            "新视频硬优先、从每轮开始起固定间隔 %d 分钟；"
            "评论和修复仅使用检查空档并到点让路；"
            "评论刷新按发布时间分级：前 %d 小时每 %d 分钟，"
            "之后每 %d 分钟。",
            APP_NAME,
            INTERVAL_MINUTES,
            COMMENT_YOUNG_WINDOW_HOURS,
            COMMENT_YOUNG_REFRESH_MINUTES,
            COMMENT_MATURE_REFRESH_MINUTES,
        )
        if once:
            try:
                self.check_once()
                return 0
            except Exception:
                self.logger.exception("单次检查失败。")
                return 1

        while not self.stop_event.is_set():
            scan_started_at = now_china()
            scan_succeeded = False
            try:
                self.check_once()
                scan_succeeded = True
                self.consecutive_scan_failures = 0
            except DownloadCancelled:
                if self.stop_event.is_set():
                    break
            except (OSError, ParseError, ProfileScanError):
                self.consecutive_scan_failures += 1
                self.logger.exception("本次检查失败，下个周期自动重试。")
            except Exception:
                self.consecutive_scan_failures += 1
                self.logger.exception("未预期错误，下个周期自动重试。")

            if self.consecutive_scan_failures >= 3:
                self.logger.critical(
                    "新视频主页已连续检查失败 %d 次；评论和修复继续停让，"
                    "下一周期仍优先重试视频。",
                    self.consecutive_scan_failures,
                )

            next_check = next_scheduled_check(
                scan_started_at,
                now_china(),
            )
            self.log(
                "下次新视频检查时间：%s。",
                next_check.strftime("%m-%d %H:%M:%S"),
            )
            if scan_succeeded and not self.stop_event.is_set():
                try:
                    self.run_one_maintenance_task(next_check)
                except DownloadCancelled:
                    if self.stop_event.is_set():
                        break
                except Exception:
                    self.logger.exception(
                        "低优先级维护任务失败；不影响下一次新视频检查。"
                    )
            self.stop_event.wait(
                max(0.0, (next_check - now_china()).total_seconds())
            )
        self.log("模型下载器云端服务已停止。")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--once",
        action="store_true",
        help="立即检查一次后退出",
    )
    args = parser.parse_args()
    logger = setup_logging()
    monitor = CloudMonitor(logger)
    signal.signal(signal.SIGTERM, monitor.stop)
    signal.signal(signal.SIGINT, monitor.stop)
    return monitor.run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
