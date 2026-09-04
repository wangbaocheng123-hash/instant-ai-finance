from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .doubao_asr import DoubaoAsrUnavailable, is_configured as doubao_asr_is_configured, transcribe_video
from .paths import LIBRARY_ROOT
from .model_mr_metadata import KEYWORD_CATEGORIES, clean_keyword_info, clean_links, clean_words, keyword_revision


DEFAULT_ORIGIN = "http://127.0.0.1:8787"
SNAPSHOT_VERSION = 2
SUPPORTED_SNAPSHOT_VERSIONS = {1, SNAPSHOT_VERSION}
_DETAIL_LOCK = threading.RLock()


def _default_snapshot_path() -> Path:
    return Path(
        os.environ.get(
            "INSTANT_AI_MODEL_MR_SNAPSHOT",
            str(LIBRARY_ROOT / "model-mr" / "public-snapshot.json"),
        )
    )


def _default_media_root() -> Path:
    configured = os.environ.get("INSTANT_AI_MODEL_MR_MEDIA_ROOT", "").strip()
    return Path(configured) if configured else _default_snapshot_path().parent / "media"


class ModelMrUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelMrClient:
    origin: str = os.environ.get("INSTANT_AI_MODEL_MR_ORIGIN", DEFAULT_ORIGIN).rstrip("/")
    snapshot_path: Path = field(default_factory=_default_snapshot_path)
    media_root: Path = field(default_factory=_default_media_root)

    def __post_init__(self) -> None:
        parsed = urlparse(self.origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("模型先生服务地址无效。")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("INSTANT_AI_MODEL_MR_ALLOW_REMOTE") != "1":
            raise ValueError("模型先生服务默认只允许服务器回环地址。")

    @property
    def details_root(self) -> Path:
        return self.snapshot_path.parent / "details"

    def status(self) -> dict[str, Any]:
        try:
            source = self._json("/api/status", timeout=3)
            chat = self.chat_config()
        except ModelMrUnavailable as error:
            snapshot = self._snapshot()
            if snapshot is not None:
                works = snapshot.get("works") if isinstance(snapshot.get("works"), list) else []
                thoughts = snapshot.get("thoughts") if isinstance(snapshot.get("thoughts"), list) else []
                counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
                owner_library = int(snapshot.get("version") or 0) >= SNAPSHOT_VERSION
                return {
                    "available": True,
                    "module": "模型先生",
                    "mode": "owner-mobile-library" if owner_library else "sanitized-snapshot",
                    "message": (
                        "模型先生主人资料库已连接；本地视频、正式原文和评论只在登录后提供。"
                        if owner_library
                        else "模型先生精简资料已连接。"
                    ),
                    "features": (
                        [
                            "本地视频",
                            "视频原文",
                            "豆包识别文字" if doubao_asr_is_configured() else "豆包转写结果",
                            "评论",
                            "投资思路",
                        ]
                        if owner_library
                        else ["作品浏览", "投资思路"]
                    ),
                    "counts": {
                        "works": int(counts.get("works") or len(works)),
                        "media": int(counts.get("media") or 0),
                        "transcripts": int(counts.get("transcripts") or 0),
                        "comments": int(counts.get("comments") or 0),
                        "analyses": int(counts.get("thoughts") or len(thoughts)),
                    },
                    "chat_enabled": False,
                    "doubao_asr_enabled": doubao_asr_is_configured(),
                    "snapshot_updated_at": str(snapshot.get("exported_at") or ""),
                }
            return {
                "available": False,
                "module": "模型先生",
                "mode": "independent-owner",
                "message": str(error),
                "features": ["本地视频", "视频原文", "豆包识别文字", "评论", "投资思路", "智能问答"],
            }
        counts = source.get("counts") if isinstance(source.get("counts"), dict) else {}
        return {
            "available": True,
            "module": "模型先生",
            "mode": "independent-owner",
            "message": "模型先生完整主人模块已连接。",
            "features": ["本地视频", "视频原文", "豆包识别文字", "评论", "投资思路", "智能问答"],
            "counts": {
                "works": int(counts.get("videos") or 0),
                "media": int(counts.get("assets") or 0),
                "transcripts": int(counts.get("transcripts") or 0),
                "comments": int(counts.get("comments") or 0),
                "analyses": int(counts.get("analyses") or 0),
            },
            "chat_enabled": bool(chat.get("enabled")),
        }

    def works(self, *, limit: int = 40, offset: int = 0) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, min(int(offset), 500_000))
        try:
            # The original Windows sidecar has no offset parameter. Request the
            # smallest prefix needed for this page, then slice locally. The cloud
            # snapshot path below performs true page slicing without loading work
            # details, comments or media.
            source_limit = min(safe_offset + safe_limit, 500)
            source = self._json(f"/api/videos?{urlencode({'limit': source_limit, 'account': '模型先生'})}")
        except ModelMrUnavailable:
            snapshot = self._require_snapshot()
            items = snapshot.get("works") if isinstance(snapshot.get("works"), list) else []
            public_items = [item for item in items if isinstance(item, dict)]
            page = public_items[safe_offset : safe_offset + safe_limit]
            cleaned = [self._clean_snapshot_work(item) for item in page]
            total = len(public_items)
            return {
                "items": cleaned,
                "count": len(cleaned),
                "total": total,
                "offset": safe_offset,
                "has_more": safe_offset + len(cleaned) < total,
                "mode": "owner-mobile-library" if int(snapshot.get("version") or 0) >= 2 else "sanitized-snapshot",
            }
        items = source.get("items") if isinstance(source.get("items"), list) else []
        cleaned_prefix = [self._clean_work(item) for item in items if isinstance(item, dict)]
        cleaned = cleaned_prefix[safe_offset : safe_offset + safe_limit]
        source_total = source.get("total")
        try:
            total = max(len(cleaned_prefix), int(source_total))
        except (TypeError, ValueError):
            total = len(cleaned_prefix)
        prefix_may_continue = len(cleaned_prefix) >= source_limit and source_limit < 500
        if prefix_may_continue:
            total = max(total, safe_offset + len(cleaned) + 1)
        return {
            "items": cleaned,
            "count": len(cleaned),
            "total": total,
            "offset": safe_offset,
            "has_more": safe_offset + len(cleaned) < total,
            "mode": "independent-owner",
        }

    def work_detail(self, work_id: int) -> dict[str, Any]:
        safe_id = self._safe_work_id(work_id)
        try:
            source = self._json(f"/api/videos/{safe_id}", timeout=30, max_bytes=64 * 1024 * 1024)
            detail = self._clean_detail(source, work_id=safe_id, live=True)
            try:
                stock_mentions = self._json(
                    f"/api/videos/{safe_id}/stock-mentions?limit=20",
                    timeout=30,
                    max_bytes=4 * 1024 * 1024,
                )
            except ModelMrUnavailable:
                stock_mentions = {}
            source_comments = source.get("comments") if isinstance(source.get("comments"), list) else []
            comment_id_map = {
                int(item.get("id") or 0): index
                for index, item in enumerate(source_comments, start=1)
                if isinstance(item, dict) and int(item.get("id") or 0) > 0
            }
            detail["stock_mentions"] = self._clean_stock_mentions(stock_mentions, comment_id_map)
            return detail
        except ModelMrUnavailable:
            self._require_snapshot()
            detail_path = self._detail_path(safe_id)
            try:
                value = json.loads(detail_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ModelMrUnavailable("这条作品的原文和评论资料尚未同步。") from error
            if not isinstance(value, dict):
                raise ModelMrUnavailable("这条作品的资料格式不正确。")
            return self._clean_snapshot_detail(value, safe_id)

    def save_title(self, work_id: int, title: str) -> dict[str, Any]:
        safe_id = self._safe_work_id(work_id)
        cleaned_title = str(title or "").strip()
        if not cleaned_title:
            raise ValueError("作品标题不能为空。")
        if len(cleaned_title) > 120:
            raise ValueError("作品标题不能超过 120 个字符。")
        try:
            source = self._json(
                f"/api/videos/{safe_id}/title",
                method="POST",
                payload={"title": cleaned_title},
                timeout=30,
            )
            title_info = source.get("title_info") if isinstance(source.get("title_info"), dict) else {}
            active_title = str(title_info.get("active_title") or cleaned_title).strip()
            return {"ok": True, "title": active_title, "saved": True, "mode": "live"}
        except ModelMrUnavailable:
            with _DETAIL_LOCK:
                detail = self.work_detail(safe_id)
                detail["work"]["title"] = cleaned_title
                snapshot = self._require_snapshot()
                matched = False
                for item in snapshot.get("works", []):
                    if isinstance(item, dict) and int(item.get("id") or 0) == safe_id:
                        item["title"] = cleaned_title
                        matched = True
                        break
                if not matched:
                    raise ModelMrUnavailable("作品索引中没有这条记录。")
                snapshot["updated_at"] = int(time.time())
                self._write_json(self._detail_path(safe_id), self._clean_snapshot_detail(detail, safe_id))
                self._write_json(self.snapshot_path, snapshot)
            return {"ok": True, "title": cleaned_title, "saved": True, "mode": "owner-mobile-library"}

    def save_video_text(self, work_id: int, text: str) -> dict[str, Any]:
        safe_id = self._safe_work_id(work_id)
        cleaned_text = str(text or "").strip()
        if not cleaned_text:
            raise ValueError("视频原文不能为空。")
        if len(cleaned_text) > 200_000:
            raise ValueError("视频原文过长。")
        try:
            source = self._json(
                f"/api/videos/{safe_id}/notes",
                method="POST",
                payload={"note_type": "video_text", "text": cleaned_text},
                timeout=30,
            )
            note = source.get("note") if isinstance(source.get("note"), dict) else {}
            return {"ok": True, "text": str(note.get("text") or cleaned_text), "saved": True, "mode": "live"}
        except ModelMrUnavailable:
            with _DETAIL_LOCK:
                detail = self.processing_detail(safe_id)
                detail["video_text"] = {
                    "text": cleaned_text,
                    "official": True,
                    "source": "owner-mobile-edit",
                    "updated_at": int(time.time()),
                }
                self._write_json(self._detail_path(safe_id), detail)
            return {"ok": True, "text": cleaned_text, "saved": True, "mode": "owner-mobile-library"}

    def processing_detail(self, work_id: int) -> dict[str, Any]:
        """Read the private cloud detail only; never call the desktop sidecar."""
        try:
            value = json.loads(self._detail_path(work_id).read_text(encoding="utf-8"))
            if not isinstance(value, dict) or int(value.get("work", {}).get("id", 0)) != work_id:
                raise ValueError("invalid detail")
            return value
        except (OSError, ValueError, TypeError) as error:
            raise ModelMrUnavailable("云端作品详情尚未完整同步。") from error

    def save_auto_video_text(self, work_id: int, text: str) -> str:
        """Canonical ASR text, not a claim of human verification. Never overwrite."""
        cleaned = str(text or "").strip()
        if not cleaned or len(cleaned) > 200_000:
            raise ValueError("自动识别原文为空或过长。")
        with _DETAIL_LOCK:
            detail = self.processing_detail(work_id)
            existing = str(detail.get("video_text", {}).get("text") or "").strip()
            if existing:
                return existing
            snapshot = self._require_snapshot()
            indexed = next((w for w in snapshot["works"] if int(w.get("id", 0)) == work_id), None)
            if indexed is None:
                raise ValueError("作品不存在。")
            detail["video_text"] = {"text": cleaned, "official": True,
                                    "source": "doubao-auto-unreviewed", "updated_at": str(int(time.time()))}
            detail["work"]["has_video_text"] = True
            indexed["has_video_text"] = True
            self._write_json(self._detail_path(work_id), detail)
            self._write_json(self.snapshot_path, snapshot)
            if self.processing_detail(work_id).get("video_text", {}).get("text") != cleaned:
                raise ModelMrUnavailable("自动保存后校验失败。")
            return cleaned

    def repair_processing_index(self, work_id: int) -> None:
        """Repair a partial detail/index save without re-submitting a model job."""
        with _DETAIL_LOCK:
            detail = self.processing_detail(work_id)
            snapshot = self._require_snapshot()
            indexed = next((w for w in snapshot["works"] if int(w.get("id", 0)) == work_id), None)
            if indexed is None:
                raise ValueError("作品不存在。")
            fields = {key: detail["work"][key] for key in ("keywords", "keyword_info") if key in detail["work"]}
            fields["has_video_text"] = bool(str(detail.get("video_text", {}).get("text") or "").strip())
            if any(indexed.get(key) != value for key, value in fields.items()):
                indexed.update(fields)
                self._write_json(self.snapshot_path, snapshot)

    def transcribe(self, work_id: int, engine: str) -> dict[str, Any]:
        from .model_mr_processing import PROVIDER_LOCK
        if not PROVIDER_LOCK.acquire(blocking=False):
            raise ModelMrUnavailable("自动处理或其他识别正在进行，请稍后查看，未重复提交。")
        try:
            result = self._transcribe(work_id, engine)
            # Keep a successful manual ASR candidate so a queued arrival reuses it.
            if engine == "doubao" and result.get("text") and not result.get("cached"):
                with _DETAIL_LOCK:
                    try:
                        detail = self.processing_detail(work_id)
                    except ModelMrUnavailable:
                        return result  # Desktop live mode has its own storage.
                    detail.setdefault("transcripts", []).append({"text": result["text"],
                        "source": "doubao-recording-asr-2.0", "created_at": str(int(time.time()))})
                    self._write_json(self._detail_path(work_id), detail)
            return result
        finally:
            PROVIDER_LOCK.release()

    def _transcribe(self, work_id: int, engine: str) -> dict[str, Any]:
        safe_id = self._safe_work_id(work_id)
        endpoint = "doubao-asr-transcription" if engine == "doubao" else "transcribe-video-text"
        try:
            source = self._json(
                f"/api/videos/{safe_id}/{endpoint}",
                method="POST",
                payload={},
                timeout=15 * 60,
                max_bytes=8 * 1024 * 1024,
            )
            return {
                "text": str(source.get("text") or ""),
                "engine": str(source.get("engine") or endpoint),
                "cached": False,
                "message": "识别完成，请核对后保存。",
            }
        except ModelMrUnavailable:
            detail = self.work_detail(safe_id)
            if engine == "doubao" and doubao_asr_is_configured():
                media = self.video_path(safe_id)
                if media is None:
                    raise ModelMrUnavailable("这条作品没有可识别的本地视频。")
                try:
                    return transcribe_video(media[0], safe_id)
                except DoubaoAsrUnavailable as error:
                    raise ModelMrUnavailable(str(error)) from error
            transcript = next(
                (
                    item
                    for item in detail.get("transcripts", [])
                    if isinstance(item, dict) and str(item.get("text") or "").strip()
                    and (engine != "doubao" or "doubao" in str(item.get("source") or "").casefold())
                ),
                None,
            )
            text = str((transcript or {}).get("text") or detail.get("video_text", {}).get("text") or "").strip()
            if not text:
                raise ModelMrUnavailable("云端还没有这条作品的识别文字；需等待下一次主人资料同步。")
            return {
                "text": text,
                "engine": str((transcript or {}).get("source") or "saved-official-text"),
                "cached": True,
                "message": "已载入此前保存的豆包/正式识别结果；云端没有重复计费。",
            }

    def video_path(self, work_id: int) -> tuple[Path, str] | None:
        safe_id = self._safe_work_id(work_id)
        try:
            detail = self.work_detail(safe_id)
        except ModelMrUnavailable:
            return None
        media_file = str(detail.get("work", {}).get("media_file") or "").strip().replace("\\", "/")
        if not media_file:
            return None
        root = self.media_root.resolve()
        target = (root / media_file).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        if not target.is_file():
            return None
        mime_type = mimetypes.guess_type(target.name)[0] or "video/mp4"
        return target, mime_type

    @property
    def transfer_map_path(self) -> Path:
        return self.snapshot_path.parent / "beijing-transfer-map.json"

    def import_beijing_work(
        self,
        *,
        source_work_id: str,
        source_revision: int,
        title: str,
        description: str,
        source_url: str,
        published_at: str,
        comments: list[dict[str, Any]],
        media_path: Path,
        media_sha256: str,
    ) -> dict[str, Any]:
        """Idempotently import one verified Beijing model-downloader work."""

        source_id = str(source_work_id or "").strip()
        digest = str(media_sha256 or "").strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", source_id):
            raise ValueError("来源作品编号无效。")
        revision = int(source_revision)
        if revision <= 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("来源作品版本无效。")
        source_media = Path(media_path).resolve(strict=True)
        if not source_media.is_file():
            raise ValueError("来源视频不存在。")

        with _DETAIL_LOCK:
            snapshot = self._snapshot() or {
                "version": SNAPSHOT_VERSION,
                "exported_at": int(time.time()),
                "scope": "single-owner-mobile-library",
                "excluded": [
                    "fans",
                    "admin",
                    "api_keys",
                    "local_paths",
                    "raw_database",
                    "raw_json",
                    "comment_media",
                ],
                "counts": {},
                "media_source_root": self.media_root.name,
                "works": [],
                "thoughts": [],
            }
            mapping = self._read_transfer_map()
            entry = mapping.get(source_id) if isinstance(mapping.get(source_id), dict) else {}
            work_id = int(entry.get("work_id") or 0)
            if not work_id:
                work_id = self._match_existing_work(snapshot, source_id, source_url)
            if not work_id:
                existing_ids = [
                    int(item.get("id") or 0)
                    for item in snapshot.get("works", [])
                    if isinstance(item, dict)
                ]
                work_id = max([999_999, *existing_ids]) + 1
            if int(entry.get("source_revision") or 0) > revision:
                return {"ok": True, "work_id": work_id, "status": "stale"}

            media_name = f"beijing-{source_id}-{digest[:16]}.mp4"
            self._copy_media(source_media, self.media_root / media_name, digest)

            detail_path = self._detail_path(work_id)
            previous_raw: dict[str, Any] = {}
            try:
                loaded = json.loads(detail_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous_raw = loaded
            except (OSError, json.JSONDecodeError):
                pass
            previous = self._clean_snapshot_detail(previous_raw, work_id) if previous_raw else {}
            previous_work = previous.get("work") if isinstance(previous.get("work"), dict) else {}
            if not previous_work:
                previous_work = next(
                    (
                        item
                        for item in snapshot.get("works", [])
                        if isinstance(item, dict) and int(item.get("id") or 0) == work_id
                    ),
                    {},
                )
            imported_title = str(entry.get("imported_title") or "")
            previous_title = str(previous_work.get("title") or "").strip()
            incoming_title = (str(title or "").strip() or f"抖音作品 {source_id}")[:120]
            preserve_previous_title = bool(
                previous_title
                and (
                    not entry
                    or (imported_title and previous_title != imported_title)
                )
            )
            active_title = previous_title if preserve_previous_title else incoming_title
            clean_comments = [
                self._clean_comment(item, index)
                for index, item in enumerate(comments, start=1)
                if isinstance(item, dict)
            ]
            work = {
                "id": work_id,
                "title": active_title,
                "description": str(description or previous_work.get("description") or ""),
                "url": str(
                    source_url
                    or previous_work.get("url")
                    or f"https://www.douyin.com/video/{source_id}"
                ),
                "published_at": str(published_at or previous_work.get("published_at") or ""),
                "has_video_text": bool(previous.get("video_text", {}).get("text")) if previous else False,
                "has_interpretation": bool(previous.get("interpretation", {}).get("text")) if previous else False,
                "comment_count": len(clean_comments),
                "media_available": True,
                "media_file": media_name,
                "keywords": list(previous_work.get("keywords") or []),
                "keyword_info": clean_keyword_info(previous_work.get("keyword_info"), previous_work.get("keywords")),
            }
            detail = {
                "version": SNAPSHOT_VERSION,
                "work": work,
                "video_text": previous.get("video_text", {}),
                "interpretation": previous.get("interpretation", {}),
                "transcripts": previous.get("transcripts", []),
                "comments": clean_comments,
                "stock_mentions": previous.get("stock_mentions", {}),
                "comment_total": len(clean_comments),
                "capabilities": {},
            }
            self._write_json(detail_path, self._clean_snapshot_detail(detail, work_id))

            works = [
                item
                for item in snapshot.get("works", [])
                if isinstance(item, dict) and int(item.get("id") or 0) != work_id
            ]
            works.append(work)
            works.sort(
                key=lambda item: (str(item.get("published_at") or ""), int(item.get("id") or 0)),
                reverse=True,
            )
            now = int(time.time())
            snapshot["version"] = SNAPSHOT_VERSION
            snapshot["works"] = works
            snapshot["exported_at"] = now
            snapshot["updated_at"] = now
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            counts.update(
                {
                    "works": len(works),
                    "media": sum(1 for item in works if item.get("media_file")),
                    "comments": sum(max(0, int(item.get("comment_count") or 0)) for item in works),
                }
            )
            snapshot["counts"] = counts
            mapping[source_id] = {
                "work_id": work_id,
                "source_revision": revision,
                "imported_title": incoming_title,
                "media_sha256": digest,
            }
            self._write_json(self.snapshot_path, snapshot)
            self._write_json(self.transfer_map_path, mapping)
            return {"ok": True, "work_id": work_id, "status": "imported"}

    def _read_transfer_map(self) -> dict[str, Any]:
        try:
            value = json.loads(self.transfer_map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _match_existing_work(snapshot: dict[str, Any], source_id: str, source_url: str) -> int:
        target_url = str(source_url or "").rstrip("/")
        for item in snapshot.get("works", []):
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("url") or "").rstrip("/")
            if candidate and (candidate == target_url or candidate.endswith(f"/video/{source_id}")):
                return int(item.get("id") or 0)
        return 0

    @staticmethod
    def _copy_media(source: Path, destination: Path, expected_sha256: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            if ModelMrClient._sha256_file(destination) == expected_sha256:
                return
            raise ModelMrUnavailable("模型先生目标视频摘要冲突。")
        handle, temporary_name = tempfile.mkstemp(
            prefix=".beijing-model-",
            suffix=".mp4",
            dir=destination.parent,
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            digest = hashlib.sha256()
            with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
                while True:
                    chunk = input_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            if digest.hexdigest() != expected_sha256:
                raise ModelMrUnavailable("模型先生来源视频摘要不匹配。")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def open_live_video(self, work_id: int, range_header: str = "") -> BinaryIO:
        safe_id = self._safe_work_id(work_id)
        source = self._json(f"/api/videos/{safe_id}", timeout=15, max_bytes=64 * 1024 * 1024)
        assets = source.get("assets") if isinstance(source.get("assets"), list) else []
        file_url = ""
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            candidate = str(asset.get("file_url") or "")
            if re.fullmatch(r"/api/assets/\d+/file", candidate):
                file_url = candidate
                break
        if not file_url:
            raise ModelMrUnavailable("这条作品没有可播放的本地视频。")
        headers = {"Accept": "video/mp4"}
        if range_header:
            headers["Range"] = range_header
        try:
            return urlopen(Request(f"{self.origin}{file_url}", headers=headers), timeout=30)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise ModelMrUnavailable("本地视频暂时无法读取。") from error

    def thoughts(self, *, limit: int = 200) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 300))
        try:
            source = self._json(f"/api/investment-thoughts?{urlencode({'limit': safe_limit})}")
        except ModelMrUnavailable:
            snapshot = self._require_snapshot()
            categories = snapshot.get("thoughts") if isinstance(snapshot.get("thoughts"), list) else []
            cleaned = [self._clean_thought(item) for item in categories[:safe_limit] if isinstance(item, dict)]
            return {
                "categories": [item for item in cleaned if item.get("name")],
                "count": len(cleaned),
                "purpose": "模型先生投资思路只读索引",
                "mode": "owner-mobile-library" if int(snapshot.get("version") or 0) >= 2 else "sanitized-snapshot",
            }
        categories = source.get("categories") if isinstance(source.get("categories"), list) else []
        cleaned = [self._clean_thought(item) for item in categories if isinstance(item, dict) and item.get("name")]
        return {"categories": cleaned, "count": len(cleaned), "purpose": "模型先生投资思路只读索引"}

    def thought_works(self, category_id: int, *, limit: int = 24, offset: int = 0, query: str = "") -> dict[str, Any]:
        safe_id = self._safe_work_id(category_id)
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, min(int(offset), 500_000))
        search = str(query or "").strip()[:120]
        try:
            source = self._json(f"/api/investment-thoughts?{urlencode({'category_id': safe_id, 'limit': 500})}")
        except ModelMrUnavailable:
            snapshot = self._require_snapshot()
            categories = [self._clean_thought(item) for item in snapshot.get("thoughts", []) if isinstance(item, dict)]
            works = [item for item in snapshot.get("works", []) if isinstance(item, dict)]
            links_available = isinstance(snapshot.get("thought_links"), dict)
            links = clean_links(snapshot.get("thought_links"), {int(item["id"]) for item in works}, {item["id"] for item in categories})
            related = {safe_id, *(item["id"] for item in categories if item.get("parent_id") == safe_id)}
            items = [self._clean_snapshot_work(item) for item in works
                     if related.intersection(links.get(str(item["id"]), []))]
        else:
            categories = [self._clean_thought(item) for item in source.get("categories", []) if isinstance(item, dict)]
            links_available = True
            items = [self._clean_work({**item, "url": item.get("source_url", ""),
                                      "keyword_info": {"keywords": item.get("keywords", [])}})
                     for item in source.get("items", []) if isinstance(item, dict)]
        category = next((item for item in categories if item["id"] == safe_id), None)
        if category is None:
            raise ValueError("投资思路分类不存在。")
        keywords = clean_words([word for item in items for word in item.get("keywords", [])], 40)
        if search:
            items = [item for item in items if search.casefold() in " ".join([
                item.get("title", ""), item.get("description", ""), *item.get("keywords", [])]).casefold()]
        total = len(items)
        page = items[safe_offset:safe_offset + safe_limit]
        return {"category": category, "items": page, "count": len(page), "total": total,
                "offset": safe_offset, "has_more": safe_offset + len(page) < total,
                "keywords": keywords, "links_available": links_available,
                "message": "" if links_available else "分类目录已同步，但作品关联尚未同步。请补齐分类元数据。"}

    def save_keywords(self, work_id: int, categories: dict[str, Any], keywords: list[Any], expected_revision: str,
                      *, ai_info: dict[str, Any] | None = None) -> dict[str, Any]:
        safe_id = self._safe_work_id(work_id)
        if not isinstance(categories, dict) or not isinstance(keywords, list):
            raise ValueError("关键词格式不正确。")
        if any(not isinstance(words, list) or len(words) > 8 for words in categories.values()) or len(keywords) > 80:
            raise ValueError("每类最多 8 个关键词，全部最多 80 个。")
        if set(categories) - set(KEYWORD_CATEGORIES):
            raise ValueError("关键词分类不支持。")
        all_words = [word for values in categories.values() for word in values] + keywords
        if any(not isinstance(word, str) or not clean_words([word]) for word in all_words) or len(set(all_words)) > 80:
            raise ValueError("关键词须为不含路径的短文字，合计最多 80 个。")
        with _DETAIL_LOCK:
            snapshot = self._require_snapshot()
            indexed = next((item for item in snapshot["works"] if int(item.get("id") or 0) == safe_id), None)
            if indexed is None:
                raise ValueError("作品不存在。")
            # Read the exported detail directly: never invoke a model or sidecar on a save.
            try:
                detail = json.loads(self._detail_path(safe_id).read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ModelMrUnavailable("作品详情未同步完整，未保存关键词。") from error
            current_work = detail.get("work") or indexed
            revision = keyword_revision(current_work.get("keyword_info"), current_work.get("keywords"))
            if expected_revision != revision:
                raise ValueError("关键词已被更新，请收起并重新打开后再编辑。")
            if ai_info is not None:
                from .model_mr_keywords import source_hash
                if ai_info.get("source_hash") != source_hash(str(detail.get("video_text", {}).get("text") or "")):
                    raise ValueError("视频原文已变化，提炼结果未覆盖现有资料。")
            info = clean_keyword_info({"categories": categories, "keywords": keywords, "model": "manual",
                                       "schema_version": "owner-keywords/v1", "confirmed_at": str(int(time.time())),
                                       "edited_by_owner": True})
            if ai_info is not None:
                info = clean_keyword_info({**ai_info, "categories": categories, "keywords": keywords,
                                           "confirmed_at": str(int(time.time())), "edited_by_owner": False})
            indexed.update(keywords=info["keywords"], keyword_info=info)
            detail.setdefault("work", {"id": safe_id}).update(keywords=info["keywords"], keyword_info=info)
            # Keep every unrelated field byte-for-value, including comments, text and media references.
            self._write_json(self._detail_path(safe_id), detail)
            self._write_json(self.snapshot_path, snapshot)
        return {"ok": True, "saved": True, "keyword_info": info, "keywords": info["keywords"],
                "keyword_revision": keyword_revision(info), "mode": "owner-mobile-library"}

    def chat_config(self) -> dict[str, Any]:
        try:
            source = self._json("/api/chat/config", timeout=3)
        except ModelMrUnavailable:
            if self._snapshot() is None:
                raise
            return {
                "enabled": False,
                "default_model": "",
                "models": [],
                "message": "云端主人资料库已连接；智能问答需单独配置模型服务。",
            }
        models = source.get("models") if isinstance(source.get("models"), list) else []
        return {
            "enabled": bool(source.get("enabled")),
            "default_model": str(source.get("default_model") or ""),
            "models": [
                {
                    "id": str(item.get("id") or ""),
                    "label": str(item.get("label") or item.get("id") or ""),
                    "description": str(item.get("description") or ""),
                }
                for item in models
                if isinstance(item, dict) and item.get("id")
            ],
            "message": "模型先生智能问答已启用。" if source.get("enabled") else "模型先生智能问答当前未开启。",
        }

    def chat(self, messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        cleaned: list[dict[str, str]] = []
        for item in messages[-12:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                cleaned.append({"role": role, "content": content[:6000]})
        if not cleaned:
            raise ValueError("聊天内容不能为空。")
        return self._json(
            "/api/chat",
            method="POST",
            payload={"messages": cleaned, "model": model, "account": "模型先生"},
            timeout=90,
        )

    def _json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 10,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.origin}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(max_bytes)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise ModelMrUnavailable("模型先生本机服务未连接。") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ModelMrUnavailable("模型先生服务返回了无效数据。") from error
        if not isinstance(value, dict):
            raise ModelMrUnavailable("模型先生服务返回格式不正确。")
        if value.get("error"):
            raise ModelMrUnavailable(str(value["error"]))
        return value

    def write_owner_library(
        self,
        output: Path,
        *,
        media_manifest: Path,
        works_limit: int = 500,
        thoughts_limit: int = 300,
    ) -> dict[str, Any]:
        source = self._json(
            f"/api/videos?{urlencode({'limit': max(1, min(works_limit, 500)), 'account': '模型先生'})}",
            timeout=30,
            max_bytes=32 * 1024 * 1024,
        )
        source_works = [item for item in source.get("items", []) if isinstance(item, dict)]
        thoughts = self.thoughts(limit=thoughts_limit).get("categories", [])
        thought_source = self._json("/api/investment-thoughts?limit=500", timeout=30)
        thought_links = clean_links(thought_source.get("links_by_video"),
                                    {int(item["id"]) for item in source_works},
                                    {int(item["id"]) for item in thoughts})
        media_lookup, backup_root = self._media_lookup(media_manifest)
        details_root = output.parent / "details"
        details_root.mkdir(parents=True, exist_ok=True)
        works: list[dict[str, Any]] = []
        transcript_count = 0
        comment_count = 0
        media_count = 0
        for source_work in source_works:
            work_id = self._safe_work_id(source_work.get("id"))
            raw_detail = self._json(f"/api/videos/{work_id}", timeout=30, max_bytes=64 * 1024 * 1024)
            detail = self._clean_detail(raw_detail, work_id=work_id, live=False)
            try:
                raw_stock_mentions = self._json(
                    f"/api/videos/{work_id}/stock-mentions?limit=20",
                    timeout=30,
                    max_bytes=4 * 1024 * 1024,
                )
            except ModelMrUnavailable:
                raw_stock_mentions = {}
            source_comments = raw_detail.get("comments") if isinstance(raw_detail.get("comments"), list) else []
            comment_id_map = {
                int(item.get("id") or 0): index
                for index, item in enumerate(source_comments, start=1)
                if isinstance(item, dict) and int(item.get("id") or 0) > 0
            }
            detail["stock_mentions"] = self._clean_stock_mentions(raw_stock_mentions, comment_id_map)
            media_file = self._match_media(raw_detail, media_lookup)
            if media_file:
                detail["work"]["media_file"] = media_file
                detail["work"]["video_url"] = f"/api/model-mr/works/{work_id}/video"
                detail["work"]["media_available"] = True
                media_count += 1
            else:
                detail["work"]["media_file"] = ""
                detail["work"]["video_url"] = ""
                detail["work"]["media_available"] = False
            summary = self._clean_snapshot_work({**self._clean_work(source_work), **detail["work"]})
            works.append(summary)
            transcript_count += len(detail.get("transcripts", []))
            comment_count += len(detail.get("comments", []))
            self._write_json(details_root / f"{work_id}.json", detail)
        payload = {
            "version": SNAPSHOT_VERSION,
            "exported_at": int(time.time()),
            "scope": "single-owner-mobile-library",
            "excluded": ["fans", "admin", "api_keys", "local_paths", "raw_database", "raw_json", "comment_media"],
            "counts": {
                "works": len(works),
                "media": media_count,
                "transcripts": transcript_count,
                "comments": comment_count,
                "thoughts": len(thoughts),
            },
            "media_source_root": backup_root.name,
            "works": works,
            "thoughts": thoughts,
            "thought_links": thought_links,
        }
        self._write_json(output, payload)
        return {"path": str(output), **payload["counts"], "media_root": str(backup_root)}

    def write_public_snapshot(self, output: Path, *, works_limit: int = 500, thoughts_limit: int = 300) -> dict[str, Any]:
        works = self.works(limit=works_limit).get("items", [])
        thoughts = self.thoughts(limit=thoughts_limit).get("categories", [])
        payload = {
            "version": 1,
            "exported_at": int(time.time()),
            "scope": "works-and-investment-thoughts-only",
            "excluded": ["videos", "comments", "fans", "admin", "api_keys", "local_paths", "raw_database"],
            "works": works,
            "thoughts": thoughts,
        }
        self._write_json(output, payload)
        return {"path": str(output), "works": len(works), "thoughts": len(thoughts)}

    def _snapshot(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or int(value.get("version") or 0) not in SUPPORTED_SNAPSHOT_VERSIONS:
            return None
        if not isinstance(value.get("works"), list) or not isinstance(value.get("thoughts"), list):
            return None
        return value

    def _require_snapshot(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        if snapshot is None:
            raise ModelMrUnavailable("模型先生本机服务未连接，主人资料也尚未部署。")
        return snapshot

    def _detail_path(self, work_id: int) -> Path:
        return self.details_root / f"{self._safe_work_id(work_id)}.json"

    def _write_detail(self, work_id: int, detail: dict[str, Any]) -> None:
        with _DETAIL_LOCK:
            self._write_json(self._detail_path(work_id), self._clean_snapshot_detail(detail, work_id))

    @staticmethod
    def _write_json(output: Path, payload: dict[str, Any]) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".json", dir=output.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _safe_work_id(value: object) -> int:
        try:
            work_id = int(value or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("作品编号无效。") from error
        if work_id <= 0:
            raise ValueError("作品编号无效。")
        return work_id

    @staticmethod
    def _clean_work(item: dict[str, Any]) -> dict[str, Any]:
        keyword_info = clean_keyword_info(item.get("keyword_info"), item.get("keywords"))
        description = str(item.get("description") or "")
        if description.startswith("由 model-"):
            description = ""
        primary_asset = item.get("primary_asset") if isinstance(item.get("primary_asset"), dict) else {}
        work_id = int(item.get("id") or 0)
        media_available = bool(primary_asset and str(primary_asset.get("mime_type") or "").startswith("video/"))
        return {
            "id": work_id,
            "title": str(item.get("active_title") or item.get("title") or "未命名作品"),
            "description": description,
            "url": str(item.get("url") or ""),
            "published_at": str(item.get("published_at") or item.get("discovered_at") or ""),
            "has_video_text": bool(item.get("has_video_text")),
            "has_interpretation": bool(item.get("has_interpretation")),
            "comment_count": max(0, int(item.get("comment_count") or 0)),
            "media_available": media_available,
            "video_url": f"/api/model-mr/works/{work_id}/video" if media_available and work_id else "",
            "media_file": "",
            "keywords": keyword_info["keywords"],
            "keyword_info": keyword_info,
            "keyword_revision": keyword_revision(keyword_info),
        }

    @classmethod
    def _clean_snapshot_work(cls, item: dict[str, Any]) -> dict[str, Any]:
        work_id = int(item.get("id") or 0)
        info = clean_keyword_info(item.get("keyword_info"), item.get("keywords"))
        media_file = str(item.get("media_file") or "").strip().replace("\\", "/")
        media_available = bool(item.get("media_available") or media_file)
        return {
            "id": work_id,
            "title": str(item.get("title") or "未命名作品"),
            "description": str(item.get("description") or ""),
            "url": str(item.get("url") or ""),
            "published_at": str(item.get("published_at") or ""),
            "has_video_text": bool(item.get("has_video_text")),
            "has_interpretation": bool(item.get("has_interpretation")),
            "comment_count": max(0, int(item.get("comment_count") or 0)),
            "media_available": media_available,
            "video_url": f"/api/model-mr/works/{work_id}/video" if media_available and work_id else "",
            "media_file": media_file,
            "keywords": info["keywords"],
            "keyword_info": info,
            "keyword_revision": keyword_revision(info),
        }

    @classmethod
    def _clean_detail(cls, value: dict[str, Any], *, work_id: int, live: bool) -> dict[str, Any]:
        video = value.get("video") if isinstance(value.get("video"), dict) else {}
        summary_source = dict(video)
        title_info = value.get("title_info") if isinstance(value.get("title_info"), dict) else {}
        notes = value.get("notes") if isinstance(value.get("notes"), dict) else {}
        video_text_note = notes.get("video_text") if isinstance(notes.get("video_text"), dict) else {}
        interpretation_note = notes.get("interpretation") if isinstance(notes.get("interpretation"), dict) else {}
        comments_source = value.get("comments") if isinstance(value.get("comments"), list) else []
        summary_source["active_title"] = str(title_info.get("active_title") or video.get("active_title") or "")
        summary_source["has_video_text"] = bool(str(video_text_note.get("text") or "").strip())
        summary_source["has_interpretation"] = bool(str(interpretation_note.get("text") or "").strip())
        summary_source["comment_count"] = int(value.get("comment_total") or len(comments_source))
        assets = value.get("assets") if isinstance(value.get("assets"), list) else []
        primary = next((asset for asset in assets if isinstance(asset, dict) and str(asset.get("mime_type") or "").startswith("video/")), None)
        summary_source["primary_asset"] = primary
        summary_source["keyword_info"] = value.get("keyword_info") if isinstance(value.get("keyword_info"), dict) else {}
        work = cls._clean_work({"id": work_id, **summary_source})
        transcripts = [cls._clean_transcript(item) for item in value.get("transcripts", []) if isinstance(item, dict)] if isinstance(value.get("transcripts"), list) else []
        comments = [cls._clean_comment(item, index) for index, item in enumerate(comments_source, start=1) if isinstance(item, dict)]
        return {
            "version": SNAPSHOT_VERSION,
            "work": work,
            "video_text": {
                "text": str(video_text_note.get("text") or ""),
                "official": bool(video_text_note.get("official")),
                "source": "official-note" if video_text_note else "",
                "updated_at": str(video_text_note.get("updated_at") or ""),
            },
            "interpretation": {
                "text": str(interpretation_note.get("text") or ""),
                "updated_at": str(interpretation_note.get("updated_at") or ""),
            },
            "transcripts": transcripts,
            "comments": comments,
            "comment_total": max(len(comments), int(value.get("comment_total") or 0)),
            "capabilities": {
                "video": bool(primary),
                "save_title": True,
                "save_video_text": True,
                "transcribe_video": live,
                "doubao_asr": live,
                "comments": True,
            },
        }

    @classmethod
    def _clean_snapshot_detail(cls, value: dict[str, Any], work_id: int) -> dict[str, Any]:
        work = cls._clean_snapshot_work(value.get("work") if isinstance(value.get("work"), dict) else {"id": work_id})
        if work["id"] != work_id:
            work["id"] = work_id
            work["video_url"] = f"/api/model-mr/works/{work_id}/video" if work["media_available"] else ""
        video_text = value.get("video_text") if isinstance(value.get("video_text"), dict) else {}
        interpretation = value.get("interpretation") if isinstance(value.get("interpretation"), dict) else {}
        transcripts = [cls._clean_transcript(item) for item in value.get("transcripts", []) if isinstance(item, dict)] if isinstance(value.get("transcripts"), list) else []
        comments = [cls._clean_comment(item, index) for index, item in enumerate(value.get("comments", []), start=1) if isinstance(item, dict)] if isinstance(value.get("comments"), list) else []
        return {
            "version": SNAPSHOT_VERSION,
            "work": work,
            "video_text": {
                "text": str(video_text.get("text") or ""),
                "official": bool(video_text.get("official")),
                "source": str(video_text.get("source") or ""),
                "updated_at": str(video_text.get("updated_at") or ""),
            },
            "interpretation": {
                "text": str(interpretation.get("text") or ""),
                "updated_at": str(interpretation.get("updated_at") or ""),
            },
            "transcripts": transcripts,
            "comments": comments,
            "stock_mentions": cls._clean_stock_mentions(
                value.get("stock_mentions") if isinstance(value.get("stock_mentions"), dict) else {},
            ),
            "comment_total": max(len(comments), int(value.get("comment_total") or 0)),
            "capabilities": {
                "video": bool(work["media_available"]),
                "save_title": True,
                "save_video_text": True,
                "transcribe_video": bool(transcripts or video_text.get("text")),
                "doubao_asr": doubao_asr_is_configured()
                or any("doubao" in str(item.get("source") or "").casefold() for item in transcripts),
                "comments": True,
            },
        }

    @staticmethod
    def _clean_transcript(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "text": str(item.get("text") or ""),
            "source": str(item.get("source") or ""),
            "language": str(item.get("language") or "zh"),
            "created_at": str(item.get("created_at") or ""),
        }

    @staticmethod
    def _clean_comment(item: dict[str, Any], index: int) -> dict[str, Any]:
        raw = item.get("raw_json") if isinstance(item.get("raw_json"), dict) else {}
        kind = str(item.get("kind") or raw.get("kind") or "user_comment")
        reply_depth = max(0, min(int(item.get("reply_depth") or raw.get("reply_depth") or 0), 8))
        preserved_thread_key = str(item.get("thread_key") or "").strip().lower()
        if re.fullmatch(r"[a-f0-9]{12}", preserved_thread_key):
            thread_key = preserved_thread_key
        else:
            thread_source = str(raw.get("thread_id") or raw.get("root_source_comment_id") or index)
            thread_key = hashlib.sha256(thread_source.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return {
            "id": index,
            "author": str(item.get("author") or "匿名用户")[:80],
            "text": str(item.get("text") or "")[:20_000],
            "like_count": max(0, int(item.get("like_count") or 0)),
            "reply_count": max(0, int(item.get("reply_count") or 0)),
            "published_at": str(item.get("published_at") or ""),
            "kind": kind,
            "reply_depth": reply_depth,
            "thread_key": thread_key,
            "author_liked": bool(item.get("author_liked") or raw.get("author_liked")),
        }

    @staticmethod
    def _clean_stock_mentions(
        value: dict[str, Any],
        comment_id_map: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for index, item in enumerate(value.get("items", []), start=1):
            if not isinstance(item, dict):
                continue
            mapped_ids: list[int] = []
            for raw_id in item.get("comment_ids", []) if isinstance(item.get("comment_ids"), list) else []:
                try:
                    comment_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                mapped = comment_id_map.get(comment_id) if comment_id_map is not None else comment_id
                if mapped and mapped not in mapped_ids:
                    mapped_ids.append(mapped)
            items.append(
                {
                    "rank": max(1, int(item.get("rank") or index)),
                    "name": str(item.get("name") or "")[:80],
                    "code": str(item.get("code") or "")[:12],
                    "comment_count": max(0, int(item.get("comment_count") or 0)),
                    "mention_count": max(0, int(item.get("mention_count") or 0)),
                    "fan_comment_count": max(0, int(item.get("fan_comment_count") or 0)),
                    "author_comment_count": max(0, int(item.get("author_comment_count") or 0)),
                    "examples": [str(text)[:180] for text in item.get("examples", [])[:3] if str(text).strip()]
                    if isinstance(item.get("examples"), list)
                    else [],
                    "comment_ids": mapped_ids[:200],
                }
            )
        uncertain: list[dict[str, Any]] = []
        for item in value.get("uncertain", []) if isinstance(value.get("uncertain"), list) else []:
            if not isinstance(item, dict):
                continue
            uncertain.append(
                {
                    "text": str(item.get("text") or "")[:80],
                    "comment_count": max(0, int(item.get("comment_count") or 0)),
                    "candidates": [str(name)[:80] for name in item.get("candidates", [])[:8] if str(name).strip()]
                    if isinstance(item.get("candidates"), list)
                    else [],
                }
            )
        return {
            "total_comments": max(0, int(value.get("total_comments") or 0)),
            "stock_count": max(len(items), int(value.get("stock_count") or 0)),
            "items": items[:20],
            "uncertain": uncertain[:20],
            "method": "local-security-master" if items or value.get("method") == "local-security-master" else "",
            "api_used": False,
            "message": str(value.get("message") or "")[:240],
        }

    @staticmethod
    def _clean_thought(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(item.get("id") or 0),
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
            "level": int(item.get("level") or 1),
            "parent_id": int(item["parent_id"]) if item.get("parent_id") is not None else None,
            "video_count": int(item.get("video_count") or 0),
        }

    @staticmethod
    def _media_lookup(media_manifest: Path) -> tuple[dict[str, str], Path]:
        try:
            manifest = json.loads(media_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("压缩视频清单无法读取。") from error
        backup_root = Path(str(manifest.get("backup_root") or ""))
        if not backup_root.is_dir():
            raise ValueError("压缩视频目录不存在。")
        lookup: dict[str, str] = {}
        basename_candidates: dict[str, list[str]] = {}
        for item in manifest.get("items", []):
            if not isinstance(item, dict) or item.get("status") != "ok":
                continue
            source = Path(str(item.get("source") or ""))
            target = Path(str(item.get("target") or ""))
            if not target.is_file():
                continue
            try:
                relative = target.resolve().relative_to(backup_root.resolve()).as_posix()
            except ValueError:
                continue
            lookup[str(source.resolve()).casefold()] = relative
            basename_candidates.setdefault(source.name.casefold(), []).append(relative)
        for name, candidates in basename_candidates.items():
            if len(candidates) == 1:
                lookup[f"name:{name}"] = candidates[0]
        return lookup, backup_root

    @staticmethod
    def _match_media(raw_detail: dict[str, Any], lookup: dict[str, str]) -> str:
        assets = raw_detail.get("assets") if isinstance(raw_detail.get("assets"), list) else []
        for asset in assets:
            if not isinstance(asset, dict) or not str(asset.get("mime_type") or "").startswith("video/"):
                continue
            local_path = str(asset.get("local_path") or "").strip()
            if local_path:
                exact = lookup.get(str(Path(local_path).resolve()).casefold())
                if exact:
                    return exact
            name = str(asset.get("original_name") or "").strip().casefold()
            fallback = lookup.get(f"name:{name}") if name else None
            if fallback:
                return fallback
        return ""


MODEL_MR = ModelMrClient()
