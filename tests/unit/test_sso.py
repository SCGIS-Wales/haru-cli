"""Tests for the SSO PKCE login flow with the sso-oidc client mocked."""

import contextlib
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError

from haru.auth.sso import (
    ClientRegistration,
    SsoToken,
    _await_callback,
    _start_loopback_server,
    build_authorize_url,
    exchange_code,
    register_client,
    run_login,
)
from haru.config.schema import AuthConfig
from haru.errors import AuthError

REDIRECT_URI = "http://127.0.0.1:43210/oauth/callback"


def make_auth_config(**overrides: Any) -> AuthConfig:
    """Build an AuthConfig for tests."""
    sso = {
        "start_url": "https://example.awsapps.com/start",
        "sso_region": "us-east-1",
        "account_id_env": "HARU_AWS_ACCOUNT_ID",
        "role_name": "HaruBedrockInvoke",
        "callback_port": 0,
        **overrides,
    }
    return AuthConfig.model_validate({"sso": sso, "bedrock_region": "us-east-1"})


def make_registration() -> ClientRegistration:
    """Build a client registration for tests."""
    return ClientRegistration(
        client_id="client-123",
        client_secret="client-secret",
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )


def client_error(operation: str) -> ClientError:
    """Build a botocore ClientError for tests."""
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, operation)


