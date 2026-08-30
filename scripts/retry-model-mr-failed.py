from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path


def _load_encoder_module():
    script = Path(__file__).with_name("reencode-model-mr-videos-h264.py")
    spec = importlib.util.spec_from_file_location("model_mr_h264", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载编码脚本：{script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description="按已有清单重试模型先生视频失败项，并合并回清单")
    parser.add_argument(
        "--manifest",
        default=r"H:\模型先生智能体\模型视频_360p_有声\model_mr_360p_h264_audio_manifest_20260830.json",
    )
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failed = [item for item in manifest["items"] if item.get("status") == "failed"]
    if not failed:
        print("没有需要重试的失败项")
        return

    encoder = _load_encoder_module()
    ffmpeg = encoder._ffmpeg_path()

    def retry(item):
        source = Path(item["source"])
        target = Path(item["target"])
        try:
            return encoder._transcode_one(
                ffmpeg,
                source,
                target,
                640,
                360,
                "1800k",
                "128k",
                20,
                "ultrafast",
                "crf",
                True,
            )
        except Exception as error:
            item["message"] = str(error)
            return item

    updated: dict[str, object] = {}
    workers = max(1, min(args.workers, 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(retry, item): item for item in failed}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            original = futures[future]
            result = future.result()
            result_dict = asdict(result) if hasattr(result, "__dataclass_fields__") else result
            updated[original["source"]] = result_dict
            print(f"[{index}/{len(failed)}] {Path(original['source']).name} -> {result_dict.get('status')}", flush=True)

    merged = []
    for item in manifest["items"]:
        merged.append(updated.get(item["source"], item))
    manifest["items"] = merged
    manifest["count"] = len(merged)
    manifest["settings"].update({"video_bitrate": "1800k", "audio_bitrate": "128k", "crf": 20, "preset": "ultrafast"})
    manifest["stats"] = {
        "ok": sum(1 for item in merged if item.get("status") == "ok"),
        "failed": sum(1 for item in merged if item.get("status") == "failed"),
        "skip_exist": sum(1 for item in merged if item.get("status") == "skip_exist"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
