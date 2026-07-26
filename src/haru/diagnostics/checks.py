"""Individual diagnostic checks for ``haru doctor``.

Each check is a pure function returning a :class:`CheckResult`; the command
layer only formats. Checks are ordered as a dependency chain, and anything
downstream of a failure is reported as skipped rather than raising.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from haru.auth.cache import cache_path, read_token_cache
from haru.auth.identity import effective_identity
from haru.auth.session import ensure_token, session_for_role
from haru.config.schema import HaruConfig
from haru.errors import HaruError
from haru.models.bedrock import get_model_config, list_models, resolve_model_id
from haru.models.errors import INVOKE_ACTIONS

Status = Literal["pass", "fail", "warn", "skip"]

_REFRESH_WINDOW_MINUTES = 15


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one diagnostic check."""

    name: str
    status: Status
    detail: str
    remediation: str = ""
    iam_actions: tuple[str, ...] = field(default=())


def run_checks(  # noqa: PLR0913 - keyword-only injection seams, all optional
    config: HaruConfig,
    config_path: Path,
    *,
    invoke: bool = False,
    cache_dir: Path | None = None,
    sso_client: Any | None = None,
    session_factory: Callable[..., Any] | None = None,
) -> Iterator[CheckResult]:
    """Yield diagnostic results in dependency order, skipping after a failure."""
    yield check_config(config, config_path)
    yield check_guardrails(config)
    yield from check_regions(config)

    token_result = check_token(config, cache_dir)
    yield token_result
    identity_result = check_identity(config, cache_dir)
    yield identity_result

    if token_result.status == "fail" or identity_result.status == "fail":
        yield _skipped("AWS credentials", "sign-in checks failed")
        yield _skipped("Bedrock access", "sign-in checks failed")
        return

    account_id, _, role_name, _ = effective_identity(config.auth.sso, cache_dir)
    try:
        session = _build_session(
            config, account_id, role_name, cache_dir, sso_client, session_factory
        )
    except HaruError as exc:
        yield CheckResult(
            "AWS credentials",
            "fail",
            str(exc),
            remediation="Re-run 'haru login' and choose an account and role you are assigned.",
        )
        yield _skipped("Bedrock access", "could not obtain role credentials")
        return
    yield CheckResult(
        "AWS credentials",
        "pass",
        f"Obtained credentials for role {role_name} in account {account_id}.",
    )

    yield check_caller_identity(session)
    yield check_bedrock_control_plane(session, _effective_region(config))
    yield from check_inference_profiles(session, config)
    if invoke:
        yield check_invoke(config, session)
    else:
        yield _skipped(
            "Bedrock invoke (live)",
            "not requested; re-run with --invoke for a definitive answer",
        )


def check_config(config: HaruConfig, config_path: Path) -> CheckResult:
    """Report which configuration file was loaded."""
    return CheckResult("Configuration", "pass", f"Loaded {config_path}.")


def check_guardrails(config: HaruConfig) -> CheckResult:
    """Guardrails must carry an id when enabled (they fail closed)."""
    guardrails = config.guardrails
    if guardrails is None or not guardrails.enabled:
        return CheckResult("Guardrails", "pass", "Guardrails are disabled.")
    if not guardrails.guardrail_id:
        return CheckResult(
            "Guardrails",
            "fail",
            "Guardrails are enabled but no guardrail_id is configured.",
            remediation="Set guardrails.guardrail_id, or set enabled: false.",
        )
    return CheckResult(
        "Guardrails",
        "pass",
        f"Guardrail {guardrails.guardrail_id} (version {guardrails.guardrail_version}).",
        remediation="",
        iam_actions=("bedrock:ApplyGuardrail",),
    )


def check_regions(config: HaruConfig) -> Iterator[CheckResult]:
    """Warn when a model's region differs from the configured Bedrock region."""
    if config.models is None:
        return
    auth_region = config.auth.bedrock_region
    divergent = {
        name: entry.region
        for name, entry in config.models.models.items()
        if entry.region != auth_region
    }
    if not divergent:
        yield CheckResult("Regions", "pass", f"All models use {auth_region}.")
        return
    listed = ", ".join(f"{name}={region}" for name, region in sorted(divergent.items()))
    yield CheckResult(
        "Regions",
        "warn",
        f"auth.bedrock_region is {auth_region} but {listed}.",
        remediation="The model's region wins for Bedrock calls; align them if unintended.",
    )


