"""Tests for boto3 session construction from the cached SSO token."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from haru.auth.cache import read_token_cache, write_token_cache
from haru.auth.identity import SelectedIdentity, write_identity
from haru.auth.session import build_boto3_session
from haru.auth.sso import ClientRegistration, SsoToken
from haru.config.schema import AuthConfig
from haru.errors import AuthExpiredError

START_URL = "https://example.awsapps.com/start"

ROLE_CREDENTIALS = {
    "roleCredentials": {
        "accessKeyId": "AKIAEXAMPLE",
        "secretAccessKey": "secret-key",
        "sessionToken": "session-token",
        "expiration": 4102444800000,
    }
}


def make_config() -> AuthConfig:
    """Build an AuthConfig pointing at the test cache."""
    return AuthConfig.model_validate(
        {
            "sso": {
                "start_url": START_URL,
                "sso_region": "us-east-1",
                "account_id_env": "HARU_AWS_ACCOUNT_ID",
                "role_name": "HaruBedrockInvoke",
            },
            "bedrock_region": "eu-west-1",
        }
    )


def seed_cache(
    tmp_path: Path,
    *,
    expires_in: timedelta = timedelta(hours=2),
    refresh_token: str | None = "refresh-abc",
    registration_expires_in: timedelta = timedelta(days=30),
) -> SsoToken:
    """Write a cached token with the given lifetimes and return it."""
    now = datetime.now(UTC).replace(microsecond=0)
    token = SsoToken(
        access_token="access-abc",
        refresh_token=refresh_token,
        expires_at=now + expires_in,
        registration=ClientRegistration(
            client_id="client-123",
            client_secret="client-secret",
            expires_at=now + registration_expires_in,
        ),
    )
    write_token_cache(token, START_URL, "us-east-1", cache_dir=tmp_path)
    return token


@pytest.fixture
def account_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the AWS account id environment variable."""
    monkeypatch.setenv("HARU_AWS_ACCOUNT_ID", "123456789012")


@pytest.mark.usefixtures("account_env")
def test_valid_token_builds_session(tmp_path: Path, mocker: Any) -> None:
    """A fresh cached token exchanges for role credentials without refresh."""
    seed_cache(tmp_path)
    oidc = mocker.Mock()
    sso_client = mocker.Mock()
    sso_client.get_role_credentials.return_value = ROLE_CREDENTIALS

    session = build_boto3_session(
        make_config(), oidc=oidc, sso_client=sso_client, cache_dir=tmp_path
    )

    oidc.create_token.assert_not_called()
    sso_client.get_role_credentials.assert_called_once_with(
        roleName="HaruBedrockInvoke", accountId="123456789012", accessToken="access-abc"
    )
    assert session.region_name == "eu-west-1"
    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.access_key == "AKIAEXAMPLE"


@pytest.mark.usefixtures("account_env")
def test_expiring_token_is_refreshed_and_recached(tmp_path: Path, mocker: Any) -> None:
    """A token inside the refresh window is refreshed and written back."""
    seed_cache(tmp_path, expires_in=timedelta(minutes=5))
    oidc = mocker.Mock()
    oidc.create_token.return_value = {
        "accessToken": "access-new",
        "refreshToken": "refresh-new",
        "expiresIn": 3600,
    }
    sso_client = mocker.Mock()
    sso_client.get_role_credentials.return_value = ROLE_CREDENTIALS

    build_boto3_session(make_config(), oidc=oidc, sso_client=sso_client, cache_dir=tmp_path)

    oidc.create_token.assert_called_once_with(
        clientId="client-123",
        clientSecret="client-secret",
        grantType="refresh_token",
        refreshToken="refresh-abc",
    )
    sso_client.get_role_credentials.assert_called_once()
    call_kwargs = sso_client.get_role_credentials.call_args.kwargs
    assert call_kwargs["accessToken"] == "access-new"
    cached = read_token_cache(START_URL, cache_dir=tmp_path)
    assert cached is not None
    assert cached.access_token == "access-new"
    assert cached.refresh_token == "refresh-new"


def test_missing_cache_raises(tmp_path: Path) -> None:
    """No cached token means a login is required."""
    with pytest.raises(AuthExpiredError, match="haru login"):
        build_boto3_session(make_config(), cache_dir=tmp_path)


