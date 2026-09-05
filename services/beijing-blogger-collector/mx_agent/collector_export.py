from __future__ import annotations

import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .storage import Storage, from_json
from .transfer_contract import (
    MAX_COMMENT_ITEMS,
    MAX_MANIFEST_MEDIA,
    TransferContractError,
    V1_MEDIA_MIME_TYPES,
    build_comment_bundle,
    new_manifest,
    safe_cross_platform_filename,
    sha256_file,
)
from .transfer_outbox import TransferOutbox

if TYPE_CHECKING:
    from .creators import CreatorRegistry


CHINA_TZ = timezone(timedelta(hours=8))
VALID_COMMENT_KINDS = {
    "user_comment",
    "user_reply",
    "author_comment",
    "author_reply",
}
VALID_COMMENT_SECTIONS = {"fan_comment", "author_interaction"}
MEDIA_ROLES = {
    "video": "video",
    "image": "image",
    "audio": "audio",
    "cover": "cover",
}
WIRE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")


class CollectorExportError(RuntimeError):
    """A diagnostic export failure that collection may safely retry later."""


@dataclass(frozen=True)
class CollectedContentExport:
    """Protocol data plus local-only artifact metadata prepared for the outbox."""

    creator: dict[str, Any]
    work: dict[str, Any]
    media: tuple[dict[str, Any], ...]
    media_artifacts: tuple[dict[str, Any], ...]
    comment_snapshot: dict[str, Any]
    comment_bundle_bytes: bytes
    captured_at: str
    missing_media_count: int
    omitted_media_count: int
    unsupported_media_count: int

    @property
    def comment_count(self) -> int:
        return int(self.comment_snapshot["captured_count"])


def _bounded_text(value: Any, limit: int) -> str:
    # Public platform text may contain newlines, tabs or other invisible
    # control bytes. Keep the stored source untouched, but normalize the wire
    # copy so one malformed comment cannot invalidate the entire signed bundle.
    text = WIRE_CONTROL_CHARACTERS.sub(" ", str(value or ""))
    return text.strip()[:limit]


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "是"}:
        return True
    if text in {"0", "false", "no", "n", "否"}:
        return False
    return None


def _aware_iso(
    value: Any,
    *,
    field: str,
    fallback: str | None = None,
    required: bool = True,
) -> str:
    text = str(value or "").strip()
    if not text:
        text = str(fallback or "").strip()
    if not text:
        if required:
            raise CollectorExportError(f"{field} 缺少可用时间。")
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CollectorExportError(f"{field} 不是有效的 ISO 时间。") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.isoformat()


def _iso_order(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _raw_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    parsed = from_json(str(value or ""), {})
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _safe_filename(value: Any, *, digest: str, mime_type: str) -> str:
    candidate = Path(str(value or "").strip()).name
    if candidate:
        try:
            return safe_cross_platform_filename(candidate)
        except TransferContractError:
            pass
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    extension = re.sub(r"[^A-Za-z0-9.]", "", extension)[:12] or ".bin"
    return f"media-{digest[:24]}{extension}"


def _comment_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str, int]:
    raw = _raw_mapping(row.get("raw_json"))
    source_id = str(row.get("source_comment_id") or "")
    display = _integer(raw.get("display_order"), -1)
    return (
        0 if display >= 0 else 1,
        display if display >= 0 else 0,
        source_id,
        _integer(row.get("id"), 0),
    )


def _comment_freshness(row: Mapping[str, Any], *, fallback: str) -> tuple[datetime, int]:
    captured_at = _aware_iso(
        row.get("captured_at"),
        field="comment.captured_at",
        fallback=fallback,
    )
    return _iso_order(captured_at), _integer(row.get("id"), 0)


