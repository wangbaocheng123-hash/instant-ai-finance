"""Validated, owner-authorized articles; never store or execute source HTML."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .size_library import MAX_GALLERY_ASSETS, MAX_IMAGE_BYTES, RESOURCE_ID_PATTERN, _image_type

MAX_ARTICLE_BLOCKS = 300
MAX_ARTICLE_TEXT = 100_000
MAX_ARTICLE_IMAGE_BYTES = 100 * 1024 * 1024


def trusted_article_url(value: object, *, video: bool = False) -> bool:
    if not isinstance(value, str) or len(value) > 1024:
        return False
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.netloc != "mp.weixin.qq.com"
                or url.fragment or url.username or url.password):
            return False
        if re.fullmatch(r"/s/[A-Za-z0-9_-]{10,64}", url.path):
            return not url.query
        pairs = parse_qsl(url.query, keep_blank_values=True)
        query = dict(pairs)
        return bool(video and url.path == "/mp/readtemplate" and len(pairs) == 4
                    and set(query) == {"t", "action", "auto", "vid"}
                    and query["t"] == "pages/video_player_tmpl"
                    and query["action"] == "mpvideo" and query["auto"] == "0"
                    and re.fullmatch(r"wxv_[0-9]{15,22}", query["vid"]))
    except ValueError:
        return False


def validate_blocks(blocks: object, image_count: int) -> list[dict]:
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_ARTICLE_BLOCKS:
        raise ValueError("invalid_article_blocks")
    result, used_images, text_size = [], set(), 0
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("invalid_article_block")
        kind = block.get("type")
        if kind in {"paragraph", "heading"}:
            text = block.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
                raise ValueError("invalid_article_text")
            text_size += len(text)
            result.append({"type": kind, "text": text.strip()})
        elif kind == "image":
            position, alt = block.get("position"), block.get("alt", "")
            if (type(position) is not int or not 1 <= position <= image_count
                    or not isinstance(alt, str) or len(alt) > 512):
                raise ValueError("invalid_article_image_reference")
            used_images.add(position)
            result.append({"type": kind, "position": position, "alt": alt})
        elif kind == "video":
            text, url = block.get("text"), block.get("url")
            if (not isinstance(text, str) or not text.strip() or len(text) > 512
                    or not trusted_article_url(url, video=True)):
                raise ValueError("invalid_article_video")
            result.append({"type": kind, "text": text.strip(), "url": url})
        else:
            raise ValueError("unsupported_article_block")
    if text_size > MAX_ARTICLE_TEXT or used_images != set(range(1, image_count + 1)):
        raise ValueError("article_content_incomplete")
    return result


def public_article(body: str, assets: list[dict]) -> list[dict] | None:
    """Fail closed if a referenced image has become private or is missing."""
    try:
        blocks = validate_blocks(json.loads(body), len(assets))
        images = {asset["position"]: asset for asset in assets}
        return [
            {"type": "image", "alt": block["alt"], "url": images[block["position"]]["url"]}
            if block["type"] == "image" else block
            for block in blocks
        ]
    except (ValueError, TypeError, KeyError):
        return None


def import_article(store, metadata: dict, image_paths: list[Path]) -> dict:
    """Insert-only, transactional import. Identical retries are harmless."""
    if not isinstance(metadata, dict) or metadata.get("authorization") != "owner_authorized_copy":
        raise ValueError("article_authorization_required")
    fields = {}
    for key, limit in (("id", 96), ("title", 512), ("source", 80), ("summary", 2000), ("source_url", 1024)):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError("invalid_article_metadata")
        fields[key] = value.strip()
    sequence = metadata.get("sequence")
    if (not RESOURCE_ID_PATTERN.fullmatch(fields["id"])
            or type(sequence) is not int or sequence < 1
            or not trusted_article_url(fields["source_url"])):
        raise ValueError("invalid_article_metadata")
    if not isinstance(image_paths, list) or len(image_paths) > MAX_GALLERY_ASSETS:
        raise ValueError("invalid_article_images")
    blocks = validate_blocks(metadata.get("blocks"), len(image_paths))
    body = json.dumps(blocks, ensure_ascii=False, sort_keys=True)
    prepared, total_bytes = [], 0
    for path in image_paths:
        path = Path(path).resolve(strict=True)
        size = path.stat().st_size
        if not path.is_file() or not 1 <= size <= MAX_IMAGE_BYTES:
            raise ValueError("invalid_article_image_size")
        mime, extension = _image_type(path)
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) != size or total_bytes > MAX_ARTICLE_IMAGE_BYTES:
            raise ValueError("invalid_article_image_size")
        prepared.append((data, mime, extension, hashlib.sha256(data).hexdigest()))

    created = []
    try:
        with store._write_lock, closing(store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM knowledge_items WHERE id = ?", (fields["id"],)).fetchone()
            if existing:
                source = connection.execute("SELECT * FROM knowledge_sources WHERE id = ?", (f"source-{fields['id']}",)).fetchone()
                digests = connection.execute("SELECT sha256, public_level, storage_key FROM knowledge_assets WHERE item_id = ? ORDER BY position", (fields["id"],)).fetchall()
                unchanged = (
                    existing["item_type"] == "local_article" and existing["review_status"] == "published"
                    and existing["title"] == fields["title"] and existing["summary"] == fields["summary"]
                    and existing["body"] == body and existing["display_sequence"] == sequence
                    and source and source["source_url"] == fields["source_url"]
                    and source["source_type"] == fields["source"]
                    and source["authorization_scope"] == "owner_authorized_copy"
                    and [row["sha256"] for row in digests] == [item[3] for item in prepared]
                    and all(row["public_level"] == "public" for row in digests)
                )
                if not unchanged:
                    raise ValueError("article_exists_with_different_content")
                for row in digests:
                    stored = (store.knowledge_root / row["storage_key"]).resolve()
                    if (store.knowledge_root not in stored.parents or not stored.is_file()
                            or hashlib.sha256(stored.read_bytes()).hexdigest() != row["sha256"]):
                        raise ValueError("existing_article_asset_mismatch")
            else:
                if connection.execute("SELECT 1 FROM knowledge_sources WHERE source_url = ?", (fields["source_url"],)).fetchone():
                    raise ValueError("article_source_already_imported")
                if connection.execute("SELECT 1 FROM knowledge_items WHERE display_view = 'sizes' AND display_sequence = ?", (sequence,)).fetchone():
                    raise ValueError("article_sequence_in_use")
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """INSERT INTO knowledge_items
                       (id,item_type,title,primary_topic,summary,body,review_status,display_view,display_sequence,updated_at)
                       VALUES (?,'local_article',?,'object_furniture',?,?,'published','sizes',?,?)""",
                    (fields["id"], fields["title"], fields["summary"], body, sequence, now),
                )
                connection.execute(
                    """INSERT INTO knowledge_sources
                       (id,item_id,source_type,source_url,author,authorization_scope,verified_at)
                       VALUES (?,?,?,?,?,'owner_authorized_copy',?)""",
                    (f"source-{fields['id']}", fields["id"], fields["source"], fields["source_url"], "阿木爷爷", now),
                )
                for position, (data, mime, extension, digest) in enumerate(prepared, 1):
                    asset_id = secrets.token_urlsafe(18)
                    directory = store.knowledge_root / "originals" / "images" / asset_id
                    directory.mkdir(mode=0o750, exist_ok=False)
                    created.append(directory)
                    destination = directory / f"original{extension}"
                    with destination.open("xb") as output:
                        output.write(data)
                    destination.chmod(0o640)
                    connection.execute(
                        """INSERT INTO knowledge_assets
                           (id,item_id,asset_kind,position,storage_key,content_type,byte_size,sha256,public_level)
                           VALUES (?,?,'image',?,?,?,?,?,'public')""",
                        (asset_id, fields["id"], position, destination.relative_to(store.knowledge_root).as_posix(), mime, len(data), digest),
                    )
    except Exception:
        # Only roll back files created by this attempt; never remove existing assets.
        for directory in reversed(created):
            for path in directory.iterdir():
                path.unlink()
            directory.rmdir()
        raise
    return next(item for item in store.list_resources() if item["id"] == fields["id"])
