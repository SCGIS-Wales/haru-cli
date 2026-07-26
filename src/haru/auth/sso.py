"""OAuth 2.0 Authorization Code + PKCE login against IAM Identity Center.

Implements the same flow ``aws sso login`` uses by default since AWS CLI
v2.22.0: register a public OIDC client, open the browser to the authorize
endpoint, capture the code on a 127.0.0.1 loopback listener, and exchange it
with ``CreateToken``. Only loopback redirect URIs are accepted.
"""

import secrets
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import boto3
from botocore.exceptions import ClientError

from haru.auth.pkce import generate_pkce_pair
from haru.config.schema import AuthConfig
from haru.errors import AuthError

_LOOPBACK_HOST = "127.0.0.1"
_CALLBACK_PATH = "/oauth/callback"
_AUTHORIZE_SCOPES = "sso:account:access"
_CALLBACK_RESPONSE = (
    b"<html><body><p>Sign-in complete. You can close this window and return to haru.</p>"
    b"</body></html>"
)


@dataclass(frozen=True)
class ClientRegistration:
    """A registered public OIDC client."""

    client_id: str
    client_secret: str
    expires_at: datetime


@dataclass(frozen=True)
class SsoToken:
    """An SSO access token plus the registration needed to refresh it."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    registration: ClientRegistration


def register_client(
    oidc: Any, client_name: str, start_url: str, redirect_uri: str
) -> ClientRegistration:
    """Register a public OIDC client for the authorization-code + PKCE flow."""
    host = urlsplit(redirect_uri).hostname
    if host != _LOOPBACK_HOST:
        raise AuthError(f"Redirect URI must be a {_LOOPBACK_HOST} loopback address, got {host!r}")
    try:
        response = oidc.register_client(
            clientName=client_name,
            clientType="public",
            scopes=[_AUTHORIZE_SCOPES],
            redirectUris=[redirect_uri],
            issuerUrl=start_url,
            grantTypes=["authorization_code", "refresh_token"],
        )
    except ClientError as exc:
        raise AuthError(f"OIDC client registration failed: {exc}") from exc
    return ClientRegistration(
        client_id=response["clientId"],
        client_secret=response["clientSecret"],
        expires_at=datetime.fromtimestamp(response["clientSecretExpiresAt"], tz=UTC),
    )


def build_authorize_url(
    region: str, client_id: str, redirect_uri: str, state: str, code_challenge: str
) -> str:
    """Build the IAM Identity Center authorize URL for the PKCE flow."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scopes": _AUTHORIZE_SCOPES,
        }
    )
    return f"https://oidc.{region}.amazonaws.com/authorize?{query}"


def exchange_code(
    oidc: Any, registration: ClientRegistration, code: str, verifier: str, redirect_uri: str
) -> SsoToken:
    """Exchange an authorization code (plus PKCE verifier) for an SSO token."""
    try:
        response = oidc.create_token(
            clientId=registration.client_id,
            clientSecret=registration.client_secret,
            grantType="authorization_code",
            code=code,
            codeVerifier=verifier,
            redirectUri=redirect_uri,
        )
    except ClientError as exc:
        raise AuthError(f"Token exchange failed: {exc}") from exc
    return SsoToken(
        access_token=response["accessToken"],
        refresh_token=response.get("refreshToken"),
        expires_at=datetime.now(UTC) + timedelta(seconds=response["expiresIn"]),
        registration=registration,
    )


def run_login(
    config: AuthConfig,
    *,
    oidc: Any | None = None,
    opener: Callable[[str], object] | None = None,
    timeout_seconds: float = 300.0,
) -> SsoToken:
    """Run the interactive browser login and return the resulting SSO token.

    Orchestrates client registration, PKCE pair generation, opening the
    authorize URL, capturing the code on a loopback listener (validating the
    ``state`` parameter), and the CreateToken exchange.
    """
    sso = config.sso
    if oidc is None:
        oidc = boto3.Session().client("sso-oidc", region_name=sso.sso_region)
    open_url = opener if opener is not None else webbrowser.open

    server = _start_loopback_server(sso.callback_port)
    try:
        port = server.server_address[1]
        redirect_uri = f"http://{_LOOPBACK_HOST}:{port}{_CALLBACK_PATH}"
        registration = register_client(oidc, sso.client_name, sso.start_url, redirect_uri)
        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        open_url(
            build_authorize_url(
                sso.sso_region, registration.client_id, redirect_uri, state, challenge
            )
        )
        code, returned_state = _await_callback(server, timeout_seconds)
        if not secrets.compare_digest(returned_state, state):
            raise AuthError("State mismatch in OAuth callback; aborting login")
        return exchange_code(oidc, registration, code, verifier, redirect_uri)
    finally:
        server.server_close()


class _CallbackServer(HTTPServer):
    """Loopback HTTP server that records the OAuth callback query parameters."""

    callback_params: dict[str, list[str]] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles the single OAuth redirect request on the loopback listener."""

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != _CALLBACK_PATH:
            self.send_error(404)
            return
        server = cast(_CallbackServer, self.server)
        server.callback_params = parse_qs(parsed.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_CALLBACK_RESPONSE)

    def log_message(self, format: str, *args: object) -> None:
        """Silence default request logging; callback URLs must never be logged."""


def _start_loopback_server(port: int) -> _CallbackServer:
    """Bind the loopback callback server (port 0 selects an ephemeral port)."""
    try:
        return _CallbackServer((_LOOPBACK_HOST, port), _CallbackHandler)
    except OSError as exc:
        raise AuthError(f"Could not bind loopback callback port {port}: {exc}") from exc


def _await_callback(server: _CallbackServer, timeout_seconds: float) -> tuple[str, str]:
    """Wait for the OAuth redirect and return its ``(code, state)`` pair."""
    deadline = time.monotonic() + timeout_seconds
    server.timeout = 1.0
    while server.callback_params is None:
        if time.monotonic() >= deadline:
            raise AuthError("Timed out waiting for the browser sign-in to complete")
        server.handle_request()
    params = server.callback_params
    if "error" in params:
        raise AuthError(f"Authorization failed: {params['error'][0]}")
    codes = params.get("code")
    states = params.get("state")
    if not codes or not states:
        raise AuthError("OAuth callback is missing the code or state parameter")
    return codes[0], states[0]
