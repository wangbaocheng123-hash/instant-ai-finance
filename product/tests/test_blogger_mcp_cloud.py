from __future__ import annotations

import base64
import hashlib
import io
import json
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
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        auth_file = root / "auth.json"
        configure_owner("amu", "correct horse battery staple", auth_file)
        self.auth = OwnerAuth(required=True, path=auth_file)
        self.store = BloggerOAuthStore(root / "blogger_oauth.db")
        self.oauth = BloggerMcpOAuth(self.auth, self.store)

    def tearDown(self) -> None:
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
        challenge = 'Bearer resource_metadata="https://grandpaamu.com/.well-known/oauth-protected-resource", scope="blogger.read"'
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

        form = {**authorization, "username": "amu", "password": "correct horse battery staple"}
        status, headers, _body = self.handler_request(
            "POST",
            "/oauth/authorize",
            urlencode(form).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 302)
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


if __name__ == "__main__":
    unittest.main()
