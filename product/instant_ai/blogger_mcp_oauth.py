from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from .auth import OwnerAuth, SESSION_SECONDS
from .blogger_ingest import DEFAULT_BLOGGER_AGENT_ROOT


PUBLIC_ORIGIN = os.environ.get("INSTANT_AI_PUBLIC_ORIGIN", "https://grandpaamu.com").rstrip("/")
ISSUER = PUBLIC_ORIGIN
MCP_RESOURCE = f"{PUBLIC_ORIGIN}/mcp"
MCP_SCOPE = "blogger.read"
AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
REGISTER_PATH = "/oauth/register"
PROTECTED_RESOURCE_PATHS = {
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
}
AUTHORIZATION_METADATA_PATH = "/.well-known/oauth-authorization-server"
CODE_TTL_SECONDS = 5 * 60
_CLIENT_ID = re.compile(r"mcp-client-[A-Za-z0-9_-]{32,96}")
_PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43,128}")
_PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_CHATGPT_CALLBACK = re.compile(r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}")
_STABLE_CHATGPT_CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


class BloggerOAuthError(ValueError):
    def __init__(self, code: str, description: str) -> None:
        super().__init__(description)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class AuthorizationRequest:
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    resource: str
    scope: str


class BloggerOAuthStore:
    """Small Git-external store for DCR clients and one-time authorization codes."""

    def __init__(self, path: Path | None = None, *, clock=time.time) -> None:
        default = DEFAULT_BLOGGER_AGENT_ROOT / "database" / "blogger_oauth.db"
        self.path = Path(path or default)
        self.clock = clock
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                redirect_uri TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT NOT NULL,
                resource TEXT NOT NULL,
                scope TEXT NOT NULL,
                username TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            """
        )
        self._remove_legacy_redirect_uniqueness(connection)
        return connection

    @staticmethod
    def _remove_legacy_redirect_uniqueness(connection: sqlite3.Connection) -> None:
        """Migrate the original one-client-per-callback schema in place.

        ChatGPT's stable callback URI is shared by many connector instances, while
        DCR requires a dedicated client id for every instance.  The first schema
        incorrectly made the callback unique, so existing production databases
        need a lossless table rebuild before new registrations can be independent.
        """
        redirect_unique = False
        for index in connection.execute("PRAGMA index_list(oauth_clients)").fetchall():
            if not int(index["unique"]):
                continue
            columns = connection.execute(
                f'PRAGMA index_info("{str(index["name"]).replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            if [str(column["name"]) for column in columns] == ["redirect_uri"]:
                redirect_unique = True
                break
        if not redirect_unique:
            return
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE oauth_clients RENAME TO oauth_clients_legacy;
            CREATE TABLE oauth_clients (
                client_id TEXT PRIMARY KEY,
                redirect_uri TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            INSERT INTO oauth_clients(client_id, redirect_uri, created_at)
            SELECT client_id, redirect_uri, created_at FROM oauth_clients_legacy;
            DROP TABLE oauth_clients_legacy;
            COMMIT;
            """
        )

    def register_client(self, redirect_uri: str) -> str:
        if not self.valid_redirect_uri(redirect_uri):
            raise BloggerOAuthError("invalid_redirect_uri", "只允许 ChatGPT 官方 OAuth 回调地址。")
        with self._lock, closing(self._connect()) as connection, connection:
            client_id = f"mcp-client-{secrets.token_urlsafe(32)}"
            connection.execute(
                "INSERT INTO oauth_clients(client_id, redirect_uri, created_at) VALUES (?, ?, ?)",
                (client_id, redirect_uri, int(self.clock())),
            )
            return client_id

    def validate_client(self, client_id: str, redirect_uri: str) -> bool:
        if not _CLIENT_ID.fullmatch(client_id) or not self.valid_redirect_uri(redirect_uri):
            return False
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM oauth_clients WHERE client_id = ? AND redirect_uri = ?",
                (client_id, redirect_uri),
            ).fetchone()
        return row is not None

    def issue_code(self, request: AuthorizationRequest, username: str) -> str:
        code = secrets.token_urlsafe(48)
        code_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
        now = int(self.clock())
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM oauth_codes WHERE expires_at <= ?", (now,))
            connection.execute(
                """
                INSERT INTO oauth_codes(
                    code_hash, client_id, redirect_uri, code_challenge,
                    resource, scope, username, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code_hash,
                    request.client_id,
                    request.redirect_uri,
                    request.code_challenge,
                    request.resource,
                    request.scope,
                    username,
                    now + CODE_TTL_SECONDS,
                ),
            )
        return code

    def consume_code(
        self,
        code: str,
        *,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
        resource: str,
    ) -> Mapping[str, Any]:
        code_hash = hashlib.sha256(str(code or "").encode("ascii", "ignore")).hexdigest()
        now = int(self.clock())
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM oauth_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
            connection.execute("DELETE FROM oauth_codes WHERE expires_at <= ?", (now,))
            if row is None or int(row["expires_at"]) <= now:
                raise BloggerOAuthError("invalid_grant", "授权码无效或已过期。")
            if (
                row["client_id"] != client_id
                or row["redirect_uri"] != redirect_uri
                or row["resource"] != resource
            ):
                raise BloggerOAuthError("invalid_grant", "授权码与客户端或资源不匹配。")
            verifier = str(code_verifier or "")
            if not _PKCE_VERIFIER.fullmatch(verifier):
                raise BloggerOAuthError("invalid_grant", "PKCE 校验失败。")
            digest = hashlib.sha256(verifier.encode("ascii")).digest()
            challenge = _urlsafe(digest)
            if not secrets.compare_digest(challenge, str(row["code_challenge"])):
                raise BloggerOAuthError("invalid_grant", "PKCE 校验失败。")
            connection.execute("DELETE FROM oauth_codes WHERE code_hash = ?", (code_hash,))
            return dict(row)

    @staticmethod
    def valid_redirect_uri(value: str) -> bool:
        return value == _STABLE_CHATGPT_CALLBACK or _CHATGPT_CALLBACK.fullmatch(value) is not None


class BloggerMcpOAuth:
    def __init__(self, owner_auth: OwnerAuth, store: BloggerOAuthStore | None = None) -> None:
        self.owner_auth = owner_auth
        self.store = store or BloggerOAuthStore()

    @staticmethod
    def protected_resource_metadata() -> dict[str, Any]:
        return {
            "resource": MCP_RESOURCE,
            "authorization_servers": [ISSUER],
            "scopes_supported": [MCP_SCOPE],
            "resource_documentation": f"{PUBLIC_ORIGIN}/",
        }

    @staticmethod
    def authorization_server_metadata() -> dict[str, Any]:
        # Do not advertise RFC 9207 issuer identification for new connectors.
        # ChatGPT then assigns a callback-id-specific redirect URI instead of
        # routing every development connector through its shared stable callback.
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{PUBLIC_ORIGIN}{AUTHORIZE_PATH}",
            "token_endpoint": f"{PUBLIC_ORIGIN}{TOKEN_PATH}",
            "registration_endpoint": f"{PUBLIC_ORIGIN}{REGISTER_PATH}",
            "token_endpoint_auth_methods_supported": ["none"],
            "grant_types_supported": ["authorization_code"],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": [MCP_SCOPE],
        }

    def register(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        redirect_uris = payload.get("redirect_uris")
        if not isinstance(redirect_uris, list) or len(redirect_uris) != 1:
            raise BloggerOAuthError("invalid_client_metadata", "必须提供一个 ChatGPT 回调地址。")
        redirect_uri = str(redirect_uris[0] or "")
        method = str(payload.get("token_endpoint_auth_method") or "none")
        if method != "none":
            raise BloggerOAuthError("invalid_client_metadata", "仅支持 PKCE 公共客户端。")
        client_id = self.store.register_client(redirect_uri)
        return {
            "client_id": client_id,
            "client_id_issued_at": int(self.store.clock()),
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        }

    def parse_authorization_request(self, params: Mapping[str, list[str]]) -> AuthorizationRequest:
        def one(name: str) -> str:
            values = params.get(name, [])
            return str(values[0] if len(values) == 1 else "")

        request = AuthorizationRequest(
            client_id=one("client_id"),
            redirect_uri=one("redirect_uri"),
            state=one("state"),
            code_challenge=one("code_challenge"),
            resource=one("resource"),
            scope=one("scope") or MCP_SCOPE,
        )
        if one("response_type") != "code" or one("code_challenge_method") != "S256":
            raise BloggerOAuthError("invalid_request", "只支持 authorization_code + PKCE S256。")
        if not request.state or len(request.state) > 1024:
            raise BloggerOAuthError("invalid_request", "缺少有效 state。")
        if not _PKCE_CHALLENGE.fullmatch(request.code_challenge):
            raise BloggerOAuthError("invalid_request", "缺少有效 PKCE challenge。")
        if request.resource != MCP_RESOURCE or request.scope != MCP_SCOPE:
            raise BloggerOAuthError("invalid_scope", "请求的资源或权限范围无效。")
        if not self.store.validate_client(request.client_id, request.redirect_uri):
            raise BloggerOAuthError("invalid_client", "OAuth 客户端未登记。")
        return request

    def authorization_redirect(self, request: AuthorizationRequest, username: str) -> str:
        code = self.store.issue_code(request, username)
        parameters = {"code": code, "state": request.state}
        # Preserve already registered stable-callback clients while new clients
        # use callback-id redirects selected from the metadata above.
        if request.redirect_uri == _STABLE_CHATGPT_CALLBACK:
            parameters["iss"] = ISSUER
        return f"{request.redirect_uri}?{urlencode(parameters)}"

    def exchange(self, payload: Mapping[str, list[str]]) -> dict[str, Any]:
        def one(name: str) -> str:
            values = payload.get(name, [])
            return str(values[0] if len(values) == 1 else "")

        if one("grant_type") != "authorization_code":
            raise BloggerOAuthError("unsupported_grant_type", "只支持 authorization_code。")
        row = self.store.consume_code(
            one("code"),
            client_id=one("client_id"),
            redirect_uri=one("redirect_uri"),
            code_verifier=one("code_verifier"),
            resource=one("resource"),
        )
        token, _session = self.owner_auth.create_oauth_token(
            str(row["username"]),
            audience=MCP_RESOURCE,
            scopes=(str(row["scope"]),),
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": SESSION_SECONDS,
            "scope": str(row["scope"]),
        }

    def bearer_session(self, header: str):
        scheme, separator, token = str(header or "").partition(" ")
        if separator != " " or scheme.casefold() != "bearer":
            return None
        return self.owner_auth.oauth_session(
            token.strip(),
            audience=MCP_RESOURCE,
            required_scope=MCP_SCOPE,
        )

    @staticmethod
    def challenge() -> str:
        metadata = f"{PUBLIC_ORIGIN}/.well-known/oauth-protected-resource"
        return (
            f'Bearer resource_metadata="{metadata}", scope="{MCP_SCOPE}", '
            'error="invalid_token", error_description="Owner authorization required"'
        )


def _urlsafe(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


__all__ = [
    "AUTHORIZATION_METADATA_PATH",
    "AUTHORIZE_PATH",
    "BloggerMcpOAuth",
    "BloggerOAuthError",
    "BloggerOAuthStore",
    "ISSUER",
    "MCP_RESOURCE",
    "MCP_SCOPE",
    "PROTECTED_RESOURCE_PATHS",
    "REGISTER_PATH",
    "TOKEN_PATH",
]
