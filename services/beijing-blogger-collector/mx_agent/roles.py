from __future__ import annotations

import os
from enum import StrEnum


class ApplicationRole(StrEnum):
    COLLECTOR = "collector"
    INTELLIGENCE = "intelligence"
    ALL_DEV = "all-dev"


def application_role(value: str | None = None) -> ApplicationRole:
    configured = str(
        value if value is not None else os.getenv("BLOGGER_AGENT_ROLE", "all-dev")
    ).strip().lower()
    try:
        return ApplicationRole(configured)
    except ValueError as exc:
        expected = ", ".join(role.value for role in ApplicationRole)
        raise RuntimeError(f"BLOGGER_AGENT_ROLE 必须是：{expected}。") from exc


def require_role(*allowed: ApplicationRole, value: str | None = None) -> ApplicationRole:
    current = application_role(value)
    if current not in allowed:
        names = "、".join(role.value for role in allowed)
        raise RuntimeError(f"当前角色 {current.value} 不允许启动此进程；需要 {names}。")
    return current
