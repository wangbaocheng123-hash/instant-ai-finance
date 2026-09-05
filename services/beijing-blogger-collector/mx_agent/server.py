from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .analysis import VideoIntelligenceAgent
from .auto_transcription import AutoDoubaoTranscriptionService
from .agent_settings import public_agent_settings, update_agent_settings
from .chat import DEFAULT_CHAT_MODEL, LocalChatService
from .comment_sync import DouyinCommentSyncService
from .comment_vision import CommentRecognitionService
from .cover_title import CoverTitleRecognizer
from .creator_paths import ensure_creator_directories, rename_creator_directory
from .creators import CreatorRegistry, CreatorSyncManager
from .download_watcher import DownloadWatcher
from .doubao import DoubaoFoundationService
from .douyin import comment_signal, normalize_douyin_webhook, normalize_manual_video
from .importer import DownloadImportService
from .investment_thoughts import InvestmentThoughtService
from .keywords import DEFAULT_KEYWORD_MODEL, KeywordExtractionService
from .keyword_taxonomy import (
    KEYWORD_SCHEMA_VERSION,
    flatten_keyword_categories,
    normalize_keyword_categories,
)
from .mainlines import InvestmentMainlineService
from .monitor import MonitorService
from .optimizer import OptimizationLoop
from .roles import ApplicationRole, require_role
from .settings import (
    ARCHIVE_DIR,
    DATA_DIR,
    ROOT_DIR,
    WEB_DIR,
    load_settings,
    doubao_api_is_configured,
    doubao_api_is_enabled,
    openai_api_is_enabled,
    save_doubao_api_enabled,
    save_openai_api_enabled,
)
from .storage import Storage, from_json
from .stock_mentions import StockMentionService
from .transcriber import VideoTranscriber


class AppContext:
    def __init__(self) -> None:
        # This legacy combined UI remains available only for local development.
        # Production Beijing and Singapore use their dedicated entry points.
        require_role(ApplicationRole.ALL_DEV)
        self.settings = load_settings()
        self.storage = Storage(self.settings.database_path)
        self.creators = CreatorRegistry(self.settings)
        self.doubao = DoubaoFoundationService(
            self.settings,
            self.storage,
        )
        self.mainlines = InvestmentMainlineService(self.settings, self.storage)
        self.investment_thoughts = InvestmentThoughtService(self.storage)
        self.chat = LocalChatService(self.settings)
        self.keywords = KeywordExtractionService(
            self.settings,
            self.storage,
            doubao_service=self.doubao,
        )
        self.analyzer = VideoIntelligenceAgent(self.settings, self.storage)
        self.monitor = MonitorService(self.settings, self.storage, self.analyzer)
        self.optimizer = OptimizationLoop(self.settings, self.storage)
        self.importer = DownloadImportService(self.storage)
        self.transcriber = VideoTranscriber(self.storage, self.settings)
        self.comment_recognizer = CommentRecognitionService(self.settings)
        self.browser_task_lock = threading.Lock()
        self.comment_sync = DouyinCommentSyncService(
            self.storage,
            execution_lock=self.browser_task_lock,
        )
        self.stock_mentions = StockMentionService(self.storage)
        self.cover_titles = CoverTitleRecognizer(self.storage)
        self.auto_transcription = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            self.doubao,
        )
        self.auto_transcription.start()
        self.download_watcher = DownloadWatcher(
            self.settings,
            self.storage,
            self.importer,
            self.analyzer,
            on_video_imported=self.auto_transcription.enqueue,
        )
        self.download_watcher.start()
        self.creator_sync = CreatorSyncManager(
            self.creators,
            self.storage,
            execution_lock=self.browser_task_lock,
        )
        self.creator_sync.start()


CTX = AppContext()


