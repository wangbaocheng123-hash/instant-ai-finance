from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        # Each installation owns its local .env. Blank values intentionally
        # override inherited credentials, but blank path values use defaults.
        os.environ[key.strip()] = value.strip()


_BOOTSTRAP_ENV_PATH = Path(
    os.getenv("BLOGGER_AGENT_ENV_FILE", str(ROOT_DIR / ".env"))
).expanduser().resolve()
_load_env_file(_BOOTSTRAP_ENV_PATH)


def _runtime_path(env_name: str, default: Path) -> Path:
    configured = str(os.getenv(env_name, "") or "").strip()
    return Path(configured or default).expanduser().resolve()


DATA_DIR = _runtime_path("BLOGGER_AGENT_DATA_DIR", ROOT_DIR / "data")
CONFIG_DIR = _runtime_path("BLOGGER_AGENT_CONFIG_DIR", ROOT_DIR / "config")
PROMPTS_DIR = ROOT_DIR / "prompts"
WEB_DIR = ROOT_DIR / "web"
ARCHIVE_DIR = DATA_DIR / "archive"
DEFAULT_DOUBAO_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-1-turbo-260628"


@dataclass
class Settings:
    app_name: str
    source_account_name: str
    monitor_interval_seconds: int
    analysis_version: str
    prompt_version: str
    deep_model: str
    fast_model: str
    reasoning_effort: str
    auto_analyze_new_videos: bool
    auto_doubao_transcribe_enabled: bool
    openai_api_enabled: bool
    doubao_api_enabled: bool
    source_mode: str
    creator_profile_url: str
    creator_sync_mode: str
    creator_sync_enabled: bool
    creator_sync_interval_minutes: int
    creator_sync_history_limit: int
    creator_comments_enabled: bool
    creator_comment_limit: int
    creator_comment_refresh_minutes: int
    creator_comment_tracking_hours: int
    database_path: Path
    openai_api_key: str | None
    doubao_asr_api_key: str | None
    doubao_asr_app_id: str | None
    doubao_asr_access_key: str | None
    doubao_asr_resource_id: str
    doubao_ark_api_key: str | None
    doubao_ark_base_url: str
    doubao_text_model: str | None
    doubao_vision_model: str | None


def load_settings() -> Settings:
    env_path = _runtime_path(
        "BLOGGER_AGENT_ENV_FILE",
        ROOT_DIR / ".env",
    )
    _load_env_file(env_path)
    config_path = CONFIG_DIR / "settings.json"
    if not config_path.exists():
        example_path = ROOT_DIR / "config" / "settings.example.json"
        if not example_path.exists():
            raise FileNotFoundError(f"缺少默认配置模板：{example_path}")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    config = json.loads(config_path.read_text(encoding="utf-8"))

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    return Settings(
        app_name=config.get("app_name", "博主智能体模板"),
        source_account_name=config.get("source_account_name", "新博主"),
        monitor_interval_seconds=int(
            os.getenv(
                "MX_AGENT_MONITOR_INTERVAL_SECONDS",
                str(config.get("monitor_interval_seconds", 30)),
            )
        ),
        analysis_version=config.get("analysis_version", "analysis-v1"),
        prompt_version=config.get("prompt_version", "prompt-v1"),
        deep_model=os.getenv("MX_AGENT_DEEP_MODEL", config.get("deep_model", "gpt-5.5")),
        fast_model=os.getenv("MX_AGENT_FAST_MODEL", config.get("fast_model", "gpt-5.4-mini")),
        reasoning_effort=os.getenv(
            "MX_AGENT_REASONING_EFFORT",
            config.get("reasoning_effort", "medium"),
        ),
        auto_analyze_new_videos=bool(config.get("auto_analyze_new_videos", False)),
        auto_doubao_transcribe_enabled=(
            bool(config.get("auto_doubao_transcribe_enabled", True))
            and os.getenv("MX_AGENT_AUTO_DOUBAO_TRANSCRIBE", "1") != "0"
        ),
        openai_api_enabled=bool(config.get("openai_api_enabled", False)),
        doubao_api_enabled=bool(config.get("doubao_api_enabled", False)),
        source_mode=config.get("source_mode", "manual-first"),
        creator_profile_url=str(config.get("creator_profile_url", "") or "").strip(),
        creator_sync_mode=(
            str(config.get("creator_sync_mode") or "").strip().lower()
            if str(config.get("creator_sync_mode") or "").strip().lower()
            in {"count", "realtime"}
            else (
                "realtime"
                if bool(config.get("creator_sync_enabled", False))
                else "count"
            )
        ),
        creator_sync_enabled=bool(config.get("creator_sync_enabled", False)),
        creator_sync_interval_minutes=max(
            3, int(config.get("creator_sync_interval_minutes", 10))
        ),
        creator_sync_history_limit=max(
            1, min(1000, int(config.get("creator_sync_history_limit", 500)))
        ),
        creator_comments_enabled=bool(config.get("creator_comments_enabled", True)),
        creator_comment_limit=max(
            20, min(50000, int(config.get("creator_comment_limit", 5000)))
        ),
        creator_comment_refresh_minutes=max(
            30, int(config.get("creator_comment_refresh_minutes", 60))
        ),
        creator_comment_tracking_hours=max(
            1, min(720, int(config.get("creator_comment_tracking_hours", 24)))
        ),
        database_path=_runtime_path(
            "BLOGGER_AGENT_DATABASE_PATH",
            DATA_DIR / "mx_agent.sqlite3",
        ),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        doubao_asr_api_key=os.getenv("DOUBAO_ASR_API_KEY") or None,
        doubao_asr_app_id=os.getenv("DOUBAO_ASR_APP_ID") or None,
        doubao_asr_access_key=os.getenv("DOUBAO_ASR_ACCESS_KEY") or None,
        doubao_asr_resource_id=os.getenv(
            "DOUBAO_ASR_RESOURCE_ID",
            "volc.seedasr.auc",
        ).strip(),
        doubao_ark_api_key=os.getenv("DOUBAO_ARK_API_KEY") or None,
        doubao_ark_base_url=os.getenv(
            "DOUBAO_ARK_BASE_URL",
            DEFAULT_DOUBAO_ARK_BASE_URL,
        ).rstrip("/"),
        doubao_text_model=(
            os.getenv("DOUBAO_TEXT_MODEL")
            or DEFAULT_DOUBAO_TEXT_MODEL
        ),
        doubao_vision_model=os.getenv("DOUBAO_VISION_MODEL") or None,
    )