def _normalize_comments(
    rows: list[dict[str, Any]],
    *,
    creator_name: str,
    fallback_captured_at: str,
) -> list[dict[str, Any]]:
    # Storage uniqueness also includes source, while the wire identity does not.
    # Keep the newest deterministic row for a duplicated source_comment_id.
    newest: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = str(row.get("source_comment_id") or "").strip()
        if not source_id:
            raise CollectorExportError("评论缺少 source_comment_id。")
        previous = newest.get(source_id)
        if previous is None or _comment_freshness(
            row, fallback=fallback_captured_at
        ) > _comment_freshness(previous, fallback=fallback_captured_at):
            newest[source_id] = row

    normalized: list[dict[str, Any]] = []
    for fallback_order, row in enumerate(sorted(newest.values(), key=_comment_sort_key)):
        raw = _raw_mapping(row.get("raw_json"))
        source_id = str(row.get("source_comment_id") or "").strip()
        parent_id = str(raw.get("parent_source_comment_id") or "").strip()
        root_id = str(
            raw.get("root_source_comment_id")
            or raw.get("thread_root_source_comment_id")
            or raw.get("thread_id")
            or ""
        ).strip()
        reply_to_id = str(raw.get("reply_to_comment_id") or "").strip()
        author = _bounded_text(row.get("author"), 160)
        stored_kind = str(raw.get("kind") or "").strip()
        is_creator = bool(raw.get("is_creator")) or stored_kind.startswith("author_")
        if creator_name and author.casefold() == creator_name.casefold():
            is_creator = True
        is_reply = bool(parent_id)
        if is_creator:
            kind = "author_reply" if is_reply else "author_comment"
        elif stored_kind in VALID_COMMENT_KINDS:
            kind = stored_kind
        else:
            kind = "user_reply" if is_reply else "user_comment"
        section = str(raw.get("section") or "").strip()
        if section not in VALID_COMMENT_SECTIONS:
            section = "author_interaction" if is_creator else "fan_comment"
        text = _bounded_text(row.get("text"), 20_000)
        if not text:
            raise CollectorExportError(f"评论 {source_id} 缺少正文。")
        display_order = _integer(raw.get("display_order"), fallback_order)
        if display_order < 0:
            display_order = fallback_order
        author_liked = _optional_bool(
            raw.get("author_liked")
            if "author_liked" in raw
            else raw.get("author_like_status")
        )
        normalized.append(
            {
                "source_comment_id": source_id,
                "parent_source_comment_id": parent_id,
                "root_source_comment_id": root_id,
                "reply_to_comment_id": reply_to_id,
                "author": author,
                "is_creator": is_creator,
                "text": text,
                "like_count": max(0, _integer(row.get("like_count"))),
                "reply_count": max(0, _integer(row.get("reply_count"))),
                "published_at": _aware_iso(
                    row.get("published_at"),
                    field=f"comment[{source_id}].published_at",
                    required=False,
                ),
                "captured_at": _aware_iso(
                    row.get("captured_at"),
                    field=f"comment[{source_id}].captured_at",
                    fallback=fallback_captured_at,
                ),
                "kind": kind,
                "section": section,
                "sentiment": _bounded_text(row.get("sentiment") or "neutral", 32),
                "risk_level": _bounded_text(row.get("risk_level") or "normal", 32),
                "author_liked": author_liked,
                "low_value": bool(raw.get("low_value", False)),
                "ip_label": _bounded_text(raw.get("ip_label"), 80),
                "public_label": _bounded_text(raw.get("public_label"), 160),
                "actual_reply_user": _bounded_text(raw.get("actual_reply_user"), 160),
                "display_order": display_order,
            }
        )
    return normalized