def check_token(config: HaruConfig, cache_dir: Path | None) -> CheckResult:
    """Report SSO token presence and expiry (never the token itself)."""
    sso = config.auth.sso
    path = cache_path(sso.start_url, cache_dir)
    try:
        token = read_token_cache(sso.start_url, cache_dir=cache_dir)
    except HaruError as exc:
        return CheckResult("SSO token", "fail", str(exc), remediation="Run 'haru login'.")
    if token is None:
        return CheckResult(
            "SSO token",
            "fail",
            f"No cached token at {path}.",
            remediation="Run 'haru login'.",
        )
    remaining = token.expires_at - datetime.now(UTC)
    minutes = int(remaining.total_seconds() // 60)
    if minutes <= 0:
        if token.refresh_token is None:
            return CheckResult(
                "SSO token",
                "fail",
                f"Token expired at {token.expires_at:%Y-%m-%d %H:%M UTC} and cannot refresh.",
                remediation="Run 'haru login'.",
            )
        return CheckResult(
            "SSO token", "warn", "Token expired; haru will refresh it on the next call."
        )
    if minutes <= _REFRESH_WINDOW_MINUTES:
        return CheckResult(
            "SSO token", "warn", f"Token expires in {minutes} min; haru will refresh it."
        )
    return CheckResult("SSO token", "pass", f"Valid for {minutes} min ({path}).")


def check_identity(config: HaruConfig, cache_dir: Path | None) -> CheckResult:
    """Report the effective account and role plus where each came from."""
    account_id, account_source, role_name, role_source = effective_identity(
        config.auth.sso, cache_dir
    )
    if account_id is None or role_name is None:
        return CheckResult(
            "Identity",
            "fail",
            "No AWS account or role has been selected.",
            remediation="Run 'haru login' and choose an account and role.",
        )
    detail = f"account {account_id} ({account_source}), role {role_name} ({role_source})"
    if "pinned in config" in (account_source, role_source):
        return CheckResult(
            "Identity",
            "warn",
            detail,
            remediation=(
                "Pinned values are used as-is. If sign-in fails, remove the pin from"
                " auth.sso and re-run 'haru login' to choose from your assignments."
            ),
        )
    return CheckResult("Identity", "pass", detail)


def check_caller_identity(session: Any) -> CheckResult:
    """Confirm the credentials are live and show the assumed-role ARN."""
    try:
        identity = session.client("sts").get_caller_identity()
    except Exception as exc:
        return CheckResult("Caller identity", "fail", _describe(exc))
    return CheckResult(
        "Caller identity",
        "pass",
        f"{identity.get('Arn', 'unknown')} (account {identity.get('Account', 'unknown')}).",
    )


def check_bedrock_control_plane(session: Any, region: str) -> CheckResult:
    """List foundation models; indicative of Bedrock access, not proof."""
    try:
        response = session.client("bedrock", region_name=region).list_foundation_models()
    except Exception as exc:
        return CheckResult(
            "Bedrock control plane",
            "warn",
            f"ListFoundationModels failed in {region}: {_describe(exc)}",
            remediation=(
                "This is the control plane only and does not prove that invoking a"
                " model is denied. Re-run with --invoke for a definitive answer."
            ),
            iam_actions=("bedrock:ListFoundationModels",),
        )
    count = len(response.get("modelSummaries", []))
    return CheckResult(
        "Bedrock control plane", "pass", f"{count} foundation models visible in {region}."
    )


def check_inference_profiles(session: Any, config: HaruConfig) -> Iterator[CheckResult]:
    """Check that each configured model's inference profile exists in-region."""
    if config.models is None:
        return
    for name in list_models(config):
        entry = get_model_config(config, name)
        model_id = resolve_model_id(entry.model_id)
        if model_id.startswith("arn:"):
            yield _skipped(f"Model {name}", "configured as an explicit ARN")
            continue
        try:
            session.client("bedrock", region_name=entry.region).get_inference_profile(
                inferenceProfileIdentifier=model_id
            )
        except Exception as exc:
            yield CheckResult(
                f"Model {name}",
                "warn",
                f"{model_id} not confirmed in {entry.region}: {_describe(exc)}",
                remediation=(
                    "Check the model id, the region, and that model access is enabled"
                    " for it in the Bedrock console."
                ),
                iam_actions=("bedrock:GetInferenceProfile",),
            )
            continue
        yield CheckResult(f"Model {name}", "pass", f"{model_id} available in {entry.region}.")


def check_invoke(config: HaruConfig, session: Any) -> CheckResult:
    """Definitive check: make a real, minimal Bedrock call (billable)."""
    from haru.models.errors import BedrockContext, translate_bedrock_error

    entry = get_model_config(config)
    model_id = resolve_model_id(entry.model_id)
    account_id, _, role_name, _ = effective_identity(config.auth.sso)
    context = BedrockContext(
        model_id=model_id,
        region=entry.region,
        account_id=account_id,
        role_name=role_name,
        guardrail_id=config.guardrails.guardrail_id if config.guardrails else None,
    )
    try:
        session.client("bedrock-runtime", region_name=entry.region).converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            inferenceConfig={"maxTokens": 1},
        )
    except Exception as exc:
        translated = translate_bedrock_error(exc, context)
        return CheckResult(
            "Bedrock invoke (live)",
            "fail",
            f"Converse failed for {context.describe()}.",
            remediation=str(translated) if translated is not None else _describe(exc),
            iam_actions=INVOKE_ACTIONS,
        )
    return CheckResult(
        "Bedrock invoke (live)", "pass", f"Converse succeeded for {context.describe()}."
    )


def _build_session(  # noqa: PLR0913, PLR0917 - internal seam plumbing
    config: HaruConfig,
    account_id: str | None,
    role_name: str | None,
    cache_dir: Path | None,
    sso_client: Any | None,
    session_factory: Callable[..., Any] | None,
) -> Any:
    """Obtain a boto3 session for the effective identity."""
    if session_factory is not None:
        return session_factory(config.auth, account_id, role_name)
    token = ensure_token(config.auth, cache_dir=cache_dir)
    return session_for_role(
        config.auth, token, str(account_id), str(role_name), sso_client=sso_client
    )


def _effective_region(config: HaruConfig) -> str:
    """The region Bedrock calls land in for the default model."""
    if config.models is None:
        return config.auth.bedrock_region
    return get_model_config(config).region


def _skipped(name: str, reason: str) -> CheckResult:
    """A check that was not run."""
    return CheckResult(name, "skip", f"Skipped: {reason}.")


def _describe(exc: Exception) -> str:
    """Render an exception as an error code and message where available."""
    from haru.models.errors import error_code

    code = error_code(exc)
    return f"{code}: {exc}" if code else str(exc)
