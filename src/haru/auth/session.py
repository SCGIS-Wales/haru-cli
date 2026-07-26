"""Construct boto3 sessions from the cached SSO token, refreshing when needed."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import (
    ClientError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from haru.auth.cache import read_token_cache, write_token_cache
from haru.auth.sso import SsoToken
from haru.config.schema import AuthConfig
from haru.errors import AuthExpiredError, ConfigError

_REFRESH_WINDOW = timedelta(minutes=15)
_RELOGIN_HINT = "run 'haru login'"


def build_boto3_session(
    config: AuthConfig,
    *,
    oidc: Any | None = None,
    sso_client: Any | None = None,
    cache_dir: Path | None = None,
) -> boto3.Session:
    """Build a boto3 session with role credentials from the cached SSO token.

    Refreshes the access token via the ``refresh_token`` grant when it is
    within the refresh window. Raises AuthExpiredError whenever a fresh
    interactive login is required.
    """
    sso = config.sso
    token = read_token_cache(sso.start_url, cache_dir=cache_dir)
    if token is None:
        raise AuthExpiredError(f"No cached SSO token; {_RELOGIN_HINT}")
    if token.expires_at <= datetime.now(UTC) + _REFRESH_WINDOW:
        token = _refresh_token(config, token, oidc=oidc, cache_dir=cache_dir)

    account_id = os.environ.get(sso.account_id_env)
    if account_id is None:
        raise ConfigError(
            f"Environment variable {sso.account_id_env!r} must hold the target AWS account id"
        )
    if sso_client is None:
        sso_client = boto3.Session().client("sso", region_name=sso.sso_region)
    try:
        response = sso_client.get_role_credentials(
            roleName=sso.role_name, accountId=account_id, accessToken=token.access_token
        )
    except (ClientError, SSOTokenLoadError, UnauthorizedSSOTokenError, TokenRetrievalError) as exc:
        raise AuthExpiredError(f"SSO credentials rejected; {_RELOGIN_HINT}") from exc

    credentials = response["roleCredentials"]
    return boto3.Session(
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        aws_session_token=credentials["sessionToken"],
        region_name=config.bedrock_region,
    )


def _refresh_token(
    config: AuthConfig, token: SsoToken, *, oidc: Any | None, cache_dir: Path | None
) -> SsoToken:
    """Refresh an expiring token via the ``refresh_token`` grant and re-cache it."""
    sso = config.sso
    now = datetime.now(UTC)
    if token.refresh_token is None or token.registration.expires_at <= now:
        raise AuthExpiredError(f"SSO token expired and cannot be refreshed; {_RELOGIN_HINT}")
    if oidc is None:
        oidc = boto3.Session().client("sso-oidc", region_name=sso.sso_region)
    try:
        response = oidc.create_token(
            clientId=token.registration.client_id,
            clientSecret=token.registration.client_secret,
            grantType="refresh_token",
            refreshToken=token.refresh_token,
        )
    except ClientError as exc:
        raise AuthExpiredError(f"SSO token refresh failed; {_RELOGIN_HINT}") from exc

    refreshed = SsoToken(
        access_token=response["accessToken"],
        refresh_token=response.get("refreshToken", token.refresh_token),
        expires_at=now + timedelta(seconds=response["expiresIn"]),
        registration=token.registration,
    )
    write_token_cache(refreshed, sso.start_url, sso.sso_region, cache_dir=cache_dir)
    return refreshed
