"""Tests for account/role discovery and persistence after SSO sign-in."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from haru.auth.identity import (
    SOURCE_CONFIG,
    SOURCE_PIN_REJECTED,
    SOURCE_SELECTED,
    SelectedIdentity,
    effective_identity,
    identity_path,
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


def test_pinned_account_used_when_accessible(tmp_path: Path) -> None:
    """A configured account_id is honoured when it is actually accessible."""
    sso = FakeSso([{"accountId": "999900001111"}], {"999900001111": ["BedrockAccess"]})

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
    sso = FakeSso([{"accountId": "999900001111"}], {"999900001111": ["BedrockAccess"]})

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


def test_stale_pinned_role_warns_and_falls_back(tmp_path: Path) -> None:
    """A role that is not assigned triggers a warning and the role chooser."""
    sso = FakeSso(
        [{"accountId": "111122223333", "accountName": "Sandbox"}],
        {"111122223333": ["Assigned-A", "Assigned-B"]},
    )
    warnings: list[str] = []

    identity = select_identity(
        make_sso_config(role_name="HaruBedrockInvoke"),
        make_token(),
        sso_client=sso,
        cache_dir=tmp_path,
        chooser=lambda question, labels: 0,
        notify=warnings.append,
    )

    assert identity.role_name == "Assigned-A"
    assert identity.role_source == "login selection"
    assert any("HaruBedrockInvoke" in message for message in warnings)
    assert read_identity(START_URL, tmp_path) is not None


def test_valid_pinned_role_kept(tmp_path: Path) -> None:
    """A pinned role that is assigned is used and marked as config-sourced."""
    sso = FakeSso([{"accountId": "111122223333"}], {"111122223333": ["Assigned-A", "Pinned"]})
    warnings: list[str] = []

    identity = select_identity(
        make_sso_config(role_name="Pinned"),
        make_token(),
        sso_client=sso,
        cache_dir=tmp_path,
        notify=warnings.append,
    )

    assert identity.role_name == "Pinned"
    assert identity.role_source == "pinned in config"
    assert warnings == []


def test_stale_pinned_account_warns_and_falls_back(tmp_path: Path) -> None:
    """An inaccessible pinned account warns and falls back to the chooser."""
    sso = FakeSso([{"accountId": "111122223333"}], {"111122223333": ["BedrockAccess"]})
    warnings: list[str] = []

    identity = select_identity(
        make_sso_config(account_id="999999999999"),
        make_token(),
        sso_client=sso,
        cache_dir=tmp_path,
        notify=warnings.append,
    )

    assert identity.account_id == "111122223333"
    assert any("999999999999" in message for message in warnings)


def test_effective_identity_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """effective_identity reports where each value came from."""
    monkeypatch.delenv("HARU_AWS_ACCOUNT_ID", raising=False)
    assert effective_identity(make_sso_config(), tmp_path) == (None, "unset", None, "unset")

    write_identity(
        SelectedIdentity(account_id="111122223333", role_name="Chosen"), START_URL, tmp_path
    )
    assert effective_identity(make_sso_config(), tmp_path) == (
        "111122223333",
        "login selection",
        "Chosen",
        "login selection",
    )

    pinned = make_sso_config(account_id="999999999999", role_name="Pinned")
    assert effective_identity(pinned, tmp_path) == (
        "999999999999",
        "pinned in config",
        "Pinned",
        "pinned in config",
    )


def test_rejected_role_pin_is_persisted(tmp_path: Path) -> None:
    """A pin the sign-in disproved is recorded alongside the identity."""
    sso = FakeSso([{"accountId": "111122223333"}], {"111122223333": ["Assigned-A"]})

    identity = select_identity(
        make_sso_config(role_name="HaruBedrockInvoke"),
        make_token(),
        sso_client=sso,
        cache_dir=tmp_path,
    )

    assert identity.role_name == "Assigned-A"
    assert identity.role_source == SOURCE_SELECTED
    stored = read_identity(START_URL, tmp_path)
    assert stored is not None
    assert stored.rejected_role_pin == "HaruBedrockInvoke"


def test_rejected_pin_does_not_override_login_selection(tmp_path: Path) -> None:
    """Regression: the login fallback must survive the next command.

    Before this, effective_identity re-applied the pin unconditionally, so the
    role chosen at login was silently discarded on the very next invocation.
    """
    sso = FakeSso([{"accountId": "111122223333"}], {"111122223333": ["Assigned-A"]})
    config = make_sso_config(role_name="HaruBedrockInvoke")
    select_identity(config, make_token(), sso_client=sso, cache_dir=tmp_path)

    assert effective_identity(config, tmp_path) == (
        "111122223333",
        SOURCE_SELECTED,
        "Assigned-A",
        SOURCE_PIN_REJECTED,
    )


def test_valid_pin_still_wins(tmp_path: Path) -> None:
    """A pin that is actually assigned keeps taking precedence."""
    sso = FakeSso([{"accountId": "111122223333"}], {"111122223333": ["Assigned-A", "Pinned"]})
    config = make_sso_config(role_name="Pinned")
    identity = select_identity(config, make_token(), sso_client=sso, cache_dir=tmp_path)

    assert identity.rejected_role_pin is None
    assert effective_identity(config, tmp_path)[3] == SOURCE_CONFIG


def test_changed_pin_gets_a_fresh_chance(tmp_path: Path) -> None:
    """Only the exact disproved value loses; editing the pin re-arms it."""
    write_identity(
        SelectedIdentity(
            account_id="111122223333", role_name="Assigned-A", rejected_role_pin="OldPin"
        ),
        START_URL,
        tmp_path,
    )

    assert effective_identity(make_sso_config(role_name="NewPin"), tmp_path)[2:] == (
        "NewPin",
        SOURCE_CONFIG,
    )


def test_rejected_account_pin_falls_back(tmp_path: Path) -> None:
    """An account pin the sign-in disproved also stops winning."""
    sso = FakeSso([{"accountId": "111122223333"}], {"111122223333": ["Assigned-A"]})
    config = make_sso_config(account_id="999999999999")
    select_identity(config, make_token(), sso_client=sso, cache_dir=tmp_path)

    assert effective_identity(config, tmp_path)[:2] == ("111122223333", SOURCE_PIN_REJECTED)


def test_legacy_identity_file_without_new_fields(tmp_path: Path) -> None:
    """A pre-0.1.7 identity file reads as 'nothing disproved', so pins still win."""
    path = identity_path(START_URL, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"accountId": "111122223333", "roleName": "Chosen", "accountName": None}),
        encoding="utf-8",
    )

    stored = read_identity(START_URL, tmp_path)
    assert stored is not None
    assert stored.rejected_role_pin is None
    assert effective_identity(make_sso_config(role_name="Pinned"), tmp_path)[3] == SOURCE_CONFIG
