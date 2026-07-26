"""Discover and persist the AWS account and role behind an SSO sign-in.

Kiro-style browser sign-in needs no upfront AWS knowledge: after the token
exchange, the accessible accounts and permission-set roles are listed from
IAM Identity Center (as ``aws configure sso`` does), auto-selected when there
is exactly one, and persisted next to the token cache for later sessions.

Configuration pins (``account_id``/``role_name``) are validated against the
user's actual assignments; a stale pin produces a warning and falls back to
the chooser instead of persisting a broken identity.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haru.auth.cache import cache_path
from haru.auth.sso import SsoToken
from haru.config.schema import SsoConfig
from haru.errors import AuthError

Chooser = Callable[[str, list[str]], int]
"""Callback picking one option: (question, labels) -> chosen index."""

Notify = Callable[[str], None]
"""Callback for user-facing warnings during selection."""

SOURCE_SELECTED = "login selection"
SOURCE_CONFIG = "pinned in config"


@dataclass(frozen=True)
class SelectedIdentity:
    """The AWS account and role selected for Bedrock access."""

    account_id: str
    role_name: str
    account_name: str | None = None
    account_source: str = SOURCE_SELECTED
    role_source: str = SOURCE_SELECTED


def identity_path(start_url: str, cache_dir: Path | None = None) -> Path:
    """Return the persisted-identity path for ``start_url``."""
    token_path = cache_path(start_url, cache_dir)
    return token_path.with_name(f"{token_path.stem}-haru-identity.json")


def write_identity(
    identity: SelectedIdentity, start_url: str, cache_dir: Path | None = None
) -> Path:
    """Persist the selected identity next to the token cache."""
    path = identity_path(start_url, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "accountId": identity.account_id,
        "roleName": identity.role_name,
        "accountName": identity.account_name,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def read_identity(start_url: str, cache_dir: Path | None = None) -> SelectedIdentity | None:
    """Read the persisted identity; None when absent or unreadable."""
    path = identity_path(start_url, cache_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SelectedIdentity(
            account_id=payload["accountId"],
            role_name=payload["roleName"],
            account_name=payload.get("accountName"),
        )
    except (json.JSONDecodeError, KeyError):
        return None


def effective_identity(
    sso_cfg: SsoConfig, cache_dir: Path | None = None
) -> tuple[str | None, str, str | None, str]:
    """Resolve the effective account and role with their provenance.

    Returns ``(account_id, account_source, role_name, role_source)``;
    values are None (source ``"unset"``) when nothing provides them.
    Precedence per field: config pin, then ``account_id_env``, then the
    identity persisted at login.
    """
    stored = read_identity(sso_cfg.start_url, cache_dir)

    if sso_cfg.account_id is not None:
        account, account_source = sso_cfg.account_id, SOURCE_CONFIG
    elif sso_cfg.account_id_env is not None and os.environ.get(sso_cfg.account_id_env):
        account = os.environ[sso_cfg.account_id_env]
        account_source = f"from ${sso_cfg.account_id_env}"
    elif stored is not None:
        account, account_source = stored.account_id, SOURCE_SELECTED
    else:
        account, account_source = None, "unset"

    if sso_cfg.role_name is not None:
        role, role_source = sso_cfg.role_name, SOURCE_CONFIG
    elif stored is not None:
        role, role_source = stored.role_name, SOURCE_SELECTED
    else:
        role, role_source = None, "unset"

    return account, account_source, role, role_source


def select_identity(  # noqa: PLR0913 - keyword-only wiring points, all optional
    sso_cfg: SsoConfig,
    token: SsoToken,
    *,
    sso_client: Any | None = None,
    cache_dir: Path | None = None,
    chooser: Chooser | None = None,
    notify: Notify | None = None,
) -> SelectedIdentity:
    """Resolve the account and role for this sign-in and persist the choice.

    Configuration pins are honoured only when they match the user's actual
    assignments; a stale pin triggers a ``notify`` warning and falls back to
    discovery. With multiple choices and no ``chooser``, raises AuthError.
    """
    if sso_client is None:
        import boto3

        sso_client = boto3.Session().client("sso", region_name=sso_cfg.sso_region)
    warn = notify if notify is not None else _ignore_warning

    accounts = list_accounts(sso_client, token.access_token)
    pinned_account, pin_source = _pinned_account(sso_cfg)
    account_source = SOURCE_SELECTED
    chosen = next((account for account in accounts if account["accountId"] == pinned_account), None)
    if chosen is not None:
        account_source = pin_source
    else:
        if pinned_account is not None:
            warn(
                f"Warning: configured account {pinned_account} is not accessible with"
                " this sign-in; choose from your accessible accounts."
            )
        chosen = _choose(
            "Choose the AWS account to use",
            accounts,
            lambda account: f"{account['accountId']}  {account.get('accountName', '')}".strip(),
            chooser,
        )
    account_id: str = chosen["accountId"]
    account_name: str | None = chosen.get("accountName")

    roles = list_roles(sso_client, token.access_token, account_id)
    role_source = SOURCE_SELECTED
    if sso_cfg.role_name is not None and sso_cfg.role_name in roles:
        role_name = sso_cfg.role_name
        role_source = SOURCE_CONFIG
    else:
        if sso_cfg.role_name is not None:
            warn(
                f"Warning: configured role {sso_cfg.role_name!r} is not assigned to you"
                f" in account {account_id}; choose from your assigned roles."
            )
        role_name = _choose(f"Choose the role for account {account_id}", roles, str, chooser)

    identity = SelectedIdentity(
        account_id=account_id,
        role_name=role_name,
        account_name=account_name,
        account_source=account_source,
        role_source=role_source,
    )
    write_identity(identity, sso_cfg.start_url, cache_dir)
    return identity


def _ignore_warning(message: str) -> None:
    """Default notify callback: drop the warning (non-interactive callers)."""


def _pinned_account(sso_cfg: SsoConfig) -> tuple[str | None, str]:
    if sso_cfg.account_id is not None:
        return sso_cfg.account_id, SOURCE_CONFIG
    if sso_cfg.account_id_env is not None:
        value = os.environ.get(sso_cfg.account_id_env)
        if value:
            return value, f"from ${sso_cfg.account_id_env}"
    return None, ""


def list_accounts(sso_client: Any, access_token: str) -> list[dict[str, Any]]:
    from botocore.exceptions import ClientError

    try:
        paginator = sso_client.get_paginator("list_accounts")
        accounts = [
            account
            for page in paginator.paginate(accessToken=access_token)
            for account in page.get("accountList", [])
        ]
    except ClientError as exc:
        raise AuthError(f"Listing accessible AWS accounts failed: {exc}") from exc
    if not accounts:
        raise AuthError("This sign-in grants access to no AWS accounts; contact your admin")
    return sorted(accounts, key=lambda account: str(account["accountId"]))


def list_roles(sso_client: Any, access_token: str, account_id: str) -> list[str]:
    from botocore.exceptions import ClientError

    try:
        paginator = sso_client.get_paginator("list_account_roles")
        roles = [
            role["roleName"]
            for page in paginator.paginate(accessToken=access_token, accountId=account_id)
            for role in page.get("roleList", [])
        ]
    except ClientError as exc:
        raise AuthError(f"Listing roles for account {account_id} failed: {exc}") from exc
    if not roles:
        raise AuthError(f"No roles are available in account {account_id}; contact your admin")
    return sorted(roles)


def _choose[Item](
    question: str,
    options: list[Item],
    label: Callable[[Item], str],
    chooser: Chooser | None,
) -> Item:
    if len(options) == 1:
        return options[0]
    if chooser is None:
        raise AuthError(
            f"{question}: multiple options available; re-run 'haru login' interactively"
            " or pin the choice in configuration"
        )
    index = chooser(question, [label(option) for option in options])
    return options[index]
