from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .blogger_ingest import BloggerIngestStore, DEFAULT_BLOGGER_AGENT_ROOT
from .blogger_library import MODEL_MR_TRANSFER_CREATOR_ID
from .model_mr import MODEL_MR, ModelMrClient, ModelMrUnavailable


MAX_COMMENTS = 50_000
MAX_UNCOMPRESSED_COMMENTS = 128 * 1024 * 1024


class ModelMrTransferProjector:
    """Project the reserved transport creator into Model Mr's independent root."""

    def __init__(
        self,
        *,
        blogger_root: Path = DEFAULT_BLOGGER_AGENT_ROOT,
        model_mr: ModelMrClient = MODEL_MR,
    ) -> None:
        self.blogger_root = Path(blogger_root)
        self.model_mr = model_mr

    def project(self, transfer_id: str) -> None:
        transfer = BloggerIngestStore(self.blogger_root).get_transfer(transfer_id)
        if not transfer:
            raise ModelMrUnavailable("模型先生传输记录不存在。")
        manifest = transfer.get("manifest") if isinstance(transfer.get("manifest"), dict) else {}
        creator = manifest.get("creator") if isinstance(manifest.get("creator"), dict) else {}
        if str(creator.get("creator_id") or "") != MODEL_MR_TRANSFER_CREATOR_ID:
            return
        if str(transfer.get("transport_status") or "") != "transport_completed":
            raise ModelMrUnavailable("模型先生传输尚未完成。")

        work = manifest.get("work") if isinstance(manifest.get("work"), dict) else {}
        media_items = manifest.get("media") if isinstance(manifest.get("media"), list) else []
        video_descriptor = next(
            (
                item
                for item in media_items
                if isinstance(item, dict)
                and item.get("role") == "video"
                and item.get("mime_type") == "video/mp4"
            ),
            None,
        )
        if video_descriptor is None:
            raise ModelMrUnavailable("模型先生传输没有 MP4 视频。")
        artifacts = {
            str(item.get("artifact_id") or ""): item
            for item in transfer.get("artifacts", [])
            if isinstance(item, dict)
        }
        bundle_descriptor = (
            manifest.get("comment_snapshot", {}).get("bundle", {})
            if isinstance(manifest.get("comment_snapshot"), dict)
            else {}
        )
        video_artifact = artifacts.get(str(video_descriptor.get("media_id") or ""))
        comment_artifact = artifacts.get(str(bundle_descriptor.get("bundle_id") or ""))
        if not video_artifact or not comment_artifact:
            raise ModelMrUnavailable("模型先生传输附件不完整。")

        imported = self.model_mr.import_beijing_work(
            source_work_id=str(work.get("source_work_id") or ""),
            source_revision=int(work.get("revision") or 0),
            title=str(work.get("title") or ""),
            description=str(work.get("description") or ""),
            source_url=str(work.get("source_url") or ""),
            published_at=str(work.get("published_at") or ""),
            comments=self._comments(
                self._artifact_path(comment_artifact),
                bundle_descriptor,
            ),
            media_path=self._artifact_path(video_artifact),
            media_sha256=str(video_descriptor.get("sha256") or ""),
        )
        if imported.get("status") == "imported":
            from .model_mr_processing import ModelMrProcessor
            # The request only records intent. Paid work runs on the serial worker.
            ModelMrProcessor(self.model_mr).enqueue_arrival(
                int(imported["work_id"]), str(video_descriptor.get("sha256") or ""))

    def _artifact_path(self, artifact: Mapping[str, Any]) -> Path:
        relative = str(artifact.get("stored_relative_path") or "").replace("\\", "/")
        parts = [part for part in relative.split("/") if part]
        if not parts or relative.startswith("/") or any(part in {".", ".."} for part in parts):
            raise ModelMrUnavailable("模型先生传输附件路径无效。")
        root = self.blogger_root.resolve(strict=True)
        artifact_root = (root / "artifacts").resolve(strict=True)
        target = root.joinpath(*parts).resolve(strict=True)
        if target != artifact_root and artifact_root not in target.parents:
            raise ModelMrUnavailable("模型先生传输附件越界。")
        if not target.is_file() or target.stat().st_size != int(artifact.get("expected_size_bytes") or -1):
            raise ModelMrUnavailable("模型先生传输附件缺失。")
        if _sha256_file(target) != str(artifact.get("expected_sha256") or ""):
            raise ModelMrUnavailable("模型先生传输附件校验失败。")
        return target

    @staticmethod
    def _comments(path: Path, descriptor: Mapping[str, Any]) -> list[dict[str, Any]]:
        expected_count = int(descriptor.get("item_count") or 0)
        expected_size = int(descriptor.get("uncompressed_size_bytes") or 0)
        if (
            expected_count < 0
            or expected_size < 0
            or expected_count > MAX_COMMENTS
            or expected_size > MAX_UNCOMPRESSED_COMMENTS
        ):
            raise ModelMrUnavailable("模型先生评论包超过安全上限。")
        comments: list[dict[str, Any]] = []
        digest = hashlib.sha256()
        total = 0
        try:
            with gzip.open(path, "rb") as stream:
                for raw_line in stream:
                    total += len(raw_line)
                    if total > expected_size or len(comments) >= MAX_COMMENTS:
                        raise ModelMrUnavailable("模型先生评论包长度异常。")
                    digest.update(raw_line)
                    item = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(item, dict):
                        raise ModelMrUnavailable("模型先生评论包格式无效。")
                    comments.append(item)
        except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelMrUnavailable("模型先生评论包无法读取。") from error
        if (
            len(comments) != expected_count
            or total != expected_size
            or digest.hexdigest() != str(descriptor.get("uncompressed_sha256") or "")
        ):
            raise ModelMrUnavailable("模型先生评论包校验失败。")
        return comments


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _configured_blogger_root() -> Path:
    configured = os.environ.get("INSTANT_AI_BLOGGER_ROOT", "").strip()
    return Path(configured) if configured else DEFAULT_BLOGGER_AGENT_ROOT


MODEL_MR_TRANSFER_PROJECTOR = ModelMrTransferProjector(
    blogger_root=_configured_blogger_root()
)


__all__ = ["MODEL_MR_TRANSFER_PROJECTOR", "ModelMrTransferProjector"]
