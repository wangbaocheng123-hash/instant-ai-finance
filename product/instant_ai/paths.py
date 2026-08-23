from __future__ import annotations

import os
from pathlib import Path


PRODUCT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = Path(__file__).resolve().parent / "static"
LIBRARY_ROOT = Path(os.environ.get("INSTANT_AI_LIBRARY_ROOT", r"H:\即时AI文件库"))
RAW_ROOT = LIBRARY_ROOT / "raw"
EVIDENCE_ROOT = LIBRARY_ROOT / "evidence"
DATABASE_ROOT = LIBRARY_ROOT / "database"
EXPORTS_ROOT = LIBRARY_ROOT / "exports"
BACKUPS_ROOT = LIBRARY_ROOT / "backups"
CACHE_ROOT = LIBRARY_ROOT / "cache"
LOGS_ROOT = LIBRARY_ROOT / "logs"
DATABASE_PATH = DATABASE_ROOT / "instant_ai.db"


def ensure_layout() -> None:
    for directory in (
        LIBRARY_ROOT,
        RAW_ROOT,
        EVIDENCE_ROOT,
        DATABASE_ROOT,
        EXPORTS_ROOT,
        BACKUPS_ROOT,
        CACHE_ROOT,
        LOGS_ROOT,
    ):
        directory.mkdir(parents=True, exist_ok=True)
