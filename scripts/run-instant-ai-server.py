from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = REPOSITORY_ROOT / "product"

if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from instant_ai.server import run_server


if __name__ == "__main__":
    run_server(collect_if_empty=True)
