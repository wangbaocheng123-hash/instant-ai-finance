from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = REPOSITORY_ROOT / "product"
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from instant_ai.model_mr import ModelMrClient  # noqa: E402


DEFAULT_OUTPUT = Path(r"H:\即时AI文件库\model-mr\public-snapshot.json")
DEFAULT_MANIFEST = Path(
    r"H:\模型先生智能体\模型视频_360p_有声\model_mr_360p_h264_audio_manifest_20260830.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="导出即时 AI 单主人手机版所需的模型先生作品、原文、评论索引和视频映射。"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--media-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--origin", default="http://127.0.0.1:8787")
    parser.add_argument("--works", type=int, default=500)
    args = parser.parse_args()
    result = ModelMrClient(origin=args.origin).write_owner_library(
        args.output,
        media_manifest=args.media_manifest,
        works_limit=args.works,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
