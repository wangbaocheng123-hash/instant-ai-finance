from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import cv2

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".mpeg", ".mpg", ".3gp"}


@dataclass
class ItemReport:
    source: str
    target: str
    status: str
    source_size_bytes: int
    source_size_mb: float
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    fps: float
    frames: int
    source_duration_sec: float
    target_size_bytes: int
    target_size_mb: float
    ratio: float
    codec: str
    message: str


def _iter_video_files(root: Path, exclude_dirs: set[str]) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if path.name.startswith("."):
            continue
        if any(parent.name in exclude_dirs for parent in path.parents):
            continue
        yield path


def _scaled_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return width, height
    ratio = min(max_width / width, max_height / height, 1.0)
    target_width = max(2, int(width * ratio))
    target_height = max(2, int(height * ratio))
    target_width -= target_width % 2
    target_height -= target_height % 2
    return target_width, target_height


def _safe_percent(v: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round((v / total) * 100, 2)


def _video_meta(path: Path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("无法打开视频文件")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frames / fps if fps > 0 else 0.0
    cap.release()
    return width, height, fps, frames, duration


def _transcode_one(source: Path, target: Path, max_width: int, max_height: int, overwrite: bool) -> ItemReport:
    source_size = source.stat().st_size
    source_size_mb = round(source_size / 1024 / 1024, 2)

    width, height, fps, frames, duration = _video_meta(source)
    target_width, target_height = _scaled_size(width, height, max_width, max_height)

    if target.exists() and not overwrite:
        target_size = target.stat().st_size
        target_size_mb = round(target_size / 1024 / 1024, 2)
        ratio = _safe_percent(target_size, source_size)
        return ItemReport(
            source=str(source),
            target=str(target),
            status="skip_exist",
            source_size_bytes=source_size,
            source_size_mb=source_size_mb,
            source_width=width,
            source_height=height,
            target_width=target_width,
            target_height=target_height,
            fps=fps,
            frames=frames,
            source_duration_sec=duration,
            target_size_bytes=target_size,
            target_size_mb=target_size_mb,
            ratio=ratio,
            codec="skip",
            message="目标文件已存在，未覆盖",
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    source_cap = cv2.VideoCapture(str(source))
    if not source_cap.isOpened():
        raise RuntimeError("无法再次打开源文件")

    # 视频编码改为 mp4 封装，优先保证兼容性；音频会按可用解码器行为处理。
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(
        str(target),
        fourcc,
        fps,
        (target_width, target_height),
    )
    if not writer.isOpened():
        source_cap.release()
        raise RuntimeError("目标写入器创建失败")

    read_frames = 0
    while True:
        ok, frame = source_cap.read()
        if not ok:
            break
        resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        writer.write(resized)
        read_frames += 1

    source_cap.release()
    writer.release()

    if read_frames == 0:
        raise RuntimeError("读取不到视频帧")

    target_size = target.stat().st_size
    target_size_mb = round(target_size / 1024 / 1024, 2)
    ratio = round(target_size / source_size * 100, 2) if source_size > 0 else 0.0
    return ItemReport(
        source=str(source),
        target=str(target),
        status="ok",
        source_size_bytes=source_size,
        source_size_mb=source_size_mb,
        source_width=width,
        source_height=height,
        target_width=target_width,
        target_height=target_height,
        fps=fps,
        frames=frames,
        source_duration_sec=duration,
        target_size_bytes=target_size,
        target_size_mb=target_size_mb,
        ratio=ratio,
        codec="mp4v",
        message=f"处理成功：{read_frames} 帧",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将模型先生视频转为 360p 左右小分辨率备份，原文件保留。"
    )
    parser.add_argument(
        "--source-root",
        default=r"H:\模型先生智能体",
        help="模型先生原始资源根目录",
    )
    parser.add_argument(
        "--backup-root",
        default=r"H:\模型先生智能体\模型视频_360p",
        help="转码文件统一备份目录",
    )
    parser.add_argument("--max-width", type=int, default=640, help="视频最大宽度（默认 640）")
    parser.add_argument("--max-height", type=int, default=360, help="视频最大高度（默认 360）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的转码文件")
    parser.add_argument("--delete-originals", action="store_true", help="转码成功后删除原文件")
    parser.add_argument("--dry-run", action="store_true", help="只输出计划不实际转码")
    parser.add_argument("--limit", type=int, default=0, help="限制处理文件数（0=全部）")
    parser.add_argument(
        "--manifest-name",
        default="model_mr_360p_manifest.json",
        help="转码清单文件名（放在备份目录）",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    source_root = Path(args.source_root)
    backup_root = Path(args.backup_root)
    if not source_root.exists():
        raise SystemExit(f"源目录不存在：{source_root}")

    exclude_dirs = {backup_root.name}
    files = list(_iter_video_files(source_root, exclude_dirs=exclude_dirs))

    if args.limit > 0:
        files = files[: args.limit]

    report: list[ItemReport] = []
    stats = {"ok": 0, "failed": 0, "skip_exist": 0}

    for source in files:
        rel = source.relative_to(source_root)
        target = backup_root / rel
        if target.suffix.lower() != ".mp4":
            target = target.with_suffix(".mp4")

        if args.dry_run:
            report.append(
                ItemReport(
                    source=str(source),
                    target=str(target),
                    status="plan",
                    source_size_bytes=source.stat().st_size,
                    source_size_mb=round(source.stat().st_size / 1024 / 1024, 2),
                    source_width=0,
                    source_height=0,
                    target_width=args.max_width,
                    target_height=args.max_height,
                    fps=0.0,
                    frames=0,
                    source_duration_sec=0.0,
                    target_size_bytes=0,
                    target_size_mb=0.0,
                    ratio=0.0,
                    codec="N/A",
                    message=f"计划生成 -> {target}",
                )
            )
            continue

        try:
            item = _transcode_one(source, target, args.max_width, args.max_height, args.overwrite)
            if item.status == "ok":
                stats["ok"] += 1
                if args.delete_originals and target.exists():
                    source.unlink()
            else:
                stats["skip_exist"] += 1
            report.append(item)
            print(f"[ok] {source.name} -> {target.name}")
        except Exception as error:
            stats["failed"] += 1
            report.append(
                ItemReport(
                    source=str(source),
                    target=str(target),
                    status="failed",
                    source_size_bytes=source.stat().st_size,
                    source_size_mb=round(source.stat().st_size / 1024 / 1024, 2),
                    source_width=0,
                    source_height=0,
                    target_width=0,
                    target_height=0,
                    fps=0.0,
                    frames=0,
                    source_duration_sec=0.0,
                    target_size_bytes=0,
                    target_size_mb=0.0,
                    ratio=0.0,
                    codec="N/A",
                    message=f"失败：{error}",
                )
            )
            print(f"[failed] {source} => {error}")

    manifest = {
        "count": len(files),
        "source_root": str(source_root),
        "backup_root": str(backup_root),
        "settings": {
            "max_width": args.max_width,
            "max_height": args.max_height,
            "overwrite": args.overwrite,
            "delete_originals": args.delete_originals,
            "dry_run": args.dry_run,
            "limit": args.limit,
        },
        "stats": stats,
        "items": [asdict(item) for item in report],
    }
    backup_root.mkdir(parents=True, exist_ok=True)
    manifest_path = backup_root / args.manifest_name
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"处理完成：{stats['ok']} 成功，{stats['skip_exist']} 跳过，{stats['failed']} 失败")
    print(f"清单已保存：{manifest_path}")


if __name__ == "__main__":
    main()