def test_expired_without_refresh_token_raises(tmp_path: Path, mocker: Any) -> None:
    """An expired token without a refresh token requires re-login."""
    seed_cache(tmp_path, expires_in=timedelta(minutes=1), refresh_token=None)
    with pytest.raises(AuthExpiredError, match="cannot be refreshed"):
        build_boto3_session(make_config(), oidc=mocker.Mock(), cache_dir=tmp_path)


def test_expired_registration_raises(tmp_path: Path, mocker: Any) -> None:
    """An expired client registration cannot refresh; re-login required."""
    seed_cache(
        tmp_path,
        expires_in=timedelta(minutes=1),
        registration_expires_in=timedelta(seconds=-60),
    )
    with pytest.raises(AuthExpiredError, match="cannot be refreshed"):
        build_boto3_session(make_config(), oidc=mocker.Mock(), cache_dir=tmp_path)


def test_refresh_failure_raises(tmp_path: Path, mocker: Any) -> None:
    """A rejected refresh grant surfaces as AuthExpiredError."""
    seed_cache(tmp_path, expires_in=timedelta(minutes=1))
    oidc = mocker.Mock()
    oidc.create_token.side_effect = ClientError(
        {"Error": {"Code": "InvalidGrantException", "Message": "expired"}}, "CreateToken"
    )
    with pytest.raises(AuthExpiredError, match="refresh failed"):
        build_boto3_session(make_config(), oidc=oidc, cache_dir=tmp_path)


@pytest.mark.usefixtures("account_env")
def test_unauthorized_role_credentials_raises(tmp_path: Path, mocker: Any) -> None:
    """UnauthorizedException from get_role_credentials requires re-login."""
    seed_cache(tmp_path)
    sso_client = mocker.Mock()
    sso_client.get_role_credentials.side_effect = ClientError(
        {"Error": {"Code": "UnauthorizedException", "Message": "expired"}}, "GetRoleCredentials"
    )
    with pytest.raises(AuthExpiredError, match="rejected"):
        build_boto3_session(make_config(), sso_client=sso_client, cache_dir=tmp_path)


def test_no_identity_anywhere_requires_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No pin, no env var, no stored identity: re-login is required."""
    monkeypatch.delenv("HARU_AWS_ACCOUNT_ID", raising=False)
    seed_cache(tmp_path)
    with pytest.raises(AuthExpiredError, match="haru login"):
        build_boto3_session(make_config(), cache_dir=tmp_path)


def test_stored_identity_used_when_unpinned(tmp_path: Path, mocker: Any) -> None:
    """The identity chosen at login backs the session when config pins nothing."""
    seed_cache(tmp_path)
    write_identity(
        SelectedIdentity(account_id="777788889999", role_name="StoredRole"),
        START_URL,
        tmp_path,
    )
    sso_client = mocker.Mock()
    sso_client.get_role_credentials.return_value = ROLE_CREDENTIALS
    config = AuthConfig.model_validate(
        {
            "sso": {"start_url": START_URL, "sso_region": "us-east-1"},
            "bedrock_region": "eu-west-1",
        }
    )

    build_boto3_session(config, sso_client=sso_client, cache_dir=tmp_path)

    sso_client.get_role_credentials.assert_called_once_with(
        roleName="StoredRole", accountId="777788889999", accessToken="access-abc"
    )


def test_config_pins_beat_stored_identity(tmp_path: Path, mocker: Any) -> None:
    """Explicit config account/role win over the stored login choice."""
    seed_cache(tmp_path)
    write_identity(
        SelectedIdentity(account_id="777788889999", role_name="StoredRole"),
        START_URL,
        tmp_path,
    )
    sso_client = mocker.Mock()
    sso_client.get_role_credentials.return_value = ROLE_CREDENTIALS
    config = AuthConfig.model_validate(
        {
            "sso": {
                "start_url": START_URL,
                "sso_region": "us-east-1",
                "account_id": "123456789012",
                "role_name": "PinnedRole",
            },
            "bedrock_region": "eu-west-1",
        }
    )

    build_boto3_session(config, sso_client=sso_client, cache_dir=tmp_path)

    sso_client.get_role_credentials.assert_called_once_with(
        roleName="PinnedRole", accountId="123456789012", accessToken="access-abc"
    )