class Handler(BaseHTTPRequestHandler):
    server_version = "MXAgent/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/manifest.webmanifest":
            self._serve_file(
                WEB_DIR / "manifest.webmanifest",
                "application/manifest+json; charset=utf-8",
            )
        elif path == "/service-worker.js":
            self._serve_file(
                WEB_DIR / "service-worker.js",
                "text/javascript; charset=utf-8",
                extra_headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
            )
        elif path.startswith("/static/"):
            target = (WEB_DIR / path.lstrip("/")).resolve()
            if not str(target).startswith(str(WEB_DIR.resolve())):
                self._json({"error": "invalid path"}, HTTPStatus.BAD_REQUEST)
                return
            self._serve_static(target)
        elif path == "/api/status":
            self._json(
                {
                    "app_name": CTX.settings.app_name,
                    "account": CTX.settings.source_account_name,
                    "database_path": str(CTX.settings.database_path),
                    "monitor_interval_seconds": CTX.settings.monitor_interval_seconds,
                    "models": {
                        "deep": CTX.settings.deep_model,
                        "fast": CTX.settings.fast_model,
                        "reasoning_effort": CTX.settings.reasoning_effort,
                        "openai_configured": bool(CTX.settings.openai_api_key),
                        "openai_enabled": openai_api_is_enabled(CTX.settings),
                        "openai_api_switch": bool(CTX.settings.openai_api_enabled),
                        "doubao_configured": doubao_api_is_configured(
                            CTX.settings
                        ),
                        "doubao_enabled": doubao_api_is_enabled(CTX.settings),
                        "doubao_api_switch": bool(
                            CTX.settings.doubao_api_enabled
                        ),
                    },
                    "ai_providers": {
                        "domestic": CTX.doubao.status(),
                        "foreign": {
                            "provider": "openai",
                            "configured": bool(
                                CTX.settings.openai_api_key
                            ),
                            "enabled": openai_api_is_enabled(
                                CTX.settings
                            ),
                            "api_switch_enabled": bool(
                                CTX.settings.openai_api_enabled
                            ),
                        },
                    },
                    "counts": CTX.storage.counts(),
                    "douyin": CTX.monitor.status(),
                    "downloads": CTX.importer.status(),
                    "download_watcher": CTX.download_watcher.status(),
                    "auto_transcription": CTX.auto_transcription.status(),
                    "creator_sync": CTX.creator_sync.status("primary"),
                    "optimization": CTX.optimizer.feedback_summary(),
                }
            )
        elif path == "/api/chat/config":
            self._json(CTX.chat.config())
        elif path == "/api/creators":
            self._json({"items": CTX.creators.list()})
        elif path == "/api/agent-settings":
            query = parse_qs(parsed.query)
            creator_id = str(query.get("creator_id", ["primary"])[0]).strip() or "primary"
            self._json(public_agent_settings(CTX.creators.settings_for(creator_id)))
        elif path == "/api/creator-sync/status":
            query = parse_qs(parsed.query)
            creator_id = str(query.get("creator_id", ["primary"])[0]).strip() or "primary"
            self._json(CTX.creator_sync.status(creator_id))
        elif path == "/api/auto-transcription/status":
            self._json(CTX.auto_transcription.status())
        elif path == "/api/mainlines":
            self._json(CTX.mainlines.list_mainlines())
        elif path == "/api/investment-thoughts":
            query = parse_qs(parsed.query)
            category_id_value = str(query.get("category_id", [""])[0]).strip()
            self._json(
                CTX.investment_thoughts.list_library(
                    category_id=(int(category_id_value) if category_id_value else None),
                    query=str(query.get("query", [""])[0]),
                    account=str(query.get("account", [""])[0]),
                    limit=int(query.get("limit", ["100"])[0]),
                )
            )
        elif path == "/api/videos":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["50"])[0])
            account = str(query.get("account", [""])[0]).strip() or None
            items = CTX.storage.list_videos(limit=limit, account=account)
            self._json({"items": self._video_summaries_with_urls(items)})
        elif path.startswith("/api/assets/") and path.endswith("/file"):
            asset_id = int(path.split("/")[3])
            self._serve_asset_file(asset_id)
        elif self._video_child_path(path, "comments"):
            video_id = self._video_id_from_child(path)
            limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
            self._json({"items": CTX.storage.list_comments(video_id, limit=limit)})
        elif self._video_child_path(path, "stock-mentions"):
            video_id = self._video_id_from_child(path)
            limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
            self._json(CTX.stock_mentions.analyze(video_id, limit=limit))
        elif self._video_child_path(path, "assets"):
            video_id = self._video_id_from_child(path)
            self._json({"items": self._assets_with_urls(CTX.storage.list_assets(video_id))})
        elif self._video_detail_path(path):
            video_id = int(path.split("/")[3])
            detail = CTX.storage.get_video_detail(video_id)
            if not detail:
                self._json({"error": "video not found"}, HTTPStatus.NOT_FOUND)
                return
            detail["assets"] = self._assets_with_urls(detail["assets"])
            detail["keyword_info"] = self._cached_keyword_info(detail)
            detail["comment_sync"] = CTX.comment_sync.status(video_id)
            self._json(detail)
        elif path.startswith("/api/videos/") and path.endswith("/analyses"):
            video_id = int(path.split("/")[3])
            self._json({"items": CTX.storage.list_analyses(video_id)})
        elif path == "/api/runs":
            self._json({"items": CTX.storage.recent_runs()})
        elif path == "/api/downloads/recent":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["12"])[0])
            asset_type = query.get("type", ["all"])[0]
            self._json({"items": CTX.importer.recent_files(limit=limit, asset_type=asset_type)})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if self._video_child_path(path, "assets"):
                video_id = self._video_id_from_child(path)
                self._json(self._create_asset_upload(video_id), HTTPStatus.CREATED)
                return
            if (
                self._video_grandchild_path(path, "comments", "import-image")
                or self._video_grandchild_path(path, "comments", "preview-image")
            ):
                video_id = self._video_id_from_grandchild(path)
                self._json(self._preview_comments_from_images(video_id), HTTPStatus.CREATED)
                return
            if self._video_grandchild_path(path, "comments", "preview-csv"):
                video_id = self._video_id_from_grandchild(path)
                self._json(self._preview_comments_from_csv(video_id), HTTPStatus.CREATED)
                return

            payload = self._read_json()
            if path == "/api/agent-settings":
                result = self._update_creator_settings(payload)
                self._json(result)
            elif path == "/api/creators":
                creator = CTX.creators.create(payload)
                CTX.creator_sync.ensure(creator["id"])
                self._json(creator, HTTPStatus.CREATED)
            elif path == "/api/creator-sync/run":
                self._json(
                    CTX.creator_sync.run_now(
                        str(payload.get("creator_id") or "primary"),
                        force_comments=bool(payload.get("force_comments")),
                        start_date=str(payload.get("start_date") or "") or None,
                        videos_only=bool(payload.get("videos_only")),
                    )
                )
            elif path == "/api/creator-sync/login":
                self._json(
                    CTX.creator_sync.open_login_browser(
                        str(payload.get("creator_id") or "primary")
                    )
                )
            elif path == "/api/openai/control":
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled 必须是 true 或 false。")
                if enabled and not CTX.settings.openai_api_key:
                    raise RuntimeError("尚未配置 OPENAI_API_KEY，无法开启 API。")
                effective = save_openai_api_enabled(CTX.settings, enabled)
                self._json(
                    {
                        "configured": bool(CTX.settings.openai_api_key),
                        "api_switch_enabled": bool(CTX.settings.openai_api_enabled),
                        "enabled": effective,
                        "message": (
                            "OpenAI API 已开启。"
                            if effective
                            else "OpenAI API 已关闭，不会产生 OpenAI 调用费用。"
                        ),
                    }
                )
            elif path == "/api/doubao/control":
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled 必须是 true 或 false。")
                if enabled and not doubao_api_is_configured(CTX.settings):
                    raise RuntimeError(
                        "国内 API 尚未配置，暂时无法开启。"
                    )
                save_doubao_api_enabled(CTX.settings, enabled)
                if enabled:
                    CTX.auto_transcription.trigger()
                self._json(CTX.doubao.status())
            elif path == "/api/auto-transcription/run":
                self._json(CTX.auto_transcription.trigger())
            elif path == "/api/videos":
                result = self._create_manual_video(payload)
                self._json(result, HTTPStatus.CREATED)
            elif path == "/api/investment-thought-categories":
                self._json(
                    CTX.investment_thoughts.create_category(
                        name=str(payload.get("name") or ""),
                        description=str(payload.get("description") or ""),
                        parent_id=(int(payload.get("parent_id")) if payload.get("parent_id") else None),
                    ),
                    HTTPStatus.CREATED,
                )
            elif re.fullmatch(r"/api/investment-thought-categories/\d+", path):
                category_id = int(path.split("/")[3])
                update_kwargs = {
                    "name": (str(payload.get("name") or "") if "name" in payload else None),
                    "description": (
                        str(payload.get("description") or "")
                        if "description" in payload else None
                    ),
                }
                if "parent_id" in payload:
                    update_kwargs["parent_id"] = (
                        int(payload.get("parent_id")) if payload.get("parent_id") else None
                    )
                self._json(CTX.investment_thoughts.update_category(category_id, **update_kwargs))
            elif re.fullmatch(r"/api/investment-thought-categories/\d+/move", path):
                category_id = int(path.split("/")[3])
                self._json(
                    CTX.investment_thoughts.move_category(
                        category_id,
                        str(payload.get("direction") or ""),
                    )
                )
            elif path == "/api/investment-thought-video-links":
                self._json(
                    CTX.investment_thoughts.sync_video_categories(
                        video_id=int(payload.get("video_id") or 0),
                        category_ids=payload.get("category_ids") or [],
                    )
                )
            elif path == "/api/chat":
                result = CTX.chat.chat(
                    messages=payload.get("messages") or [],
                    model=str(payload.get("model") or DEFAULT_CHAT_MODEL),
                    account=str(payload.get("account") or CTX.settings.source_account_name),
                )
                self._json(
                    {
                        "answer": result.answer,
                        "model": result.model,
                        "response_id": result.response_id,
                        "tools_used": result.tools_used,
                    }
                )
            elif path == "/api/monitor/run":
                self._json(CTX.monitor.check_once())
            elif path == "/api/downloads/scan":
                self._json(CTX.download_watcher.scan_once())
            elif path == "/api/mainlines/analyze-latest":
                self._json(CTX.mainlines.analyze_latest())
            elif path == "/api/mainlines/from-content":
                self._json(
                    CTX.mainlines.analyze_external_content(
                        content=str(payload.get("content") or ""),
                        source_label=str(payload.get("source_label") or "外部AI内容"),
                        context=str(payload.get("context") or ""),
                    )
                )
            elif path == "/api/chat/adopt-memory":
                self._json(self._adopt_chat_memory(payload), HTTPStatus.CREATED)
            elif re.fullmatch(r"/api/mainline-drafts/\d+/confirm", path):
                draft_id = int(path.split("/")[3])
                self._json(CTX.mainlines.confirm_draft(draft_id))
            elif re.fullmatch(r"/api/mainline-drafts/\d+/reject", path):
                draft_id = int(path.split("/")[3])
                self._json(CTX.mainlines.reject_draft(draft_id))
            elif self._video_grandchild_path(path, "assets", "import-latest"):
                video_id = self._video_id_from_grandchild(path)
                self._json(CTX.importer.import_latest(video_id), HTTPStatus.CREATED)
            elif self._video_grandchild_path(path, "comments", "bulk"):
                video_id = self._video_id_from_grandchild(path)
                self._json(self._create_comments_bulk(video_id, payload), HTTPStatus.CREATED)
            elif self._video_grandchild_path(path, "comments", "confirm-import"):
                video_id = self._video_id_from_grandchild(path)
                self._json(
                    self._confirm_comment_image_preview(video_id, payload),
                    HTTPStatus.CREATED,
                )
            elif self._video_grandchild_path(path, "comments", "bind-link"):
                video_id = self._video_id_from_grandchild(path)
                self._json(
                    CTX.comment_sync.bind_link(
                        video_id,
                        str(payload.get("url") or ""),
                    )
                )
            elif self._video_grandchild_path(path, "comments", "auto-collect"):
                video_id = self._video_id_from_grandchild(path)
                self._json(
                    CTX.comment_sync.collect_preview(
                        video_id,
                        limit=int(payload.get("limit") or 1500),
                    ),
                    HTTPStatus.CREATED,
                )
            elif self._video_grandchild_path(path, "comments", "confirm-sync"):
                video_id = self._video_id_from_grandchild(path)
                selected = payload.get("selected_indexes")
                self._json(
                    CTX.comment_sync.confirm_preview(
                        video_id,
                        preview_id=str(payload.get("preview_id") or ""),
                        selected_indexes=(
                            [int(item) for item in selected]
                            if isinstance(selected, list)
                            else None
                        ),
                    ),
                    HTTPStatus.CREATED,
                )
            elif self._video_child_path(path, "comments"):
                video_id = self._video_id_from_child(path)
                self._json(self._create_comment(video_id, payload), HTTPStatus.CREATED)
            elif self._video_child_path(path, "notes"):
                video_id = self._video_id_from_child(path)
                self._json(self._save_note(video_id, payload), HTTPStatus.CREATED)
            elif self._video_child_path(path, "keywords-preview"):
                video_id = self._video_id_from_child(path)
                self._json(
                    CTX.keywords.preview(
                        video_id,
                        model=str(payload.get("model") or DEFAULT_KEYWORD_MODEL),
                        force=bool(payload.get("force", False)),
                    )
                )
            elif self._video_child_path(path, "keywords"):
                video_id = self._video_id_from_child(path)
                self._json(
                    CTX.keywords.save(
                        video_id,
                        categories=(
                            payload.get("categories")
                            if isinstance(payload.get("categories"), dict)
                            else None
                        ),
                        keywords=payload.get("keywords") or [],
                        items=payload.get("items") or [],
                        source_hash=str(payload.get("source_hash") or ""),
                        model=str(payload.get("model") or DEFAULT_KEYWORD_MODEL),
                    )
                )
            elif self._video_child_path(path, "transcripts"):
                video_id = self._video_id_from_child(path)
                self._json(self._create_transcript(video_id, payload), HTTPStatus.CREATED)
            elif self._video_child_path(path, "transcribe-video-text"):
                video_id = self._video_id_from_child(path)
                result = CTX.transcriber.transcribe_video_text(video_id)
                self._json(result, HTTPStatus.CREATED)
            elif self._video_child_path(path, "doubao-asr-transcription"):
                video_id = self._video_id_from_child(path)
                result = CTX.doubao.transcribe_video_text(video_id)
                self._json(result, HTTPStatus.CREATED)
            elif self._video_child_path(path, "recognize-image-text"):
                video_id = self._video_id_from_child(path)
                self._json(CTX.transcriber.recognize_image_text(video_id), HTTPStatus.CREATED)
            elif self._video_child_path(path, "recognize-image-text-ai"):
                video_id = self._video_id_from_child(path)
                self._json(CTX.transcriber.recognize_image_text_ai(video_id), HTTPStatus.CREATED)
            elif self._video_child_path(path, "recognize-title"):
                video_id = self._video_id_from_child(path)
                result = (
                    CTX.doubao.recognize_cover_title(
                        video_id,
                        force=True,
                    )
                    if CTX.doubao.vision_enabled()
                    else CTX.cover_titles.recognize_title(
                        video_id,
                        force=True,
                    )
                )
                self._json(result, HTTPStatus.CREATED)
            elif self._video_child_path(path, "title"):
                video_id = self._video_id_from_child(path)
                self._json(self._save_video_title(video_id, payload))
            elif self._video_child_path(path, "published-at"):
                video_id = self._video_id_from_child(path)
                self._json(self._update_video_published_at(video_id, payload))
            elif path.startswith("/api/videos/") and path.endswith("/analyze"):
                video_id = int(path.split("/")[3])
                self._json(CTX.analyzer.analyze_video(video_id))
            elif path == "/api/feedback":
                feedback_id = CTX.storage.save_feedback(
                    analysis_id=int(payload["analysis_id"]),
                    rating=int(payload["rating"]),
                    note=payload.get("note", ""),
                )
                self._json({"feedback_id": feedback_id, "optimization": CTX.optimizer.feedback_summary()})
            elif path == "/api/webhook/douyin":
                result = self._create_webhook_video(payload)
                self._json(result, HTTPStatus.CREATED)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if self._comment_detail_path(path):
                comment_id = int(path.split("/")[3])
                deleted = CTX.storage.delete_comment(comment_id)
                if not deleted:
                    self._json({"error": "comment not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json(deleted)
            elif re.fullmatch(r"/api/investment-thought-categories/\d+", path):
                category_id = int(path.split("/")[3])
                self._json(CTX.investment_thoughts.delete_category(category_id))
            elif re.fullmatch(r"/api/investment-thought-video-links/\d+/\d+", path):
                _, _, _, category_id, video_id = path.split("/")
                self._json(
                    CTX.investment_thoughts.unlink_video(
                        category_id=int(category_id), video_id=int(video_id)
                    )
                )
            elif self._video_detail_path(path):
                video_id = int(path.split("/")[3])
                if not CTX.storage.get_video(video_id):
                    self._json({"error": "video not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json(self._delete_video(video_id))
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _update_creator_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        creator_id = str(payload.get("creator_id") or "primary").strip() or "primary"
        creator, old_name = CTX.creators.update(creator_id, payload)
        if old_name != creator["name"]:
            rename_creator_directory(old_name, creator["name"])
            CTX.storage.rename_video_author(old_name, creator["name"])

        if creator_id == "primary":
            # Preserve compatibility for background imports and older scripts
            # that still read the original single-creator settings fields.
            update_agent_settings(CTX.settings, payload)
        else:
            global_fields = {
                "app_name",
                "openai_api_key",
                "clear_openai_api_key",
                "doubao_ark_api_key",
                "clear_doubao_ark_api_key",
                "doubao_asr_api_key",
                "clear_doubao_asr_api_key",
                "doubao_asr_app_id",
                "clear_doubao_asr_app_id",
                "doubao_asr_access_key",
                "clear_doubao_asr_access_key",
                "doubao_asr_resource_id",
                "doubao_text_model",
                "doubao_vision_model",
                "deep_model",
                "fast_model",
            }
            global_payload = {
                key: value for key, value in payload.items() if key in global_fields
            }
            update_agent_settings(CTX.settings, global_payload)

        CTX.creator_sync.ensure(creator_id)
        result = public_agent_settings(CTX.creators.settings_for(creator_id))
        result["creator_id"] = creator_id
        return result

    def _create_manual_video(self, payload: dict[str, Any]) -> dict[str, Any]:
        video = normalize_manual_video(payload)
        video_id, created = CTX.storage.upsert_video(video)
        transcript = (payload.get("transcript") or "").strip()
        if transcript:
            CTX.storage.save_transcript(video_id, transcript, source="manual")
        analysis = None
        if payload.get("analyze", True):
            analysis = CTX.analyzer.analyze_video(video_id)
        return {"video_id": video_id, "created": created, "analysis": analysis}

    def _create_transcript(self, video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        text = (payload.get("text") or payload.get("transcript") or "").strip()
        if not text:
            raise ValueError("text is required")
        transcript_id = CTX.storage.save_transcript(
            video_id,
            text,
            source=payload.get("source", "manual"),
            language=payload.get("language", "zh"),
        )
        analysis = None
        if payload.get("analyze", True):
            analysis = CTX.analyzer.analyze_video(video_id)
        return {"transcript_id": transcript_id, "analysis": analysis}

    def _create_comment(self, video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        text = (payload.get("text") or "").strip()
        if not text:
            raise ValueError("comment text is required")
        signal = self._comment_signal(text)
        source_comment_id = payload.get("source_comment_id") or hashlib.sha256(
            f"{video_id}|{payload.get('author', '')}|{text}".encode("utf-8")
        ).hexdigest()[:16]
        comment_id, created = CTX.storage.upsert_comment(
            {
                "video_id": video_id,
                "source": payload.get("source", "manual"),
                "source_comment_id": source_comment_id,
                "author": payload.get("author", "本地录入"),
                "text": text,
                "like_count": payload.get("like_count", 0),
                "reply_count": payload.get("reply_count", 0),
                "sentiment": signal["sentiment"],
                "risk_level": signal["risk_level"],
                "published_at": payload.get("published_at"),
                "raw_json": payload,
            }
        )
        return {"comment_id": comment_id, "created": created, **signal}

    def _create_comments_bulk(self, video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        raw_text = (payload.get("text") or payload.get("comments") or "").strip()
        if not raw_text:
            raise ValueError("comments text is required")
        created_count = 0
        updated_count = 0
        ids: list[int] = []
        for line in raw_text.splitlines():
            text = line.strip()
            if not text:
                continue
            author = payload.get("author", "")
            if "：" in text:
                maybe_author, maybe_text = text.split("：", 1)
                if 0 < len(maybe_author) <= 24 and maybe_text.strip():
                    author = maybe_author.strip()
                    text = maybe_text.strip()
            elif ":" in text:
                maybe_author, maybe_text = text.split(":", 1)
                if 0 < len(maybe_author) <= 24 and maybe_text.strip():
                    author = maybe_author.strip()
                    text = maybe_text.strip()
            signal = self._comment_signal(text)
            source_comment_id = hashlib.sha256(
                f"{video_id}|bulk|{author}|{text}".encode("utf-8")
            ).hexdigest()[:16]
            comment_id, created = CTX.storage.upsert_comment(
                {
                    "video_id": video_id,
                    "source": payload.get("source", "bulk-paste"),
                    "source_comment_id": source_comment_id,
                    "author": author or "批量导入",
                    "text": text,
                    "sentiment": signal["sentiment"],
                    "risk_level": signal["risk_level"],
                    "raw_json": {"line": line, "mode": "bulk"},
                }
            )
            ids.append(comment_id)
            if created:
                created_count += 1
            else:
                updated_count += 1
        return {"created": created_count, "updated": updated_count, "comment_ids": ids}

    def _preview_comments_from_csv(self, video_id: int) -> dict[str, Any]:
        if not CTX.storage.get_video(video_id):
            raise ValueError("video not found")
        _, files = self._read_multipart()
        csv_files = [
            item
            for item in files
            if str(item.get("filename") or "").casefold().endswith(".csv")
        ]
        if len(csv_files) != 1:
            raise ValueError("请选择一个CSV评论文件。")
        source = csv_files[0]
        return CTX.comment_sync.preview_csv(
            video_id,
            content=bytes(source.get("content") or b""),
            filename=str(source.get("filename") or "comments.csv"),
        )

    def _preview_comments_from_images(self, video_id: int) -> dict[str, Any]:
        video = CTX.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")

        fields, files = self._read_multipart()
        if not files:
            raise ValueError("image file is required")

        image_dir = DATA_DIR / "comment_images" / f"video_{video_id}"
        image_dir.mkdir(parents=True, exist_ok=True)

        account_author = str(video.get("author") or CTX.settings.source_account_name)
        mode = str(fields.get("mode") or "hybrid").strip().lower()
        if mode not in {"local", "hybrid"}:
            mode = "hybrid"

        preview_items: list[dict[str, Any]] = []
        preview_files: list[dict[str, Any]] = []
        warnings: list[str] = []
        models: set[str] = set()
        engines: set[str] = set()

        for file_index, upload in enumerate(files):
            content_type = str(upload.get("content_type") or "")
            if content_type and not content_type.startswith("image/"):
                continue
            original_name = self._safe_filename(upload.get("filename") or "comment-image.png")
            content = upload.get("content") or b""
            if not content:
                continue
            digest = hashlib.sha256(content).hexdigest()
            target = self._unique_path(image_dir / original_name)
            target.write_bytes(content)

            recognized = CTX.comment_recognizer.recognize(
                target,
                author_name=account_author,
                mode=mode,
            )
            engine = str(recognized.get("engine") or "")
            model = str(recognized.get("model") or "")
            if engine:
                engines.add(engine)
            if model:
                models.add(model)
            warnings.extend(str(item) for item in recognized.get("warnings") or [] if item)

            file_record = {
                "file_index": file_index,
                "original_name": original_name,
                "source_image": str(target),
                "digest": digest,
                "engine": engine,
                "model": model,
                "fallback_model": str(recognized.get("fallback_model") or ""),
                "ocr_lines": recognized.get("ocr_lines") or [],
            }
            preview_files.append(file_record)

            for source_item_index, item in enumerate(recognized.get("comments") or []):
                preview_index = len(preview_items)
                preview_items.append(
                    {
                        **item,
                        "preview_index": preview_index,
                        "source_file_index": file_index,
                        "source_item_index": source_item_index,
                        "include": True,
                    }
                )

        if not preview_items:
            raise ValueError("没有从图片中识别出可预览的评论")

        preview_id = secrets.token_hex(16)
        preview_dir = DATA_DIR / "comment_previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_record = {
            "preview_id": preview_id,
            "video_id": video_id,
            "account_author": account_author,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "files": preview_files,
            "comments": preview_items,
        }
        (preview_dir / f"{preview_id}.json").write_text(
            json.dumps(preview_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "preview_id": preview_id,
            "requires_confirmation": True,
            "engine": " + ".join(sorted(engines)) or "windows-ocr",
            "model": " + ".join(sorted(models)),
            "mode": mode,
            "items": preview_items,
            "warnings": warnings,
            "message": "识别完成，请核对后点击确认导入。数据库尚未写入。",
        }

    def _confirm_comment_image_preview(
        self,
        video_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        video = CTX.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")

        preview_id = str(payload.get("preview_id") or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{32}", preview_id):
            raise ValueError("invalid preview id")
        preview_path = DATA_DIR / "comment_previews" / f"{preview_id}.json"
        if not preview_path.is_file():
            raise ValueError("识别预览已失效，请重新识别图片")
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        if int(preview.get("video_id") or 0) != video_id:
            raise ValueError("preview does not belong to this video")

        original_items = {
            int(item.get("preview_index")): item
            for item in preview.get("comments") or []
            if isinstance(item, dict) and str(item.get("preview_index", "")).isdigit()
        }
        submitted = payload.get("items")
        if not isinstance(submitted, list):
            raise ValueError("items are required")

        files = {
            int(item.get("file_index")): item
            for item in preview.get("files") or []
            if isinstance(item, dict) and str(item.get("file_index", "")).isdigit()
        }
        existing_orders = []
        for comment in CTX.storage.list_comments(video_id, limit=1000):
            raw = comment.get("raw_json") or {}
            if isinstance(raw, dict):
                try:
                    existing_orders.append(int(raw.get("display_order") or 0))
                except (TypeError, ValueError):
                    pass
        display_order = max(existing_orders, default=0)

        account_author = str(video.get("author") or CTX.settings.source_account_name)
        created_count = 0
        updated_count = 0
        imported: list[dict[str, Any]] = []
        source_ids_by_preview_index: dict[int, str] = {}
        last_root_source_comment_id = ""

        for submitted_item in submitted:
            if not isinstance(submitted_item, dict) or not bool(submitted_item.get("include", True)):
                continue
            try:
                preview_index = int(submitted_item.get("preview_index"))
            except (TypeError, ValueError):
                continue
            original = original_items.get(preview_index)
            if not original:
                continue

            text = str(submitted_item.get("text") or original.get("text") or "").strip()
            if not text:
                continue
            author = str(
                submitted_item.get("author")
                or original.get("author")
                or "识图用户"
            ).strip() or "识图用户"
            reply_depth = max(
                0,
                min(
                    1,
                    int(submitted_item.get("reply_depth", original.get("reply_depth") or 0)),
                ),
            )
            requested_kind = str(
                submitted_item.get("kind") or original.get("kind") or "user_comment"
            )
            if requested_kind == "author_reply" and author == account_author:
                kind = "author_reply"
            else:
                kind = "user_reply" if reply_depth else "user_comment"

            file_index = int(original.get("source_file_index") or 0)
            file_record = files.get(file_index) or {}
            digest = str(file_record.get("digest") or "")
            source_item_index = int(original.get("source_item_index") or 0)
            source_comment_id = f"ocr-{digest[:12]}-{source_item_index + 1:03d}"
            display_order += 1
            signal = self._comment_signal(text)
            raw_comment = {
                "kind": kind,
                "display_order": display_order,
                "source_image": str(file_record.get("source_image") or ""),
                "ocr_lines": file_record.get("ocr_lines") or [],
                "reply_depth": reply_depth,
                "author_liked": bool(original.get("author_liked")),
                "recognition_engine": file_record.get("engine") or "",
                "recognition_model": original.get("recognition_model") or file_record.get("model") or "",
                "recognition_confidence": original.get("confidence"),
                "recognition_needs_review": bool(original.get("needs_review")),
                "local_text": original.get("local_text") or "",
                "ai_text": original.get("ai_text") or "",
                "confirmed_by_user": True,
                "preview_id": preview_id,
            }
            parent_index = original.get("parent_index")
            parent_source_comment_id = (
                source_ids_by_preview_index.get(int(parent_index))
                if isinstance(parent_index, int)
                else ""
            )
            if not parent_source_comment_id and reply_depth and last_root_source_comment_id:
                parent_source_comment_id = last_root_source_comment_id
            if parent_source_comment_id:
                raw_comment["parent_source_comment_id"] = parent_source_comment_id

            comment_id, created = CTX.storage.upsert_comment(
                {
                    "video_id": video_id,
                    "source": "ocr-image-confirmed",
                    "source_comment_id": source_comment_id,
                    "author": author,
                    "text": text,
                    "like_count": int(
                        submitted_item.get("like_count", original.get("like_count") or 0)
                    ),
                    "reply_count": 0,
                    "sentiment": signal["sentiment"],
                    "risk_level": signal["risk_level"],
                    "published_at": str(
                        submitted_item.get("published_at")
                        or original.get("published_at")
                        or ""
                    ),
                    "raw_json": raw_comment,
                }
            )
            imported.append(
                {
                    "comment_id": comment_id,
                    "created": created,
                    "author": author,
                    "text": text,
                    "kind": kind,
                }
            )
            source_ids_by_preview_index[preview_index] = source_comment_id
            if not reply_depth:
                last_root_source_comment_id = source_comment_id
            if created:
                created_count += 1
            else:
                updated_count += 1

        if not imported:
            raise ValueError("没有选择可导入的评论")

        preview_path.unlink(missing_ok=True)
        comments = CTX.storage.list_comments(video_id, limit=1000)

        return {
            "created": created_count,
            "updated": updated_count,
            "imported": imported,
            "comments": comments,
            "total_comments": len(comments),
        }

    def _delete_video(self, video_id: int) -> dict[str, Any]:
        video = CTX.storage.get_video(video_id) or {}
        assets = CTX.storage.list_assets(video_id)
        safe_roots = [ARCHIVE_DIR.resolve()]
        file_paths: list[Path] = []
        skipped_files: list[str] = []

        def is_safe_path(path: Path) -> bool:
            for root in safe_roots:
                try:
                    path.relative_to(root)
                    return True
                except ValueError:
                    continue
            return False

        def add_delete_candidate(local_path: str | None) -> None:
            if not local_path:
                return
            path = Path(local_path).resolve()
            if path in file_paths:
                return
            if not is_safe_path(path):
                skipped_files.append(str(path))
                return
            file_paths.append(path)

        for asset in assets:
            add_delete_candidate(asset.get("local_path"))
            raw = asset.get("raw_json") or {}
            if isinstance(raw, dict):
                add_delete_candidate(raw.get("source_path"))

        try:
            video_raw = json.loads(video.get("raw_json") or "{}")
        except json.JSONDecodeError:
            video_raw = {}
        if isinstance(video_raw, dict):
            add_delete_candidate(video_raw.get("source_path"))

        deleted = CTX.storage.delete_video(video_id)
        deleted_files: list[str] = []
        file_errors: list[str] = []
        for path in file_paths:
            if not path.exists():
                continue
            try:
                if path.is_file():
                    path.unlink()
                    deleted_files.append(str(path))
            except OSError as exc:
                file_errors.append(f"{path}: {exc}")

        removed_dirs: list[str] = []
        archive_root = ARCHIVE_DIR.resolve()
        archive_dir = (ARCHIVE_DIR / f"video_{video_id}").resolve()
        try:
            archive_dir.relative_to(archive_root)
            if archive_dir.exists() and archive_dir.is_dir() and not any(archive_dir.iterdir()):
                archive_dir.rmdir()
                removed_dirs.append(str(archive_dir))
        except (OSError, ValueError):
            pass

        return {
            "deleted": deleted,
            "video_id": video_id,
            "deleted_files": deleted_files,
            "skipped_files": skipped_files,
            "file_errors": file_errors,
            "removed_dirs": removed_dirs,
        }

    def _save_note(self, video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        video = CTX.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        note_type = str(payload.get("note_type") or "interpretation").strip() or "interpretation"
        if note_type not in {"interpretation", "video_text"}:
            raise ValueError("unsupported note type")
        text = str(payload.get("text") or "")
        if note_type == "video_text":
            return CTX.storage.save_official_original(video_id, text)
        note = CTX.storage.save_note(video_id, note_type, text)
        return {"note": note, "cleanup": None}

    def _adopt_chat_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        assistant_text = str(payload.get("assistant_content") or "").strip()
        if not assistant_text:
            raise ValueError("没有可采纳的本地AI文案。")
        if len(assistant_text) > 16000:
            raise ValueError("文案过长，请先保留需要采纳的主要内容。")
        user_text = str(payload.get("user_content") or "").strip()[:6000]
        message_id = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            str(payload.get("message_id") or "").strip(),
        ).strip("-")[:80]
        if not message_id:
            message_id = hashlib.sha256(assistant_text.encode("utf-8")).hexdigest()[:20]
        session_id = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            str(payload.get("chat_session_id") or "local-chat").strip(),
        ).strip("-")[:100] or "local-chat"

        def parse_timestamp(value: Any, fallback: datetime) -> datetime:
            text = str(value or "").strip()
            if not text:
                return fallback
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return fallback
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)

        now = datetime.now(UTC)
        started_at = parse_timestamp(payload.get("chat_started_at"), now - timedelta(seconds=2))
        ended_at = parse_timestamp(payload.get("chat_ended_at"), now)
        if ended_at <= started_at:
            ended_at = started_at + timedelta(seconds=1)

        lines = [
            re.sub(r"^[#>*\-\s]+", "", line).strip()
            for line in assistant_text.splitlines()
            if line.strip()
        ]
        title_seed = str(payload.get("title") or "").strip() or (lines[0] if lines else "本地AI文案")
        title = f"已采纳文案：{title_seed[:70]}"
        conclusions = [
            part.strip()
            for part in re.split(r"[。！？\n]+", assistant_text)
            if len(part.strip()) >= 6
        ][:6]
        related_ids = sorted(
            {
                f"video:{match}"
                for match in re.findall(
                    r"(?:video\s*[:：#]?|视频\s*#?)\s*(\d+)",
                    f"{user_text}\n{assistant_text}",
                    flags=re.IGNORECASE,
                )
            }
        )
        result = CTX.chat.memory_store.save(
            title=title,
            chat_started_at=started_at.isoformat(),
            chat_ended_at=ended_at.isoformat(),
            chat_timezone="Asia/Shanghai",
            source_chat_reference="模型先生本地AI",
            chat_session_id=session_id,
            discussion_topic=user_text[:500] or title_seed[:500],
            core_conclusions=conclusions or [assistant_text[:500]],
            related_record_ids=related_ids,
            model_mr_view="",
            user_view="用户已明确点击“本文案采纳”，同意把该回答作为后续讨论参考。",
            gpt_analysis=assistant_text,
            verification_items=[
                "该文案由本地AI整理；涉及模型先生观点时，仍需回查对应视频原文或本人回复。"
            ],
            memory_key=f"local-ai-{message_id}",
            metadata={
                "origin": "local_chat_adopt_button",
                "assistant_message_id": message_id,
                "model": str(payload.get("model") or ""),
                "user_confirmed": True,
            },
        )
        return result

    def _update_video_published_at(self, video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        video = CTX.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        published_at = str(payload.get("published_at") or "").strip()
        if not published_at:
            raise ValueError("published_at is required")
        updated = CTX.storage.update_video_published_at_manual(video_id, published_at)
        if not updated:
            raise ValueError("video not found")
        return {"video": updated}

    def _save_video_title(self, video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        title_info = CTX.storage.save_manual_title(video_id, title)
        return {"title_info": title_info}

    def _create_asset_upload(self, video_id: int) -> dict[str, Any]:
        video = CTX.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        fields, files = self._read_multipart()
        if not files:
            raise ValueError("file is required")

        upload = files[0]
        original_name = self._safe_filename(upload["filename"] or "video.bin")
        mime_type = upload["content_type"] or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        creator_dirs = ensure_creator_directories(str(video.get("author") or "未命名博主"))
        asset_type = str(fields.get("asset_type") or "video")
        media_dir = (
            creator_dirs["images"]
            if asset_type in {"image", "screenshot"} or mime_type.startswith("image/")
            else creator_dirs["videos"]
        )
        target = self._unique_path(media_dir / original_name)
        content = upload["content"]
        target.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        asset_id = CTX.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": asset_type,
                "storage_mode": "source_file",
                "original_name": original_name,
                "local_path": str(target),
                "mime_type": mime_type,
                "size_bytes": len(content),
                "sha256": digest,
                "source": fields.get("source", "manual-upload"),
                "status": "stored",
                "raw_json": {
                    "source_path": str(target),
                    "uploaded_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
                },
            }
        )
        return {
            "asset_id": asset_id,
            "video_id": video_id,
            "original_name": original_name,
            "size_bytes": len(content),
            "sha256": digest,
            "file_url": f"/api/assets/{asset_id}/file",
        }

    def _create_webhook_video(self, payload: dict[str, Any]) -> dict[str, Any]:
        video = normalize_douyin_webhook(payload)
        video_id, created = CTX.storage.upsert_video(video)
        analysis = None
        if created and CTX.settings.auto_analyze_new_videos:
            analysis = CTX.analyzer.analyze_video(video_id)
        return {"video_id": video_id, "created": created, "analysis": analysis}

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _read_multipart(self) -> tuple[dict[str, str], list[dict[str, Any]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("multipart/form-data is required")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        fields: dict[str, str] = {}
        files: list[dict[str, Any]] = []
        for part in message.iter_parts():
            params = dict(part.get_params(header="content-disposition")[1:])
            name = params.get("name")
            filename = params.get("filename")
            content = part.get_payload(decode=True) or b""
            if filename:
                files.append(
                    {
                        "field": name,
                        "filename": filename,
                        "content_type": part.get_content_type(),
                        "content": content,
                    }
                )
            elif name:
                fields[name] = content.decode(part.get_content_charset() or "utf-8", errors="replace")
        return fields, files

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: Path) -> None:
        if path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        else:
            content_type = "application/octet-stream"
        self._serve_file(path, content_type)

    def _serve_asset_file(self, asset_id: int) -> None:
        asset = CTX.storage.get_asset(asset_id)
        if not asset or not asset.get("local_path"):
            self._json({"error": "asset not found"}, HTTPStatus.NOT_FOUND)
            return
        path = Path(asset["local_path"]).resolve()
        if not path.exists() or not path.is_file():
            self._json({"error": "asset file not available"}, HTTPStatus.NOT_FOUND)
            return
        self._serve_file(path, asset.get("mime_type") or "application/octet-stream")

    def _serve_file(
        self,
        path: Path,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not path.exists() or not path.is_file():
            self._json({"error": "file not found"}, HTTPStatus.NOT_FOUND)
            return

        file_size = path.stat().st_size
        start = 0
        end = max(file_size - 1, 0)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range", "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or file_size == 0:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return

            start_text, end_text = match.groups()
            if not start_text and not end_text:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            if start_text:
                start = int(start_text)
                end = min(int(end_text), file_size - 1) if end_text else file_size - 1
            else:
                suffix_length = min(int(end_text), file_size)
                start = file_size - suffix_length
                end = file_size - 1
            if start >= file_size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = max(end - start + 1, 0)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header(
            "Cache-Control",
            "private, max-age=3600"
            if content_type.startswith(("video/", "audio/", "image/"))
            else "no-store",
        )
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(content_length))
        self.end_headers()
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = content_length
                while remaining:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _assets_with_urls(self, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for asset in assets:
            item = dict(asset)
            if item.get("local_path"):
                item["file_url"] = f"/api/assets/{item['id']}/file"
            result.append(item)
        return result

    def _video_summaries_with_urls(
        self,
        videos: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for video in videos:
            item = dict(video)
            asset_id = item.pop("primary_asset_id", None)
            asset_type = item.pop("primary_asset_type", "")
            asset_name = item.pop("primary_asset_name", "")
            asset_mime = item.pop("primary_asset_mime", "")
            asset_size = item.pop("primary_asset_size", 0)
            item["primary_asset"] = (
                {
                    "id": int(asset_id),
                    "asset_type": asset_type,
                    "original_name": asset_name,
                    "mime_type": asset_mime,
                    "size_bytes": int(asset_size or 0),
                    "file_url": f"/api/assets/{int(asset_id)}/file",
                }
                if asset_id
                else None
            )
            keyword_payload = from_json(item.pop("ai_keywords_json", ""), {})
            stored_categories = from_json(item.pop("keyword_categories_json", ""), {})
            categories = normalize_keyword_categories(
                {"categories": stored_categories}
                if isinstance(stored_categories, dict) and stored_categories
                else keyword_payload
            )
            schema_version = str(
                item.pop("keyword_schema_version", "")
                or keyword_payload.get("schema_version", "")
            )
            saved_source_hash = str(
                item.pop("keyword_source_hash", "")
                or keyword_payload.get("source_hash", "")
            )
            current_source_hash = str(item.pop("content_original_hash", "") or "")
            item["keyword_info"] = {
                "categories": categories,
                "keywords": flatten_keyword_categories(categories),
                "schema_version": schema_version,
                "model": (
                    item.pop("keyword_model", "")
                    or keyword_payload.get("model", "")
                ),
                "saved_source_hash": saved_source_hash,
                "confirmed_at": (
                    item.pop("keyword_confirmed_at", None)
                    or keyword_payload.get("confirmed_at")
                ),
                "stale": bool(saved_source_hash) and (
                    saved_source_hash != current_source_hash
                    or schema_version != KEYWORD_SCHEMA_VERSION
                ),
                "can_extract": bool(item.get("has_video_text")),
            }
            item["has_video_text"] = bool(item.get("has_video_text"))
            item["has_interpretation"] = bool(item.get("has_interpretation"))
            result.append(item)
        return result

    def _cached_keyword_info(self, detail: dict[str, Any]) -> dict[str, Any]:
        return CTX.keywords.status(int(detail["video"]["id"]))

    def _video_detail_path(self, path: str) -> bool:
        parts = path.strip("/").split("/")
        return len(parts) == 3 and parts[0] == "api" and parts[1] == "videos" and parts[2].isdigit()

    def _comment_detail_path(self, path: str) -> bool:
        parts = path.strip("/").split("/")
        return len(parts) == 3 and parts[0] == "api" and parts[1] == "comments" and parts[2].isdigit()

    def _video_child_path(self, path: str, child: str) -> bool:
        parts = path.strip("/").split("/")
        return (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "videos"
            and parts[2].isdigit()
            and parts[3] == child
        )

    def _video_id_from_child(self, path: str) -> int:
        return int(path.strip("/").split("/")[2])

    def _video_grandchild_path(self, path: str, child: str, grandchild: str) -> bool:
        parts = path.strip("/").split("/")
        return (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "videos"
            and parts[2].isdigit()
            and parts[3] == child
            and parts[4] == grandchild
        )

    def _video_id_from_grandchild(self, path: str) -> int:
        return int(path.strip("/").split("/")[2])

    def _safe_filename(self, filename: str) -> str:
        filename = filename.replace("\\", "/").split("/")[-1].strip()
        filename = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", filename)
        return filename or "upload.bin"

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for index in range(2, 1000):
            candidate = path.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise ValueError("too many files with the same name")

    def _comment_signal(self, text: str) -> dict[str, str]:
        return comment_signal(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8797)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"{CTX.settings.app_name} running at http://{args.host}:{args.port}")
    print(f"Database: {CTX.settings.database_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
