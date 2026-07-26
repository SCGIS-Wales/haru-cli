"""Discover and persist the AWS account and role behind an SSO sign-in.

Kiro-style browser sign-in needs no upfront AWS knowledge: after the token
exchange, the accessible accounts and permission-set roles are listed from
IAM Identity Center (as ``aws configure sso`` does), auto-selected when there
is exactly one, and persisted next to the token cache for later sessions.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from haru.auth.cache import cache_path
from haru.auth.sso import SsoToken
from haru.config.schema import SsoConfig
from haru.errors import AuthError

Chooser = Callable[[str, list[str]], int]
"""Callback picking one option: (question, labels) -> chosen index."""


@dataclass(frozen=True)
class SelectedIdentity:
    """The AWS account and role selected for Bedrock access."""

    account_id: str
    role_name: str
    account_name: str | None = None


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


def select_identity(
    sso_cfg: SsoConfig,
    token: SsoToken,
    *,
    sso_client: Any | None = None,
    cache_dir: Path | None = None,
    chooser: Chooser | None = None,
) -> SelectedIdentity:
    """Resolve the account and role for this sign-in and persist the choice.

    Configuration pins win (``account_id``/``role_name``, or the account id
    from ``account_id_env``); anything unpinned is discovered from Identity
    Center. With multiple choices and no ``chooser``, raises AuthError.
    """
    if sso_client is None:
        sso_client = boto3.Session().client("sso", region_name=sso_cfg.sso_region)

    account_id = _pinned_account(sso_cfg)
    account_name: str | None = None
    if account_id is None:
        accounts = _list_accounts(sso_client, token.access_token)
        chosen = _choose(
            "Choose the AWS account to use",
            accounts,
            lambda account: f"{account['accountId']}  {account.get('accountName', '')}".strip(),
            chooser,
        )
        account_id = chosen["accountId"]
        account_name = chosen.get("accountName")

    role_name = sso_cfg.role_name
    if role_name is None:
        roles = _list_roles(sso_client, token.access_token, account_id)
        role_name = _choose(f"Choose the role for account {account_id}", roles, str, chooser)

    identity = SelectedIdentity(
        account_id=account_id, role_name=role_name, account_name=account_name
    )
    write_identity(identity, sso_cfg.start_url, cache_dir)
    return identity


def _pinned_account(sso_cfg: SsoConfig) -> str | None:
    if sso_cfg.account_id is not None:
        return sso_cfg.account_id
    if sso_cfg.account_id_env is not None:
        return os.environ.get(sso_cfg.account_id_env)
    return None


def _list_accounts(sso_client: Any, access_token: str) -> list[dict[str, Any]]:
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


def _list_roles(sso_client: Any, access_token: str, account_id: str) -> list[str]:
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
