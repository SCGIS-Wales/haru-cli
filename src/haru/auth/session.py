"""Construct boto3 sessions from the cached SSO token, refreshing when needed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from haru.auth.cache import read_token_cache, write_token_cache
from haru.auth.identity import effective_identity
from haru.auth.sso import SsoToken
from haru.config.schema import AuthConfig, SsoConfig
from haru.errors import AuthError, AuthExpiredError

if TYPE_CHECKING:
    from pathlib import Path

    import boto3

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
    interactive login is required, and AuthError when the selected role is
    rejected for reasons re-login alone cannot fix.
    """
    import boto3
    from botocore.exceptions import (
        ClientError,
        SSOTokenLoadError,
        TokenRetrievalError,
        UnauthorizedSSOTokenError,
    )

    sso = config.sso
    token = read_token_cache(sso.start_url, cache_dir=cache_dir)
    if token is None:
        raise AuthExpiredError(f"No cached SSO token; {_RELOGIN_HINT}")
    if token.expires_at <= datetime.now(UTC) + _REFRESH_WINDOW:
        token = _refresh_token(config, token, oidc=oidc, cache_dir=cache_dir)

    account_id, role_name = _resolve_identity(sso, cache_dir)
    if sso_client is None:
        sso_client = boto3.Session().client("sso", region_name=sso.sso_region)
    try:
        response = sso_client.get_role_credentials(
            roleName=role_name, accountId=account_id, accessToken=token.access_token
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "UnauthorizedException":
            raise AuthExpiredError(f"SSO credentials rejected; {_RELOGIN_HINT}") from exc
        raise AuthError(
            f"AWS rejected role {role_name!r} in account {account_id}"
            f" ({code or 'unknown error'}). The role may not be assigned to you -"
            " re-run 'haru login' to choose again, or fix auth.sso.role_name in"
            " your configuration."
        ) from exc
    except (SSOTokenLoadError, UnauthorizedSSOTokenError, TokenRetrievalError) as exc:
        raise AuthExpiredError(f"SSO credentials rejected; {_RELOGIN_HINT}") from exc

    credentials = response["roleCredentials"]
    return boto3.Session(
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        aws_session_token=credentials["sessionToken"],
        region_name=config.bedrock_region,
    )


def _resolve_identity(sso: SsoConfig, cache_dir: Path | None) -> tuple[str, str]:
    """Resolve the account id and role: config pins, env var, then the login choice."""
    account_id, _, role_name, _ = effective_identity(sso, cache_dir)
    if account_id is None or role_name is None:
        raise AuthExpiredError(f"No AWS account/role selected yet; {_RELOGIN_HINT}")
    return account_id, role_name


def _refresh_token(
    config: AuthConfig, token: SsoToken, *, oidc: Any | None, cache_dir: Path | None
) -> SsoToken:
    """Refresh an expiring token via the ``refresh_token`` grant and re-cache it."""
    import boto3
    from botocore.exceptions import ClientError

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