class FakeOidc:
    """Minimal sso-oidc stand-in recording calls and returning canned data."""

    def __init__(self) -> None:
        self.register_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []

    def register_client(self, **kwargs: Any) -> dict[str, Any]:
        self.register_calls.append(kwargs)
        return {
            "clientId": "client-123",
            "clientSecret": "client-secret",
            "clientSecretExpiresAt": int((datetime.now(UTC) + timedelta(days=90)).timestamp()),
        }

    def create_token(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(kwargs)
        return {"accessToken": "access-abc", "refreshToken": "refresh-abc", "expiresIn": 3600}


def test_register_client_parameters() -> None:
    """register_client sends the exact PKCE public-client registration."""
    oidc = FakeOidc()
    registration = register_client(
        oidc, "haru-cli", "https://example.awsapps.com/start", REDIRECT_URI
    )
    assert registration.client_id == "client-123"
    call = oidc.register_calls[0]
    assert call["clientType"] == "public"
    assert call["scopes"] == ["sso:account:access"]
    assert call["redirectUris"] == [REDIRECT_URI]
    assert call["issuerUrl"] == "https://example.awsapps.com/start"
    assert call["grantTypes"] == ["authorization_code", "refresh_token"]


def test_register_client_rejects_non_loopback() -> None:
    """Non-loopback redirect URIs are refused before any API call."""
    oidc = FakeOidc()
    with pytest.raises(AuthError, match="loopback"):
        register_client(oidc, "haru-cli", "https://x/start", "http://evil.example.com/callback")
    assert oidc.register_calls == []


def test_register_client_wraps_client_error(mocker: Any) -> None:
    """botocore errors surface as AuthError."""
    oidc = mocker.Mock()
    oidc.register_client.side_effect = client_error("RegisterClient")
    with pytest.raises(AuthError, match="registration failed"):
        register_client(oidc, "haru-cli", "https://x/start", REDIRECT_URI)


def test_build_authorize_url() -> None:
    """The authorize URL targets the regional OIDC endpoint with PKCE params."""
    url = build_authorize_url("us-east-1", "client-123", REDIRECT_URI, "state-1", "challenge-1")
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "oidc.us-east-1.amazonaws.com"
    assert parts.path == "/authorize"
    query = parse_qs(parts.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["state"] == ["state-1"]
    assert query["code_challenge"] == ["challenge-1"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scopes"] == ["sso:account:access"]
    assert "sso%3Aaccount%3Aaccess" in url


def test_exchange_code_parameters() -> None:
    """exchange_code performs the authorization_code CreateToken call."""
    oidc = FakeOidc()
    token = exchange_code(oidc, make_registration(), "code-1", "verifier-1", REDIRECT_URI)
    call = oidc.create_calls[0]
    assert call["grantType"] == "authorization_code"
    assert call["code"] == "code-1"
    assert call["codeVerifier"] == "verifier-1"
    assert call["redirectUri"] == REDIRECT_URI
    assert call["clientId"] == "client-123"
    assert token.access_token == "access-abc"
    assert token.refresh_token == "refresh-abc"
    assert token.expires_at > datetime.now(UTC)


def test_exchange_code_wraps_client_error(mocker: Any) -> None:
    """CreateToken failures surface as AuthError."""
    oidc = mocker.Mock()
    oidc.create_token.side_effect = client_error("CreateToken")
    with pytest.raises(AuthError, match="exchange failed"):
        exchange_code(oidc, make_registration(), "code-1", "verifier-1", REDIRECT_URI)


def _get(url: str) -> None:
    with urllib.request.urlopen(url):  # noqa: S310 - fixed loopback URL in test
        pass


def test_loopback_callback_roundtrip() -> None:
    """The loopback server captures code and state from a real local request."""
    server = _start_loopback_server(0)
    try:
        port = server.server_address[1]
        thread = threading.Thread(
            target=_get,
            args=(f"http://127.0.0.1:{port}/oauth/callback?code=abc&state=xyz",),
        )
        thread.start()
        code, state = _await_callback(server, timeout_seconds=10.0)
        thread.join(timeout=10.0)
    finally:
        server.server_close()
    assert code == "abc"
    assert state == "xyz"


def test_callback_error_parameter() -> None:
    """An error parameter on the callback raises AuthError."""
    server = _start_loopback_server(0)
    try:
        port = server.server_address[1]
        thread = threading.Thread(
            target=_get,
            args=(f"http://127.0.0.1:{port}/oauth/callback?error=access_denied",),
        )
        thread.start()
        with pytest.raises(AuthError, match="access_denied"):
            _await_callback(server, timeout_seconds=10.0)
        thread.join(timeout=10.0)
    finally:
        server.server_close()


def test_callback_ignores_other_paths() -> None:
    """Requests outside the callback path get a 404 and are not captured."""
    server = _start_loopback_server(0)
    try:
        port = server.server_address[1]

        def _hit_wrong_then_right() -> None:
            with contextlib.suppress(urllib.error.HTTPError):
                _get(f"http://127.0.0.1:{port}/favicon.ico")
            _get(f"http://127.0.0.1:{port}/oauth/callback?code=abc&state=xyz")

        thread = threading.Thread(target=_hit_wrong_then_right)
        thread.start()
        code, state = _await_callback(server, timeout_seconds=10.0)
        thread.join(timeout=10.0)
    finally:
        server.server_close()
    assert (code, state) == ("abc", "xyz")


def test_callback_missing_state() -> None:
    """A callback without a state parameter raises AuthError."""
    server = _start_loopback_server(0)
    try:
        port = server.server_address[1]
        thread = threading.Thread(
            target=_get, args=(f"http://127.0.0.1:{port}/oauth/callback?code=abc",)
        )
        thread.start()
        with pytest.raises(AuthError, match="missing"):
            _await_callback(server, timeout_seconds=10.0)
        thread.join(timeout=10.0)
    finally:
        server.server_close()


def test_bind_failure_raises_auth_error() -> None:
    """A port already in use surfaces as AuthError, not OSError."""
    holder = _start_loopback_server(0)
    try:
        port = holder.server_address[1]
        with pytest.raises(AuthError, match="Could not bind"):
            _start_loopback_server(port)
    finally:
        holder.server_close()


def test_callback_timeout() -> None:
    """A zero timeout fails immediately without a request."""
    server = _start_loopback_server(0)
    try:
        with pytest.raises(AuthError, match="Timed out"):
            _await_callback(server, timeout_seconds=0.0)
    finally:
        server.server_close()


def test_run_login_happy_path(mocker: Any) -> None:
    """run_login wires registration, browser, callback, and token exchange."""
    oidc = FakeOidc()
    opened: list[str] = []

    def fake_await(server: Any, timeout_seconds: float) -> tuple[str, str]:
        query = parse_qs(urlsplit(opened[0]).query)
        return "code-1", query["state"][0]

    mocker.patch("haru.auth.sso._await_callback", side_effect=fake_await)
    token = run_login(make_auth_config(), oidc=oidc, opener=opened.append)

    assert isinstance(token, SsoToken)
    assert token.access_token == "access-abc"
    registered_redirect = oidc.register_calls[0]["redirectUris"][0]
    assert registered_redirect.startswith("http://127.0.0.1:")
    assert oidc.create_calls[0]["redirectUri"] == registered_redirect
    assert oidc.create_calls[0]["grantType"] == "authorization_code"


def test_run_login_state_mismatch(mocker: Any) -> None:
    """A state mismatch on the callback aborts before token exchange."""
    oidc = FakeOidc()
    mocker.patch("haru.auth.sso._await_callback", return_value=("code-1", "forged-state"))
    with pytest.raises(AuthError, match="State mismatch"):
        run_login(make_auth_config(), oidc=oidc, opener=lambda url: None)
    assert oidc.create_calls == []
