from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import cv2

VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".mpeg",
    ".mpg",
    ".3gp",
}


@dataclass
class ItemReport:
    source: str
    target: str
    status: str
    source_size_bytes: int
    target_size_bytes: int
    ratio: float
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    fps: float
    frames: int
    duration_sec: float
    codec: str
    audio: str
    message: str


def _iter_video_files(root: Path, exclude_dirs: set[str]) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if path.name.startswith("."):
            continue
        if any(parent.name in exclude_dirs for parent in path.parents):
            continue
        yield path


def _video_meta(path: Path) -> tuple[int, int, float, int, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("无法打开视频文件")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frames / fps if fps > 0 else 0.0
    cap.release()
    return width, height, fps, frames, duration


def _ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as error:  # pragma: no cover - environment diagnostic
        raise RuntimeError("找不到可用的 ffmpeg（需要 imageio-ffmpeg）") from error


def _replace_with_retry(source: Path, target: Path) -> None:
    """Windows 杀毒/索引服务可能在 ffmpeg 退出后短暂占用文件，替换时重试。"""
    last_error: Exception | None = None
    for delay in (0.0, 0.25, 0.75, 1.5, 3.0, 5.0):
        if delay:
            time.sleep(delay)
        try:
            source.replace(target)
            return
        except OSError as error:
            last_error = error
    raise RuntimeError(f"临时文件替换失败：{last_error}") from last_error


def _unlink_with_retry(path: Path) -> None:
    if not path.exists():
        return
    for delay in (0.0, 0.25, 0.75, 1.5):
        if delay:
            time.sleep(delay)
        try:
            path.unlink()
            return
        except OSError:
            continue


def _transcode_one(
    ffmpeg: str,
    source: Path,
    target: Path,
    max_width: int,
    max_height: int,
    video_bitrate: str,
    audio_bitrate: str,
    crf: int,
    preset: str,
    rate_control: str,
    overwrite: bool,
) -> ItemReport:
    source_size = source.stat().st_size
    width, height, fps, frames, duration = _video_meta(source)
    if target.exists() and not overwrite:
        target_size = target.stat().st_size
        return ItemReport(
            source=str(source),
            target=str(target),
            status="skip_exist",
            source_size_bytes=source_size,
            target_size_bytes=target_size,
            ratio=round(target_size / source_size * 100, 2) if source_size else 0.0,
            source_width=width,
            source_height=height,
            target_width=0,
            target_height=0,
            fps=fps,
            frames=frames,
            duration_sec=duration,
            codec="skip",
            audio="unknown",
            message="目标文件已存在，未覆盖",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    # 保留 .mp4 扩展名，避免 ffmpeg 无法判断临时输出封装格式。
    temp_target = target.with_name(target.stem + ".partial.mp4")
    if temp_target.exists():
        temp_target.unlink()

    # 360p 上限、H.264、限制视频码率；音频保留并转为 AAC，兼顾手机/浏览器兼容性。
    scale = (
        f"scale={max_width}:{max_height}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    if rate_control == "cbr":
        rate_options = [
            "-b:v",
            video_bitrate,
            "-minrate",
            video_bitrate,
            "-maxrate",
            video_bitrate,
            "-bufsize",
            video_bitrate,
        ]
    else:
        rate_options = ["-crf", str(crf), "-maxrate", video_bitrate, "-bufsize", video_bitrate]

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        scale,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        *rate_options,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ac",
        "2",
        "-ar",
        "44100",
        "-sn",
        "-movflags",
        "+faststart",
        str(temp_target),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "ffmpeg 失败").strip()
            raise RuntimeError(detail[-1200:])
        if not temp_target.exists() or temp_target.stat().st_size == 0:
            raise RuntimeError("ffmpeg 未生成有效文件")
        _replace_with_retry(temp_target, target)
    finally:
        if temp_target.exists():
            _unlink_with_retry(temp_target)

    target_size = target.stat().st_size
    return ItemReport(
        source=str(source),
        target=str(target),
        status="ok",
        source_size_bytes=source_size,
        target_size_bytes=target_size,
        ratio=round(target_size / source_size * 100, 2) if source_size else 0.0,
        source_width=width,
        source_height=height,
        target_width=0,
        target_height=0,
        fps=fps,
        frames=frames,
        duration_sec=duration,
        codec="h264",
        audio="aac-preserved",
        message="H.264 视频 + AAC 音频处理成功",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将模型先生视频转为带音频的高压缩 360p MP4")
    parser.add_argument("--source-root", default=r"H:\模型先生智能体")
    parser.add_argument("--backup-root", default=r"H:\模型先生智能体\模型视频_360p_有声")
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--max-height", type=int, default=360)
    parser.add_argument("--video-bitrate", default="420k", help="视频最大码率，默认 420k")
    parser.add_argument("--audio-bitrate", default="64k", help="音频码率，默认 64k")
    parser.add_argument("--crf", type=int, default=30, help="H.264 质量参数，数值越大体积越小")
    parser.add_argument("--preset", default="veryfast", help="H.264 编码速度预设")
    parser.add_argument("--rate-control", choices=("crf", "cbr"), default="crf", help="码率控制模式")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--delete-originals", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4, help="并行编码任务数，默认 4")
    parser.add_argument("--manifest-name", default="model_mr_360p_h264_audio_manifest_20260830.json")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    source_root = Path(args.source_root)
    backup_root = Path(args.backup_root)
    if not source_root.exists():
        raise SystemExit(f"源目录不存在：{source_root}")

    # 源根目录下可能存在本次生成的各种 360p 测试/备份目录，全部排除，避免重复套娃编码。
    exclude_dirs = {
        path.name
        for path in source_root.iterdir()
        if path.is_dir() and path.name.startswith("模型视频_360p")
    }
    exclude_dirs.add(backup_root.name)
    files = list(_iter_video_files(source_root, exclude_dirs))
    if args.limit > 0:
        files = files[: args.limit]

    report: list[ItemReport] = []
    stats = {"ok": 0, "failed": 0, "skip_exist": 0}
    if not args.dry_run:
        ffmpeg = _ffmpeg_path()
        print(f"使用编码器：{ffmpeg}")
    else:
        ffmpeg = ""

    def process(index: int, source: Path) -> tuple[int, ItemReport]:
        target = (backup_root / source.relative_to(source_root)).with_suffix(".mp4")
        try:
            item = _transcode_one(
                ffmpeg,
                source,
                target,
                args.max_width,
                args.max_height,
                args.video_bitrate,
                args.audio_bitrate,
                args.crf,
                args.preset,
                args.rate_control,
                args.overwrite,
            )
            if args.delete_originals and item.status == "ok":
                source.unlink()
            return index, item
        except Exception as error:
            return index, ItemReport(
                source=str(source),
                target=str(target),
                status="failed",
                source_size_bytes=source.stat().st_size,
                target_size_bytes=0,
                ratio=0.0,
                source_width=0,
                source_height=0,
                target_width=0,
                target_height=0,
                fps=0.0,
                frames=0,
                duration_sec=0.0,
                codec="unknown",
                audio="unknown",
                message=str(error),
            )

    if args.dry_run:
        for index, source in enumerate(files, start=1):
            target = (backup_root / source.relative_to(source_root)).with_suffix(".mp4")
            print(f"[plan {index}/{len(files)}] {source} -> {target}")
    else:
        workers = max(1, min(args.workers, 8))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            pending = [executor.submit(process, index, source) for index, source in enumerate(files, start=1)]
            for future in concurrent.futures.as_completed(pending):
                index, item = future.result()
                stats[item.status] += 1
                report.append(item)
                print(f"[{item.status} {index}/{len(files)}] {Path(item.source).name} -> {Path(item.target).name}", flush=True)
        report.sort(key=lambda item: item.source)

    backup_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "count": len(report),
        "source_root": str(source_root),
        "backup_root": str(backup_root),
        "settings": {
            "max_width": args.max_width,
            "max_height": args.max_height,
            "video_bitrate": args.video_bitrate,
            "audio_bitrate": args.audio_bitrate,
            "crf": args.crf,
            "rate_control": args.rate_control,
            "delete_originals": args.delete_originals,
        },
        "stats": stats,
        "items": [asdict(item) for item in report],
    }
    (backup_root / args.manifest_name).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"count": len(report), "stats": stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
