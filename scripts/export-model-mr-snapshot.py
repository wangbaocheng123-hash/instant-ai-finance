from __future__ import annotations

import argparse
from pathlib import Path

from instant_ai.model_mr import ModelMrClient


def main() -> None:
    parser = argparse.ArgumentParser(description="导出模型先生的云端精简只读快照。")
    parser.add_argument("output", type=Path)
    parser.add_argument("--origin", default="http://127.0.0.1:8787")
    parser.add_argument("--works", type=int, default=500)
    args = parser.parse_args()
    result = ModelMrClient(origin=args.origin).write_public_snapshot(args.output, works_limit=args.works)
    print(f"精简快照已生成：作品 {result['works']}，投资思路 {result['thoughts']}。")


if __name__ == "__main__":
    main()
