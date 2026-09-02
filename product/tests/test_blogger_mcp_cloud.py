from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

from instant_ai.auth import OwnerAuth, SESSION_COOKIE_NAME, configure_owner
from instant_ai.blogger_mcp_oauth import (
    MCP_RESOURCE,
    MCP_SCOPE,
    AuthorizationRequest,
    BloggerMcpOAuth,
    BloggerOAuthError,
    BloggerOAuthStore,
)
from instant_ai.blogger_mcp_protocol import attach_oauth_challenge, handle_message, tool_definitions
from instant_ai.oauth_diagnostics import (
    clear_oauth_diagnostics_for_tests,
    oauth_diagnostic_snapshot,
    record_oauth_event,
)
from instant_ai.server import InstantAIHandler


CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


def pkce(value: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode("ascii")).digest()).decode("ascii").rstrip("=")


class FakeLibrary:
    def search_for_mcp(self, question: str, limit: int):
        return {
            "query": question,
            "count": 1,
            "items": [
                {
                    "record_id": "cloud-video:" + "a" * 64,
                    "creator": "李爱琳rene",
                    "title": "最新作品",
                    "original_status": "official",
                }
            ][:limit],
        }

    def get_for_mcp(self, record_id: str):
        return {
            "found": True,
            "record_id": record_id,
            "video_original": {"text": "正式视频原文", "verified": True, "status": "official"},
        }


class BloggerMcpCloudTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_oauth_diagnostics_for_tests()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        auth_file = root / "auth.json"
        configure_owner("amu", "correct horse battery staple", auth_file)
        self.auth = OwnerAuth(required=True, path=auth_file)
        self.store = BloggerOAuthStore(root / "blogger_oauth.db")
        self.oauth = BloggerMcpOAuth(self.auth, self.store)

    def tearDown(self) -> None:
        clear_oauth_diagnostics_for_tests()
        self.temporary.cleanup()

    def handler_request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        handler = InstantAIHandler.__new__(InstantAIHandler)
        handler.command = method
        handler.path = path
        message = Message()
        message["Host"] = "127.0.0.1"
        for name, value in (headers or {}).items():
            message[name] = value
        if method == "POST" and message.get("Content-Length") is None:
            message["Content-Length"] = str(len(body))
        handler.headers = message
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.server = SimpleNamespace(blogger_transfer=None)
        handler.client_address = ("127.0.0.1", 12345)
        handler.close_connection = False
        statuses: list[int] = []
        response_headers: dict[str, str] = {}
        handler.send_response = lambda status, *args, **kwargs: statuses.append(int(status))
        handler.send_header = lambda name, value: response_headers.__setitem__(str(name), str(value))
        handler.end_headers = lambda: None
        with (
            patch("instant_ai.server.AUTH", self.auth),
            patch("instant_ai.server.BLOGGER_MCP_OAUTH", self.oauth),
            patch("instant_ai.server.BLOGGER_LIBRARY", FakeLibrary()),
        ):
            getattr(handler, f"do_{method}")()
        return statuses[-1], response_headers, handler.wfile.getvalue()

    def test_dcr_pkce_owner_flow_issues_scoped_non_cookie_token(self) -> None:
        registration = self.oauth.register(
            {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
        )
        client_id = registration["client_id"]
        verifier = "v" * 43
        params = {
            "response_type": ["code"],
            "client_id": [client_id],
            "redirect_uri": [CALLBACK],
            "state": ["state-123"],
            "code_challenge": [pkce(verifier)],
            "code_challenge_method": ["S256"],
            "resource": [MCP_RESOURCE],
            "scope": [MCP_SCOPE],
        }
        request = self.oauth.parse_authorization_request(params)
        code = self.store.issue_code(request, "amu")
        token_result = self.oauth.exchange(
            {
                "grant_type": ["authorization_code"],
                "code": [code],
                "client_id": [client_id],
                "redirect_uri": [CALLBACK],
                "code_verifier": [verifier],
                "resource": [MCP_RESOURCE],
            }
        )
        token = token_result["access_token"]
        self.assertEqual(token_result["scope"], MCP_SCOPE)
        self.assertEqual(self.oauth.bearer_session(f"Bearer {token}").username, "amu")
        self.assertIsNone(self.auth.oauth_session(token, audience="https://example.com/mcp", required_scope=MCP_SCOPE))
        self.assertIsNone(self.auth.oauth_session(token, audience=MCP_RESOURCE, required_scope="write"))
        self.assertIsNone(self.auth.session(f"{SESSION_COOKIE_NAME}={token}"))
        with self.assertRaises(BloggerOAuthError) as replay:
            self.oauth.exchange(
                {
                    "grant_type": ["authorization_code"],
                    "code": [code],
                    "client_id": [client_id],
                    "redirect_uri": [CALLBACK],
                    "code_verifier": [verifier],
                    "resource": [MCP_RESOURCE],
                }
            )
        self.assertEqual(replay.exception.code, "invalid_grant")

    def test_dcr_rejects_non_chatgpt_callback_and_authorization_mismatch(self) -> None:
        with self.assertRaises(BloggerOAuthError):
            self.oauth.register(
                {"redirect_uris": ["https://attacker.example/callback"], "token_endpoint_auth_method": "none"}
            )
        client_id = self.oauth.register(
            {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
        )["client_id"]
        request = AuthorizationRequest(
            client_id=client_id,
            redirect_uri=CALLBACK,
            state="state",
            code_challenge=pkce("v" * 43),
            resource=MCP_RESOURCE,
            scope=MCP_SCOPE,
        )
        code = self.store.issue_code(request, "amu")
        with self.assertRaises(BloggerOAuthError) as wrong_verifier:
            self.store.consume_code(
                code,
                client_id=client_id,
                redirect_uri=CALLBACK,
                code_verifier="x" * 43,
                resource=MCP_RESOURCE,
            )
        self.assertEqual(wrong_verifier.exception.code, "invalid_grant")
        row = self.store.consume_code(
            code,
            client_id=client_id,
            redirect_uri=CALLBACK,
            code_verifier="v" * 43,
            resource=MCP_RESOURCE,
        )
        self.assertEqual(row["username"], "amu")

    def test_dcr_issues_a_dedicated_client_per_connector_and_migrates_legacy_store(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_oauth.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    redirect_uri TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE oauth_codes (
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
            connection.execute(
                "INSERT INTO oauth_clients(client_id, redirect_uri, created_at) VALUES (?, ?, ?)",
                ("mcp-client-" + "a" * 32, CALLBACK, 1),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = BloggerMcpOAuth(self.auth, BloggerOAuthStore(legacy_path))
        first = migrated.register(
            {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
        )["client_id"]
        second = migrated.register(
            {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
        )["client_id"]
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, "mcp-client-" + "a" * 32)
        self.assertTrue(migrated.store.validate_client(first, CALLBACK))
        self.assertTrue(migrated.store.validate_client(second, CALLBACK))

    def test_pkce_accepts_the_full_rfc_verifier_character_set(self) -> None:
        client_id = self.oauth.register(
            {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
        )["client_id"]
        verifier = "a" * 40 + ".-_~"
        request = AuthorizationRequest(
            client_id=client_id,
            redirect_uri=CALLBACK,
            state="state",
            code_challenge=pkce(verifier),
            resource=MCP_RESOURCE,
            scope=MCP_SCOPE,
        )
        code = self.store.issue_code(request, "amu")
        row = self.store.consume_code(
            code,
            client_id=client_id,
            redirect_uri=CALLBACK,
            code_verifier=verifier,
            resource=MCP_RESOURCE,
        )
        self.assertEqual(row["username"], "amu")

    def test_mcp_lists_read_only_oauth_tools_and_requires_auth_only_for_calls(self) -> None:
        tools = tool_definitions()
        self.assertEqual({tool["name"] for tool in tools}, {"search_blogger_videos", "get_blogger_video_text"})
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])
            self.assertEqual(tool["securitySchemes"], [{"type": "oauth2", "scopes": [MCP_SCOPE]}])

        initialized = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            library=FakeLibrary(),
            version="0.18.0",
            authenticated=False,
        )
        self.assertEqual(initialized["result"]["serverInfo"]["title"], "博主智能体（云端）")
        listed = handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            library=FakeLibrary(),
            version="0.18.0",
            authenticated=False,
        )
        self.assertEqual(len(listed["result"]["tools"]), 2)

        call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_blogger_videos", "arguments": {"question": "李爱琳最新视频"}},
        }
        denied = handle_message(call, library=FakeLibrary(), version="0.18.0", authenticated=False)
        challenge = (
            'Bearer resource_metadata="https://grandpaamu.com/.well-known/oauth-protected-resource", '
            'scope="blogger.read", error="invalid_token", '
            'error_description="Owner authorization required"'
        )
        denied = attach_oauth_challenge(denied, challenge)
        self.assertEqual(denied["error"]["code"], -32001)
        self.assertEqual(denied["error"]["data"]["_meta"]["mcp/www_authenticate"], [challenge])

        allowed = handle_message(call, library=FakeLibrary(), version="0.18.0", authenticated=True)
        self.assertFalse(allowed["result"]["isError"])
        self.assertEqual(allowed["result"]["structuredContent"]["items"][0]["creator"], "李爱琳rene")
        detail = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "get_blogger_video_text",
                    "arguments": {"record_id": "cloud-video:" + "a" * 64},
                },
            },
            library=FakeLibrary(),
            version="0.18.0",
            authenticated=True,
        )
        self.assertEqual(detail["result"]["structuredContent"]["video_original"]["text"], "正式视频原文")

    def test_http_surface_exposes_metadata_and_challenges_private_tool_calls(self) -> None:
        status, headers, body = self.handler_request(
            "GET", "/.well-known/oauth-protected-resource"
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["resource"], MCP_RESOURCE)
        self.assertEqual(headers["Cache-Control"], "no-store")

        status, headers, body = self.handler_request("GET", "/mcp")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")
        self.assertEqual(json.loads(body)["error"], "mcp_get_stream_not_supported")

        initialize = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        ).encode()
        status, _headers, body = self.handler_request(
            "POST",
            "/mcp",
            initialize,
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"]["serverInfo"]["title"], "博主智能体（云端）")

        call = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_blogger_videos",
                    "arguments": {"question": "李爱琳rene最新视频"},
                },
            }
        ).encode()
        status, headers, body = self.handler_request(
            "POST",
            "/mcp",
            call,
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 401)
        self.assertIn("oauth-protected-resource", headers["WWW-Authenticate"])
        self.assertEqual(json.loads(body)["error"]["code"], -32001)

    def test_oauth_diagnostics_are_bounded_anonymous_and_loopback_only(self) -> None:
        raw_client_id = "mcp-client-" + "sensitive-client-identifier"
        for index in range(70):
            record_oauth_event(
                "authorize",
                "page_served",
                client_id=f"{raw_client_id}-{index}",
            )
        snapshot = oauth_diagnostic_snapshot()
        self.assertEqual(snapshot["schema"], "instant-ai-oauth-diagnostics/v1")
        self.assertEqual(snapshot["retention"], "memory_only")
        self.assertEqual(len(snapshot["events"]), 64)
        self.assertNotIn(raw_client_id, json.dumps(snapshot))
        self.assertEqual(set(snapshot["events"][0]), {"at", "stage", "outcome", "client_ref"})

        status, _headers, body = self.handler_request(
            "GET", "/api/internal/oauth-diagnostics"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["events"]), 64)

        status, _headers, body = self.handler_request(
            "GET",
            "/api/internal/oauth-diagnostics",
            headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

        status, _headers, body = self.handler_request(
            "GET",
            "/api/internal/oauth-diagnostics",
            headers={"X-Forwarded-Proto": "https"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_http_oauth_flow_unlocks_cloud_tool_call(self) -> None:
        registration_body = json.dumps(
            {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
        ).encode()
        status, _headers, body = self.handler_request(
            "POST",
            "/oauth/register",
            registration_body,
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        client_id = json.loads(body)["client_id"]
        verifier = "p" * 43
        authorization = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "state": "state-http",
            "code_challenge": pkce(verifier),
            "code_challenge_method": "S256",
            "resource": MCP_RESOURCE,
            "scope": MCP_SCOPE,
        }
        status, _headers, body = self.handler_request(
            "GET", "/oauth/authorize?" + urlencode(authorization)
        )
        self.assertEqual(status, 200)
        self.assertIn("确认授权".encode(), body)
        self.assertIn(b'name="username" value="amu"', body)
        self.assertIn(b'readonly aria-readonly="true"', body)

        wrong_form = {**authorization, "username": "amu", "password": "wrong password"}
        status, wrong_headers, body = self.handler_request(
            "POST",
            "/oauth/authorize",
            urlencode(wrong_form).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Location", wrong_headers)
        self.assertIn('role="alert"'.encode(), body)
        self.assertIn("主人账号或密码不正确".encode(), body)

        form = {**authorization, "username": "amu", "password": "correct horse battery staple"}
        status, headers, _body = self.handler_request(
            "POST",
            "/oauth/authorize",
            urlencode(form).encode(),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-Proto": "https",
            },
        )
        self.assertEqual(status, 302)
        self.assertIn(f"{SESSION_COOKIE_NAME}=", headers["Set-Cookie"])
        self.assertIn("Max-Age=2592000", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        redirect = urlparse(headers["Location"])
        values = parse_qs(redirect.query)
        self.assertEqual(values["state"], ["state-http"])
        self.assertEqual(values["iss"], ["https://grandpaamu.com"])

        token_form = {
            "grant_type": "authorization_code",
            "code": values["code"][0],
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
            "resource": MCP_RESOURCE,
        }
        status, _headers, body = self.handler_request(
            "POST",
            "/oauth/token",
            urlencode(token_form).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 200)
        access_token = json.loads(body)["access_token"]

        call = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "search_blogger_videos",
                    "arguments": {"question": "李爱琳rene最新一条视频", "limit": 1},
                },
            }
        ).encode()
        status, _headers, body = self.handler_request(
            "POST",
            "/mcp",
            call,
            {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(status, 200)
        result = json.loads(body)["result"]["structuredContent"]
        self.assertEqual(result["items"][0]["creator"], "李爱琳rene")

        owner_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        authorization["state"] = "state-http-again"
        status, _headers, body = self.handler_request(
            "GET",
            "/oauth/authorize?" + urlencode(authorization),
            headers={"Cookie": owner_cookie},
        )
        self.assertEqual(status, 200)
        self.assertIn("已登录：amu".encode(), body)
        self.assertNotIn('name="password"'.encode(), body)

        status, headers, _body = self.handler_request(
            "POST",
            "/oauth/authorize",
            urlencode(authorization).encode(),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": owner_cookie,
            },
        )
        self.assertEqual(status, 302)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(parse_qs(urlparse(headers["Location"]).query)["state"], ["state-http-again"])

        observed = {
            (event["stage"], event["outcome"])
            for event in oauth_diagnostic_snapshot()["events"]
        }
        self.assertTrue(
            {
                ("register", "client_created"),
                ("authorize", "page_served"),
                ("authorize", "credentials_rejected"),
                ("authorize", "redirect_issued"),
                ("token", "issued"),
            }.issubset(observed)
        )


if __name__ == "__main__":
    unittest.main()