def openai_api_is_enabled(settings: Settings | object) -> bool:
    """Return whether a configured OpenAI key is currently allowed to be used."""
    return bool(getattr(settings, "openai_api_key", None)) and bool(
        getattr(settings, "openai_api_enabled", True)
    )


def require_openai_api_enabled(
    settings: Settings | object,
    action: str = "此功能",
) -> None:
    if not getattr(settings, "openai_api_key", None):
        raise RuntimeError("尚未配置 OPENAI_API_KEY，无法调用 OpenAI API。")
    if not bool(getattr(settings, "openai_api_enabled", True)):
        raise RuntimeError(f"OpenAI API 总开关当前已关闭，请先在右上角开启后再使用{action}。")


def save_openai_api_enabled(settings: Settings, enabled: bool) -> bool:
    """Persist the runtime API gate and update the shared Settings instance."""
    config_path = CONFIG_DIR / "settings.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["openai_api_enabled"] = bool(enabled)
    temp_path = config_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(config_path)
    settings.openai_api_enabled = bool(enabled)
    return openai_api_is_enabled(settings)


def doubao_speech_is_configured(settings: Settings | object) -> bool:
    api_key = str(getattr(settings, "doubao_asr_api_key", "") or "").strip()
    app_id = str(getattr(settings, "doubao_asr_app_id", "") or "").strip()
    access_key = str(
        getattr(settings, "doubao_asr_access_key", "") or ""
    ).strip()
    return bool(
        api_key
        or (app_id and access_key)
    )


def doubao_text_is_configured(settings: Settings | object) -> bool:
    return bool(getattr(settings, "doubao_ark_api_key", None)) and bool(
        getattr(settings, "doubao_text_model", None)
    )


def doubao_vision_is_configured(settings: Settings | object) -> bool:
    return bool(getattr(settings, "doubao_ark_api_key", None)) and bool(
        getattr(settings, "doubao_vision_model", None)
    )


def doubao_api_is_configured(settings: Settings | object) -> bool:
    return any(
        (
            doubao_speech_is_configured(settings),
            doubao_text_is_configured(settings),
            doubao_vision_is_configured(settings),
        )
    )


def doubao_api_is_enabled(settings: Settings | object) -> bool:
    return doubao_api_is_configured(settings) and bool(
        getattr(settings, "doubao_api_enabled", False)
    )


def save_doubao_api_enabled(settings: Settings, enabled: bool) -> bool:
    """Persist the domestic provider gate without storing any secret."""
    config_path = CONFIG_DIR / "settings.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["doubao_api_enabled"] = bool(enabled)
    temp_path = config_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(config_path)
    settings.doubao_api_enabled = bool(enabled)
    return doubao_api_is_enabled(settings)
