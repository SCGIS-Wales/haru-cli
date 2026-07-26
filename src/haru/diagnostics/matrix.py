"""Probe every assigned account and role for Bedrock reachability.

This answers "which IAM role do I actually need?" empirically, by assuming
each role the user is assigned and testing Bedrock against it. Probing is
sequential and bounded: IAM Identity Center throttles ``GetRoleCredentials``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from haru.auth.identity import list_accounts, list_roles
from haru.auth.session import ensure_token, session_for_role
from haru.config.schema import HaruConfig
from haru.errors import HaruError
from haru.logging_setup import log_aws_call, log_aws_error
from haru.models.bedrock import get_model_config, resolve_model_id

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "denied", "error", "skip"]

DEFAULT_MAX_ROLES = 25


@dataclass(frozen=True)
class RoleProbe:
    """The result of probing one account and role combination."""

    account_id: str
    account_name: str | None
    role_name: str
    credentials: Outcome
    bedrock: Outcome
    invoke: Outcome = "skip"
    detail: str = ""

    @property
    def usable(self) -> bool:
        """True when this combination is the best candidate for Bedrock."""
        return self.invoke == "ok" or (self.invoke == "skip" and self.bedrock == "ok")

    @property
    def proven(self) -> bool:
        """True only when a real Converse call succeeded.

        The control plane is indicative, never proof: a role granted
        ``bedrock:InvokeModel`` on one model ARN without
        ``bedrock:ListFoundationModels`` fails the control-plane probe.
        """
        return self.invoke == "ok"


def probe_roles(  # noqa: PLR0913 - keyword-only injection seams, all optional
    config: HaruConfig,
    *,
    invoke: bool = False,
    account_filter: str | None = None,
    max_roles: int = DEFAULT_MAX_ROLES,
    cache_dir: Path | None = None,
    sso_client: Any | None = None,
) -> Iterator[RoleProbe]:
    """Yield one probe per assigned account and role, newest results first."""
    import boto3

    token = ensure_token(config.auth, cache_dir=cache_dir)
    if sso_client is None:
        sso_client = boto3.Session().client("sso", region_name=config.auth.sso.sso_region)

    entry = get_model_config(config) if config.models is not None else None
    region = entry.region if entry is not None else config.auth.bedrock_region
    model_id = resolve_model_id(entry.model_id) if entry is not None else None

    probed = 0
    for account in list_accounts(sso_client, token.access_token):
        account_id = str(account["accountId"])
        if account_filter is not None and account_id != account_filter:
            continue
        account_name = account.get("accountName")
        for role_name in list_roles(sso_client, token.access_token, account_id):
            if probed >= max_roles:
                return
            probed += 1
            yield _probe_one(
                config,
                token,
                account_id,
                account_name,
                role_name,
                region=region,
                model_id=model_id,
                invoke=invoke,
                sso_client=sso_client,
            )


def _probe_one(  # noqa: PLR0913 - one parameter per probed dimension
    config: HaruConfig,
    token: Any,
    account_id: str,
    account_name: str | None,
    role_name: str,
    *,
    region: str,
    model_id: str | None,
    invoke: bool,
    sso_client: Any,
) -> RoleProbe:
    """Probe a single account and role for credentials and Bedrock access."""
    try:
        session = session_for_role(
            config.auth, token, account_id, role_name, sso_client=sso_client, region=region
        )
    except HaruError as exc:
        return RoleProbe(account_id, account_name, role_name, "denied", "skip", detail=str(exc))

    log_aws_call(
        logger,
        "bedrock.ListFoundationModels",
        account_id=account_id,
        role_name=role_name,
        region=region,
    )
    try:
        session.client("bedrock", region_name=region).list_foundation_models()
        bedrock: Outcome = "ok"
        detail = ""
    except Exception as exc:
        bedrock = "denied" if _is_access_denied(exc) else "error"
        detail = _short(exc)
        log_aws_error(logger, "bedrock.ListFoundationModels", detail)

    invoke_outcome: Outcome = "skip"
    if invoke and model_id is not None:
        log_aws_call(
            logger,
            "bedrock-runtime.Converse",
            account_id=account_id,
            role_name=role_name,
            model_id=model_id,
            region=region,
            max_tokens=1,
        )
        try:
            session.client("bedrock-runtime", region_name=region).converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
                inferenceConfig={"maxTokens": 1},
            )
            invoke_outcome = "ok"
        except Exception as exc:
            invoke_outcome = "denied" if _is_access_denied(exc) else "error"
            # The invoke error is the decisive one; it must not be hidden
            # behind an earlier control-plane failure.
            detail = _short(exc)
            log_aws_error(logger, "bedrock-runtime.Converse", detail)

    return RoleProbe(account_id, account_name, role_name, "ok", bedrock, invoke_outcome, detail)


def _is_access_denied(exc: Exception) -> bool:
    """True when the exception is an AWS authorization failure."""
    from haru.models.errors import error_code

    return error_code(exc) in {"AccessDeniedException", "AccessDenied"}


def _short(exc: Exception) -> str:
    """A one-line error summary for the matrix detail column."""
    from haru.models.errors import error_code

    code = error_code(exc)
    return code if code else type(exc).__name__
