"""Tests for account/role discovery and persistence after SSO sign-in."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from haru.auth.identity import (
    SelectedIdentity,
    read_identity,
    select_identity,
    write_identity,
)
from haru.auth.sso import ClientRegistration, SsoToken
from haru.config.schema import SsoConfig
from haru.errors import AuthError

START_URL = "https://example.awsapps.com/start"


def make_sso_config(**overrides: Any) -> SsoConfig:
    """Build an SsoConfig for tests."""
    return SsoConfig.model_validate(
        {"start_url": START_URL, "sso_region": "us-east-1", **overrides}
    )


def make_token() -> SsoToken:
    """Build a valid SsoToken for tests."""
    now = datetime.now(UTC)
    return SsoToken(
        access_token="access-abc",
        refresh_token=None,
        expires_at=now + timedelta(hours=1),
        registration=ClientRegistration(
            client_id="client-123", client_secret="client-secret", expires_at=now
        ),
    )


class FakeSso:
    """Minimal sso client stand-in with paginators."""

    def __init__(self, accounts: list[dict[str, Any]], roles: dict[str, list[str]]) -> None:
        self._accounts = accounts
        self._roles = roles
        self.role_calls: list[str] = []

    def get_paginator(self, operation: str) -> Any:
        fake = self

        class _Paginator:
            def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
                if operation == "list_accounts":
                    return [{"accountList": fake._accounts}]
                fake.role_calls.append(kwargs["accountId"])
                roles = fake._roles.get(kwargs["accountId"], [])
                return [{"roleList": [{"roleName": name} for name in roles]}]

        return _Paginator()


def test_identity_roundtrip(tmp_path: Path) -> None:
    """A written identity reads back identically, with 0600 permissions."""
    identity = SelectedIdentity(account_id="111122223333", role_name="Dev", account_name="Sandbox")
    path = write_identity(identity, START_URL, tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_identity(START_URL, tmp_path) == identity


def test_read_missing_identity(tmp_path: Path) -> None:
    """A missing identity file reads as None."""
    assert read_identity(START_URL, tmp_path) is None


def test_single_account_and_role_auto_selected(tmp_path: Path) -> None:
    """One account with one role needs no interaction and is persisted."""
    sso = FakeSso(
        [{"accountId": "111122223333", "accountName": "Sandbox"}],
        {"111122223333": ["BedrockAccess"]},
    )

    identity = select_identity(make_sso_config(), make_token(), sso_client=sso, cache_dir=tmp_path)

    assert identity == SelectedIdentity(
        account_id="111122223333", role_name="BedrockAccess", account_name="Sandbox"
    )
    assert read_identity(START_URL, tmp_path) == identity


def test_multiple_accounts_use_chooser(tmp_path: Path) -> None:
    """The chooser picks among multiple accounts and roles."""
    sso = FakeSso(
        [
            {"accountId": "111122223333", "accountName": "Sandbox"},
            {"accountId": "444455556666", "accountName": "Prod"},
        ],
        {"444455556666": ["Admin", "ReadOnly"]},
    )
    questions: list[tuple[str, list[str]]] = []

    def choose_last(question: str, labels: list[str]) -> int:
        questions.append((question, labels))
        return len(labels) - 1

    identity = select_identity(
        make_sso_config(), make_token(), sso_client=sso, cache_dir=tmp_path, chooser=choose_last
    )

    assert identity.account_id == "444455556666"
    assert identity.role_name == "ReadOnly"
    assert len(questions) == 2
    assert "444455556666" in questions[0][1][1]


def test_multiple_accounts_without_chooser_raises(tmp_path: Path) -> None:
    """Multiple options with no chooser fail with guidance."""
    sso = FakeSso(
        [{"accountId": "111122223333"}, {"accountId": "444455556666"}],
        {},
    )
    with pytest.raises(AuthError, match="multiple options"):
        select_identity(make_sso_config(), make_token(), sso_client=sso, cache_dir=tmp_path)


def test_pinned_account_skips_account_listing(tmp_path: Path) -> None:
    """A configured account_id goes straight to role discovery."""
    sso = FakeSso([], {"999900001111": ["BedrockAccess"]})

    identity = select_identity(
        make_sso_config(account_id="999900001111"),
        make_token(),
        sso_client=sso,
        cache_dir=tmp_path,
    )

    assert identity.account_id == "999900001111"
    assert sso.role_calls == ["999900001111"]


def test_env_pinned_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """account_id_env still pins the account when set."""
    monkeypatch.setenv("HARU_AWS_ACCOUNT_ID", "999900001111")
    sso = FakeSso([], {"999900001111": ["BedrockAccess"]})

    identity = select_identity(
        make_sso_config(account_id_env="HARU_AWS_ACCOUNT_ID"),
        make_token(),
        sso_client=sso,
        cache_dir=tmp_path,
    )

    assert identity.account_id == "999900001111"


def test_no_accounts_raises(tmp_path: Path) -> None:
    """A sign-in granting no accounts fails with a clear message."""
    sso = FakeSso([], {})
    with pytest.raises(AuthError, match="no AWS accounts"):
        select_identity(make_sso_config(), make_token(), sso_client=sso, cache_dir=tmp_path)


def test_listing_failure_wrapped(tmp_path: Path, mocker: Any) -> None:
    """botocore errors surface as AuthError."""
    sso = mocker.Mock()
    sso.get_paginator.return_value.paginate.side_effect = ClientError(
        {"Error": {"Code": "UnauthorizedException", "Message": "no"}}, "ListAccounts"
    )
    with pytest.raises(AuthError, match="Listing accessible AWS accounts failed"):
        select_identity(make_sso_config(), make_token(), sso_client=sso, cache_dir=tmp_path)