def _reply_diagnostics(comments: list[dict[str, Any]]) -> dict[str, int]:
    by_id = {item["source_comment_id"]: item for item in comments}
    roots = {
        source_id: item
        for source_id, item in by_id.items()
        if not item["parent_source_comment_id"] and int(item["reply_count"]) > 0
    }
    captured_by_root = {source_id: 0 for source_id in roots}
    orphan_replies = 0

    for item in comments:
        parent_id = item["parent_source_comment_id"]
        if not parent_id:
            continue
        declared_root = item["root_source_comment_id"]
        if declared_root in roots:
            captured_by_root[declared_root] += 1
            continue
        current_id = parent_id
        seen: set[str] = set()
        resolved = ""
        while current_id and current_id not in seen:
            if current_id in roots:
                resolved = current_id
                break
            seen.add(current_id)
            parent = by_id.get(current_id)
            current_id = str((parent or {}).get("parent_source_comment_id") or "")
        if resolved:
            captured_by_root[resolved] += 1
        else:
            orphan_replies += 1

    missing_replies = 0
    incomplete = 0
    for source_id, root in roots.items():
        missing = max(0, int(root["reply_count"]) - captured_by_root[source_id])
        if missing:
            incomplete += 1
            missing_replies += missing
    return {
        "reply_groups": len(roots),
        "reply_groups_incomplete": incomplete,
        "missing_replies": missing_replies,
        "orphan_replies": orphan_replies,
    }


