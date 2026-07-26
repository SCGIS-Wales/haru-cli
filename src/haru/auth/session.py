"""Construct boto3 sessions from the cached SSO token, refreshing when needed.

The flow is split into reusable seams: ``ensure_token`` produces a valid SSO
token (refreshing when it is close to expiry) and ``session_for_role``
exchanges that token for role credentials in a specific account. Diagnostics
compose these directly to probe accounts and roles without disturbing the
persisted identity.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from haru.auth.cache import read_token_cache, write_token_cache
from haru.auth.identity import effective_identity
from haru.auth.sso import SsoToken
from haru.config.schema import AuthConfig, SsoConfig
from haru.errors import AuthError, AuthExpiredError
from haru.logging_setup import log_aws_call, log_aws_error

if TYPE_CHECKING:
    from pathlib import Path

    import boto3

logger = logging.getLogger(__name__)

_REFRESH_WINDOW = timedelta(minutes=15)
_RELOGIN_HINT = "run 'haru login'"


def ensure_token(
    config: AuthConfig, *, oidc: Any | None = None, cache_dir: Path | None = None
) -> SsoToken:
    """Return a valid cached SSO token, refreshing it when close to expiry.

    Raises AuthExpiredError when no usable token exists.
    """
    token = read_token_cache(config.sso.start_url, cache_dir=cache_dir)
    if token is None:
        logger.debug("sso token cache miss for %s", config.sso.start_url)
        raise AuthExpiredError(f"No cached SSO token; {_RELOGIN_HINT}")
    stale = token.expires_at <= datetime.now(UTC) + _REFRESH_WINDOW
    logger.debug("sso token cache hit expires_at=%s refreshing=%s", token.expires_at, stale)
    if stale:
        token = _refresh_token(config, token, oidc=oidc, cache_dir=cache_dir)
    return token


def session_for_role(  # noqa: PLR0913 - account/role pair plus optional seams
    config: AuthConfig,
    token: SsoToken,
    account_id: str,
    role_name: str,
    *,
    sso_client: Any | None = None,
    region: str | None = None,
) -> boto3.Session:
    """Exchange ``token`` for credentials of ``role_name`` in ``account_id``.

    Raises AuthExpiredError when the token itself is rejected, and AuthError
    when the role is rejected (which re-login alone cannot fix).
    """
    import boto3
    from botocore.exceptions import (
        ClientError,
        SSOTokenLoadError,
        TokenRetrievalError,
        UnauthorizedSSOTokenError,
    )

    if sso_client is None:
        sso_client = boto3.Session().client("sso", region_name=config.sso.sso_region)
    target_region = region if region is not None else config.bedrock_region
    log_aws_call(
        logger,
        "sso.GetRoleCredentials",
        account_id=account_id,
        role_name=role_name,
        sso_region=config.sso.sso_region,
        target_region=target_region,
    )
    try:
        response = sso_client.get_role_credentials(
            roleName=role_name, accountId=account_id, accessToken=token.access_token
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        log_aws_error(logger, "sso.GetRoleCredentials", code)
        if code == "UnauthorizedException":
            raise AuthExpiredError(f"SSO credentials rejected; {_RELOGIN_HINT}") from exc
        raise AuthError(
            f"AWS rejected role {role_name!r} in account {account_id}"
            f" ({code or 'unknown error'}). The role may not be assigned to you -"
            " re-run 'haru login' to choose again, or fix auth.sso.role_name in"
            " your configuration."
        ) from exc
    except (SSOTokenLoadError, UnauthorizedSSOTokenError, TokenRetrievalError) as exc:
        log_aws_error(logger, "sso.GetRoleCredentials", "TokenError")
        raise AuthExpiredError(f"SSO credentials rejected; {_RELOGIN_HINT}") from exc

    credentials = response["roleCredentials"]
    log_aws_call(
        logger, "sso.GetRoleCredentials", outcome="ok", expiration=credentials.get("expiration")
    )
    return boto3.Session(
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        aws_session_token=credentials["sessionToken"],
        region_name=target_region,
    )


def build_boto3_session(
    config: AuthConfig,
    *,
    oidc: Any | None = None,
    sso_client: Any | None = None,
    cache_dir: Path | None = None,
) -> boto3.Session:
    """Build a boto3 session with role credentials from the cached SSO token.

    Resolves the account and role from configuration pins, the account-id
    environment variable, then the identity chosen at login.
    """
    token = ensure_token(config, oidc=oidc, cache_dir=cache_dir)
    account_id, role_name = _resolve_identity(config.sso, cache_dir)
    return session_for_role(config, token, account_id, role_name, sso_client=sso_client)


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
    log_aws_call(logger, "sso-oidc.CreateToken", grant_type="refresh_token", region=sso.sso_region)
    try:
        response = oidc.create_token(
            clientId=token.registration.client_id,
            clientSecret=token.registration.client_secret,
            grantType="refresh_token",
            refreshToken=token.refresh_token,
        )
    except ClientError as exc:
        log_aws_error(logger, "sso-oidc.CreateToken", exc.response.get("Error", {}).get("Code", ""))
        raise AuthExpiredError(f"SSO token refresh failed; {_RELOGIN_HINT}") from exc

    log_aws_call(logger, "sso-oidc.CreateToken", outcome="ok", expires_in=response.get("expiresIn"))
    refreshed = SsoToken(
        access_token=response["accessToken"],
        refresh_token=response.get("refreshToken", token.refresh_token),
        expires_at=now + timedelta(seconds=response["expiresIn"]),
        registration=token.registration,
    )
    write_token_cache(refreshed, sso.start_url, sso.sso_region, cache_dir=cache_dir)
    return refreshed
