from __future__ import annotations

import hmac
import os
import re


TOKEN_ENV = "INSTANT_AI_BLOGGER_MCP_TOKEN"
SEARCH_PATH = "/api/mcp/blogger/search"
GET_PATH = "/api/mcp/blogger/get"
PATHS = {SEARCH_PATH, GET_PATH}
TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")


def configured_token() -> str:
    value = str(os.getenv(TOKEN_ENV, "") or "").strip()
    return value if TOKEN_PATTERN.fullmatch(value) else ""


def authorize(header: str) -> str:
    """Return a stable error code; never return or log credential material."""
    expected = configured_token()
    if not expected:
        return "not_configured"
    scheme, separator, supplied = str(header or "").partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        return "unauthorized"
    if not hmac.compare_digest(expected, supplied.strip()):
        return "unauthorized"
    return "ok"


__all__ = ["GET_PATH", "PATHS", "SEARCH_PATH", "TOKEN_ENV", "authorize", "configured_token"]