class CollectorContentReadyAdapter:
    """Convert a completed Beijing collection cycle into one outbox item.

    Instances are directly callable with the ``CreatorSyncService`` callback
    signature: ``adapter(video_id, creator_runtime_key)``.
    """

    def __init__(
        self,
        storage: Storage,
        registry: CreatorRegistry,
        outbox: TransferOutbox,
        *,
        artifact_dir: Path,
        collector_node_id: str,
        collector_key_id: str,
        collector_version: str,
        max_comments: int = MAX_COMMENT_ITEMS,
    ) -> None:
        self.storage = storage
        self.registry = registry
        self.outbox = outbox
        self.artifact_dir = Path(artifact_dir)
        self.collector_node_id = str(collector_node_id or "").strip()
        self.collector_key_id = str(collector_key_id or "").strip()
        self.collector_version = str(collector_version or "").strip()
        requested_limit = _integer(max_comments, 0)
        if requested_limit < 1 or requested_limit > MAX_COMMENT_ITEMS:
            raise ValueError(f"max_comments 必须在 1 到 {MAX_COMMENT_ITEMS} 之间。")
        self.max_comments = requested_limit

    def __call__(self, video_id: int, creator_runtime_key: str) -> dict[str, Any]:
        return self.export_and_enqueue(video_id, creator_runtime_key)

    def export(self, video_id: int, creator_runtime_key: str) -> CollectedContentExport:
        creator_record = self.registry.get(creator_runtime_key)
        video = self.storage.get_video(int(video_id))
        if not video:
            raise CollectorExportError(f"作品 {video_id} 不存在，无法生成传输清单。")

        creator_name = str(creator_record.get("name") or "").strip()
        video_author = str(video.get("author") or "").strip()
        if video_author and creator_name and video_author.casefold() != creator_name.casefold():
            raise CollectorExportError("作品与回调博主不匹配，已拒绝跨博主导出。")

        creator_uuid = str(creator_record.get("creator_uuid") or "").strip()
        try:
            creator_uuid = str(uuid.UUID(creator_uuid))
        except (AttributeError, TypeError, ValueError) as exc:
            raise CollectorExportError("博主缺少有效的不可变 creator_uuid。") from exc
        platform = str(creator_record.get("platform") or "douyin").strip()
        platform_user_id = str(creator_record.get("platform_user_id") or "").strip()
        if not platform_user_id:
            raise CollectorExportError("博主缺少已持久化的 platform_user_id。")

        video_raw = _raw_mapping(video.get("raw_json"))
        source_work_id = str(
            video_raw.get("douyin_aweme_id")
            or video_raw.get("aweme_id")
            or video_raw.get("source_work_id")
            or video.get("source_video_id")
            or ""
        ).strip()
        if not source_work_id:
            raise CollectorExportError("作品缺少稳定的 source_work_id。")
        fallback_captured_at = _aware_iso(
            video.get("discovered_at"),
            field="video.discovered_at",
        )

        rows = self.storage.list_comments(int(video_id), limit=self.max_comments)
        comments = _normalize_comments(
            rows,
            creator_name=creator_name,
            fallback_captured_at=fallback_captured_at,
        )
        bundle_bytes, bundle_descriptor = build_comment_bundle(comments)
        diagnostics = _reply_diagnostics(comments)
        total_comments = self.storage.count_comments(int(video_id))
        remote_total = max(
            (
                _integer(_raw_mapping(row.get("raw_json")).get("remote_total"), 0)
                for row in rows
            ),
            default=0,
        )
        expected_uncapped = max(
            total_comments,
            remote_total,
            len(comments) + diagnostics["missing_replies"],
        )
        expected_total = min(MAX_COMMENT_ITEMS, expected_uncapped)
        captured_at = max(
            (item["captured_at"] for item in comments),
            key=_iso_order,
            default=fallback_captured_at,
        )
        comment_snapshot = {
            "snapshot_id": bundle_descriptor["bundle_id"],
            "captured_at": captured_at,
            "complete": (
                expected_uncapped <= len(comments)
                and diagnostics["missing_replies"] == 0
                and diagnostics["orphan_replies"] == 0
            ),
            "expected_total": expected_total,
            "captured_count": len(comments),
            "top_level_count": sum(
                1 for item in comments if not item["parent_source_comment_id"]
            ),
            **diagnostics,
            "rules_version": "comment-rules/v1",
            "bundle": bundle_descriptor,
        }

        (
            media,
            media_artifacts,
            missing_media,
            omitted_media,
            unsupported_media,
        ) = self._export_media(int(video_id))
        raw_work_type = str(video_raw.get("douyin_work_type") or "").strip().lower()
        image_count = max(
            _integer(video_raw.get("image_count"), 0),
            sum(1 for item in media if item["role"] == "image"),
        )
        if raw_work_type in {"image", "gallery"} or (
            image_count and not any(item["role"] == "video" for item in media)
        ):
            work_type = "gallery" if image_count > 1 or raw_work_type == "gallery" else "image"
        else:
            work_type = "video"

        return CollectedContentExport(
            creator={
                "creator_id": creator_uuid,
                "display_name": creator_name,
                "platform": platform,
                "platform_user_id": platform_user_id,
            },
            work={
                "platform": platform,
                "source_work_id": source_work_id,
                "work_type": work_type,
                "title": _bounded_text(video.get("title"), 1000),
                "description": _bounded_text(video.get("description"), 20_000),
                "source_url": _bounded_text(video.get("url"), 4000),
                "cover_url": _bounded_text(video.get("cover_url"), 4000),
                "published_at": _aware_iso(
                    video.get("published_at"),
                    field="video.published_at",
                    required=False,
                ),
            },
            media=tuple(media),
            media_artifacts=tuple(media_artifacts),
            comment_snapshot=comment_snapshot,
            comment_bundle_bytes=bundle_bytes,
            captured_at=captured_at,
            missing_media_count=missing_media,
            omitted_media_count=omitted_media,
            unsupported_media_count=unsupported_media,
        )

    def export_and_enqueue(
        self,
        video_id: int,
        creator_runtime_key: str,
    ) -> dict[str, Any]:
        exported = self.export(video_id, creator_runtime_key)

        # Validate all protocol-facing fields before consuming monotonic numbers.
        self._manifest(exported, source_sequence=1, work_revision=1)
        comment_path = self._write_comment_bundle(exported)
        artifacts = [*exported.media_artifacts]
        artifacts.append(
            {
                "artifact_id": exported.comment_snapshot["bundle"]["bundle_id"],
                "artifact_kind": "comment_bundle",
                "local_path": str(comment_path),
                "size_bytes": len(exported.comment_bundle_bytes),
                "sha256": exported.comment_snapshot["bundle"]["sha256"],
            }
        )

        reservation = self.outbox.reserve(
            node_id=self.collector_node_id,
            creator_id=exported.creator["creator_id"],
            platform=exported.work["platform"],
            source_work_id=exported.work["source_work_id"],
        )
        manifest = self._manifest(
            exported,
            source_sequence=reservation.source_sequence,
            work_revision=reservation.work_revision,
        )
        queued = self.outbox.enqueue(manifest, artifacts=artifacts)
        return {
            **queued,
            "comment_count": exported.comment_count,
            "media_count": len(exported.media),
            "missing_media_count": exported.missing_media_count,
            "omitted_media_count": exported.omitted_media_count,
            "unsupported_media_count": exported.unsupported_media_count,
        }

    def _manifest(
        self,
        exported: CollectedContentExport,
        *,
        source_sequence: int,
        work_revision: int,
    ) -> dict[str, Any]:
        return new_manifest(
            collector_node_id=self.collector_node_id,
            collector_key_id=self.collector_key_id,
            collector_version=self.collector_version,
            source_sequence=source_sequence,
            creator=exported.creator,
            work=exported.work,
            work_revision=work_revision,
            media=list(exported.media),
            comment_snapshot=exported.comment_snapshot,
            captured_at=exported.captured_at,
        )

    def _export_media(
        self,
        video_id: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, int]:
        assets = self.storage.list_assets(video_id)

        def asset_order(asset: Mapping[str, Any]) -> tuple[int, int, int, str]:
            raw = _raw_mapping(asset.get("raw_json"))
            role = MEDIA_ROLES.get(str(asset.get("asset_type") or "").strip().lower())
            return (
                0 if role == "video" else 1 if role == "image" else 2,
                _integer(raw.get("image_index"), 0),
                _integer(asset.get("id"), 0),
                str(asset.get("local_path") or ""),
            )

        media: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        missing = 0
        omitted = 0
        unsupported = 0
        for asset in sorted(assets, key=asset_order):
            role = MEDIA_ROLES.get(str(asset.get("asset_type") or "").strip().lower())
            if not role:
                omitted += 1
                continue
            local_value = str(asset.get("local_path") or "").strip()
            if not local_value:
                missing += 1
                continue
            path = Path(local_value).expanduser()
            try:
                path = path.resolve(strict=True)
            except (OSError, RuntimeError):
                missing += 1
                continue
            if not path.is_file():
                missing += 1
                continue
            if len(media) >= MAX_MANIFEST_MEDIA:
                omitted += 1
                continue
            size_bytes = path.stat().st_size
            if size_bytes <= 0:
                missing += 1
                continue
            mime_type = str(asset.get("mime_type") or "").strip()
            if not mime_type:
                mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            mime_type = mime_type.split(";", 1)[0].strip().lower()
            if mime_type not in V1_MEDIA_MIME_TYPES.get(role, frozenset()):
                omitted += 1
                unsupported += 1
                continue
            digest = sha256_file(path)
            asset_id = _integer(asset.get("id"), len(media) + 1)
            media_id = f"asset:{asset_id}:{digest[:24]}"
            descriptor = {
                "media_id": media_id,
                "role": role,
                "filename": _safe_filename(
                    asset.get("original_name") or path.name,
                    digest=digest,
                    mime_type=mime_type,
                ),
                "mime_type": _bounded_text(mime_type, 100),
                "size_bytes": size_bytes,
                "sha256": digest,
                "ordinal": len(media),
            }
            media.append(descriptor)
            artifacts.append(
                {
                    "artifact_id": media_id,
                    "artifact_kind": "media",
                    "local_path": str(path),
                    "size_bytes": size_bytes,
                    "sha256": digest,
                }
            )
        return media, artifacts, missing, omitted, unsupported

    def _write_comment_bundle(self, exported: CollectedContentExport) -> Path:
        bundle = exported.comment_snapshot["bundle"]
        destination = (
            self.artifact_dir
            / "comment-bundles"
            / f"{bundle['bundle_id']}.ndjson.gz"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            if (
                destination.stat().st_size != len(exported.comment_bundle_bytes)
                or sha256_file(destination) != bundle["sha256"]
            ):
                raise CollectorExportError("已存在的评论包与内容哈希不一致。")
            return destination.resolve()

        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_bytes(exported.comment_bundle_bytes)
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return destination.resolve()


__all__ = [
    "CollectedContentExport",
    "CollectorContentReadyAdapter",
    "CollectorExportError",
]
