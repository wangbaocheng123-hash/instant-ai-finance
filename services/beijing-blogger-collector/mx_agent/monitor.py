from __future__ import annotations

import os
import time
from typing import Any

from .analysis import VideoIntelligenceAgent
from .douyin import DouyinAPIError, DouyinOfficialSource, normalize_official_comment
from .settings import Settings
from .storage import Storage


class MonitorService:
    def __init__(self, settings: Settings, storage: Storage, analyzer: VideoIntelligenceAgent):
        self.settings = settings
        self.storage = storage
        self.analyzer = analyzer
        self.source = DouyinOfficialSource(
            access_token=os.getenv("DOUYIN_ACCESS_TOKEN"),
            open_id=os.getenv("DOUYIN_OPEN_ID"),
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.source.status(),
            "state": self.storage.get_source_state(self.source.name),
        }

    def check_once(self) -> dict[str, Any]:
        run_id = self.storage.start_run(
            "monitor",
            {
                "source": self.source.name,
                "source_configured": self.source.is_configured(),
            },
        )
        try:
            if not self.source.is_configured():
                output = {
                    "fetched": 0,
                    "created_ids": [],
                    "existing_ids": [],
                    "analysis_ids": [],
                    "comments_created": 0,
                    "comments_updated": 0,
                    "message": "未配置抖音官方授权。请在 .env 中配置 DOUYIN_ACCESS_TOKEN，并确认已申请 video.list 和评论权限。",
                }
                self.storage.save_source_state(self.source.name, metadata={"status": "not_configured"})
                self.storage.finish_run(run_id, "blocked", output)
                return output

            created_ids: list[int] = []
            existing_ids: list[int] = []
            analysis_ids: list[int] = []
            comments_created = 0
            comments_updated = 0
            fetched = 0
            cursor: str | int | None = 0
            max_pages = int(os.getenv("MX_AGENT_SYNC_MAX_PAGES", "5"))
            last_item_at: str | None = None

            for _ in range(max_pages):
                page = self.source.fetch_videos(cursor=cursor)
                items = page["items"]
                fetched += len(items)
                for item in items:
                    video_id, created = self.storage.upsert_video(item)
                    last_item_at = item.get("published_at") or last_item_at
                    if created:
                        created_ids.append(video_id)
                        if self.settings.auto_analyze_new_videos:
                            analysis = self.analyzer.analyze_video(video_id)
                            analysis_ids.append(int(analysis["analysis_id"]))
                    else:
                        existing_ids.append(video_id)

                    raw = item.get("raw_json", {})
                    item_id = raw.get("item_id") or item.get("source_video_id")
                    comment_result = self._sync_comments_for_video(video_id, str(item_id))
                    comments_created += comment_result["created"]
                    comments_updated += comment_result["updated"]

                cursor = page.get("cursor")
                if not page.get("has_more") or not items:
                    break

            output = {
                "fetched": fetched,
                "created_ids": created_ids,
                "existing_ids": existing_ids,
                "analysis_ids": analysis_ids,
                "comments_created": comments_created,
                "comments_updated": comments_updated,
                "message": "抖音官方同步完成。",
            }
            self.storage.save_source_state(
                self.source.name,
                cursor=cursor,
                last_item_at=last_item_at,
                metadata={"status": "ok", **output},
            )
            self.storage.finish_run(run_id, "succeeded", output)
            return output
        except DouyinAPIError as exc:
            output = {
                "fetched": 0,
                "created_ids": [],
                "existing_ids": [],
                "analysis_ids": [],
                "comments_created": 0,
                "comments_updated": 0,
                "message": str(exc),
            }
            self.storage.save_source_state(
                self.source.name,
                metadata={"status": "error", "message": str(exc)},
            )
            self.storage.finish_run(run_id, "blocked", output)
            return output
        except Exception as exc:
            self.storage.save_source_state(
                self.source.name,
                metadata={"status": "error", "message": str(exc)},
            )
            self.storage.finish_run(run_id, "failed", error=str(exc))
            raise

    def _sync_comments_for_video(self, video_id: int, item_id: str) -> dict[str, int]:
        try:
            page = self.source.fetch_comments(item_id=item_id, cursor=0)
        except DouyinAPIError as exc:
            return {"created": 0, "updated": 0, "error": str(exc)}
        created_count = 0
        updated_count = 0
        for raw_comment in page.get("items", []):
            comment = normalize_official_comment(video_id, raw_comment)
            _, created = self.storage.upsert_comment(comment)
            if created:
                created_count += 1
            else:
                updated_count += 1
        return {"created": created_count, "updated": updated_count}

    def run_forever(self) -> None:
        while True:
            self.check_once()
            time.sleep(self.settings.monitor_interval_seconds)
