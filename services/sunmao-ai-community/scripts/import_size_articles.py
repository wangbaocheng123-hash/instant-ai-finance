"""Import approved local article bundles, without network calls or raw SQL edits."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sunmao_community.articles import import_article
from sunmao_community.size_library import MAX_GALLERY_ASSETS, MAX_IMAGE_BYTES, SizeLibraryStore


def bundle_images(manifest: Path, payload: dict) -> list[Path]:
    images = payload.get("images")
    if not isinstance(images, list) or len(images) > MAX_GALLERY_ASSETS:
        raise ValueError("manifest_requires_images")
    paths = []
    for descriptor in images:
        if not isinstance(descriptor, dict):
            raise ValueError("invalid_image_descriptor")
        name, digest = descriptor.get("file"), descriptor.get("sha256")
        if (not isinstance(name, str) or not isinstance(digest, str)
                or Path(name).is_absolute()):
            raise ValueError("invalid_image_descriptor")
        path = (manifest.parent / name).resolve(strict=True)
        if manifest.parent not in path.parents or not path.is_file():
            raise ValueError("image_outside_bundle")
        if not 1 <= path.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError("invalid_article_image_size")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("image_digest_mismatch")
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--knowledge-root", type=Path, required=True)
    args = parser.parse_args()
    # Check every package's file integrity before changing the database.
    packages = []
    for name in args.manifests:
        manifest = name.resolve(strict=True)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_article_manifest")
        packages.append((payload, bundle_images(manifest, payload)))
    seed = Path(__file__).resolve().parent.parent / "web" / "size-resources.json"
    store = SizeLibraryStore(args.database, args.knowledge_root, seed)
    for payload, images in packages:
        resource = import_article(store, payload, images)
        print(json.dumps({"id": resource["id"], "title": resource["title"],
                          "image_count": resource["image_count"], "video_count": resource["video_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
