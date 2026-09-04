"""Export saved metadata and build a new, non-production reconciliation preview.

Never calls AI/collection, edits the source library, copies media, or reads credentials.
The prepare command refuses an existing output directory; it is not a deployment tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "product"))
from instant_ai.model_mr_metadata import (  # noqa: E402
    METADATA_SCHEMA, clean_keyword_info, clean_links, prepare_metadata_merge, public_work_url,
)


def write_new(path: Path, value: dict) -> None:
    if Path(__file__).resolve().parents[1] in path.resolve().parents:
        raise ValueError("业务元数据必须保存在 Git 仓库之外。")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def saved_metadata() -> dict:
    def get(path: str) -> dict:
        # Fixed loopback source, GET only. No arbitrary endpoints or redirects to credentials.
        with urlopen("http://127.0.0.1:8787" + path, timeout=30) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("本地元数据超过上限。")
        return json.loads(raw)

    videos = get("/api/videos?account=%E6%A8%A1%E5%9E%8B%E5%85%88%E7%94%9F&limit=500")["items"]
    thoughts = get("/api/investment-thoughts?limit=500")
    categories = [{key: item.get(key) for key in ("id", "name", "description", "level", "parent_id", "video_count")}
                  for item in thoughts["categories"]]
    works = [{"id": int(item["id"]), "url": public_work_url(item.get("url")),
              "title": str(item.get("active_title") or item.get("title") or "")[:120], "published_at": str(item.get("published_at") or "")[:64],
              "keyword_info": clean_keyword_info(item.get("keyword_info"), item.get("keywords"))} for item in videos]
    return {"schema": METADATA_SCHEMA, "exported_at": datetime.now(timezone.utc).isoformat(),
            "works": works, "thoughts": categories,
            "thought_links": clean_links(thoughts.get("links_by_video"), {w["id"] for w in works}, {int(c["id"]) for c in categories})}


def prepare(source: Path, package: Path, output: Path) -> dict:
    source = source.resolve(strict=True)
    package = package.resolve(strict=True)
    output = output.resolve()
    if Path(__file__).resolve().parents[1] in output.parents:
        raise ValueError("业务预览必须保存在 Git 仓库之外。")
    if output.exists() or output == source.parent or source.parent in output.parents or str(output).startswith(("/opt/", "/var/lib/")):
        raise ValueError("预览输出必须是正式目录之外的全新目录。")
    original = source.read_bytes()
    snapshot, report = prepare_metadata_merge(json.loads(original), json.loads(package.read_text(encoding="utf-8")))
    # Refuse partial/ambiguous migrations. The source remains completely untouched.
    if report["unmatched_ids"]:
        raise ValueError("未匹配作品编号：" + ",".join(map(str, report["unmatched_ids"])))
    raw_details: dict[int, tuple[bytes, dict]] = {}
    changed = set(report["changed_ids"])
    for work in snapshot["works"]:
        work_id = int(work["id"])
        if work_id <= 0:
            raise ValueError("作品编号无效。")
        detail_path = source.parent / "details" / f"{work_id}.json"
        raw = detail_path.read_bytes()
        detail = json.loads(raw)
        if int(detail.get("work", {}).get("id", 0)) != work_id:
            raise ValueError(f"详情编号不一致：{work_id}")
        if work_id in changed:
            detail["work"].update(keyword_info=work["keyword_info"], keywords=work["keywords"])
        raw_details[work_id] = (raw, detail)
    if source.read_bytes() != original or any((source.parent / "details" / f"{i}.json").read_bytes() != raw for i, (raw, _) in raw_details.items()):
        raise ValueError("读取期间来源已变化，请重试。")
    output.mkdir(parents=True, mode=0o700)
    (output / "details").mkdir(mode=0o700)
    write_new(output / "public-snapshot.json", snapshot)
    for work_id, (_, detail) in raw_details.items():
        write_new(output / "details" / f"{work_id}.json", detail)
    report.update(works_before=len(json.loads(original)["works"]), works_after=len(snapshot["works"]),
                  source_sha256=hashlib.sha256(original).hexdigest(), source_unchanged=source.read_bytes() == original,
                  production_written=False, media_copied=False)
    write_new(output / "metadata-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    preview = commands.add_parser("prepare")
    preview.add_argument("--source", type=Path, required=True)
    preview.add_argument("--package", type=Path, required=True)
    preview.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        value = saved_metadata()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_new(args.output, value)
        print(json.dumps({"works": len(value["works"]), "categories": len(value["thoughts"]),
                          "linked_works": sum(bool(v) for v in value["thought_links"].values()),
                          "with_keywords": sum(bool(w["keyword_info"]["keywords"]) for w in value["works"])}))
    else:
        print(json.dumps(prepare(args.source, args.package, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
