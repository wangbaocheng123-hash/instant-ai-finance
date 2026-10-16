from __future__ import annotations

import argparse
import json
from pathlib import Path

from sunmao_community.size_library import SizeLibraryStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an approved image post into the community size library"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("/var/lib/sunmao-community/community.sqlite3"),
    )
    parser.add_argument(
        "--knowledge-root",
        type=Path,
        default=Path("/var/lib/sunmao-community/knowledge"),
    )
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
        raise SystemExit("manifest must be an object with an images list")
    images = [
        (manifest_path.parent / str(value)).resolve()
        for value in payload.pop("images")
    ]
    seed_path = Path(__file__).resolve().parent.parent / "web" / "size-resources.json"
    store = SizeLibraryStore(args.database, args.knowledge_root, seed_path)
    imported = store.import_gallery(payload, images)
    print(json.dumps(imported, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
