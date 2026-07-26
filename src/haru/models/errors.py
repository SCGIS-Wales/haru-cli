"""Translate Bedrock runtime errors into actionable haru exceptions.

Strands owns the Bedrock wire, so failures surface as raw botocore exceptions
at the point haru consumes the stream. This module converts the ones users can
act on into haru errors that name the model, region, role, and the exact IAM
action required. Detection is duck-typed on the botocore error shape so this
module (and the streaming path that calls it) stays import-light.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from haru.errors import AuthError, AuthExpiredError, ConfigError, HaruError

# Converse is authorized by bedrock:InvokeModel and ConverseStream by
# bedrock:InvokeModelWithResponseStream. There is no bedrock:Converse action.
INVOKE_ACTIONS = ("bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream")
GUARDRAIL_ACTION = "bedrock:ApplyGuardrail"

_KIRO_NOTE = (
    "Note: Kiro or Amazon Q working is not evidence this will work. Those call the"
    " managed Amazon Q service with your SSO token; haru calls Bedrock in your own"
    " account with your assumed role, which needs the actions above."
)


@dataclass(frozen=True)
class BedrockContext:
    """What haru was trying to do when Bedrock refused, for error messages."""

    model_id: str
    region: str
    account_id: str | None = None
    role_name: str | None = None
    guardrail_id: str | None = None

    def describe(self) -> str:
        """Render the attempted call as a single human-readable line."""
        where = f"model {self.model_id} in {self.region}"
        if self.account_id is not None:
            where += f" (account {self.account_id}"
            where += f", role {self.role_name})" if self.role_name else ")"
        return where


def error_code(exc: Exception) -> str | None:
    """Return the botocore error code for ``exc``, or None if it has none."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) and code else None


def translate_bedrock_error(  # noqa: PLR0911 - one branch per AWS error code
    exc: Exception, ctx: BedrockContext | None
) -> HaruError | None:
    """Convert a Bedrock failure into a haru error, or None if unrecognised.

    Returning None leaves genuine bugs with their original traceback.
    """
    code = error_code(exc)
    if code is None:
        return None
    where = ctx.describe() if ctx is not None else "the configured model"
    detail = _message_of(exc)

    if code in {"AccessDeniedException", "AccessDenied"}:
        return AuthError(_access_denied_message(where, detail, ctx))
    if code in {"ResourceNotFoundException", "ModelNotReadyException"}:
        return ConfigError(
            f"Bedrock could not find or serve {where}: {detail}."
            " Check the model id and region, and that model access is enabled"
            " for it in the Bedrock console. Run 'haru doctor' to verify."
        )
    if code == "ValidationException":
        return ConfigError(
            f"Bedrock rejected the request for {where}: {detail}."
            " If this mentions sampling parameters, note that Claude 5-series and"
            " Opus 4.7+ models reject non-default temperature/top_p/top_k."
        )
    if code in {"ThrottlingException", "TooManyRequestsException"}:
        return HaruError(
            f"Bedrock throttled the request for {where}. Retry, or ask for a"
            " quota increase for this model and region."
        )
    if code == "ServiceQuotaExceededException":
        return HaruError(f"Bedrock quota exceeded for {where}: {detail}.")
    if code in {
        "ExpiredTokenException",
        "UnrecognizedClientException",
        "InvalidSignatureException",
    }:
        return AuthExpiredError("AWS credentials are no longer valid; run 'haru login'.")
    return None


def _access_denied_message(where: str, detail: str, ctx: BedrockContext | None) -> str:
    """Build the remediation text for an AccessDenied on the invocation path."""
    actions = list(INVOKE_ACTIONS)
    if ctx is not None and ctx.guardrail_id:
        actions.append(GUARDRAIL_ACTION)
    target = (
        f"permission set {ctx.role_name} in account {ctx.account_id}"
        if ctx is not None and ctx.role_name and ctx.account_id
        else "the role you signed in with"
    )
    return (
        f"AWS denied access to {where}.\n"
        f"AWS said: {detail}\n"
        f"Fix: ask your AWS admin to grant {', '.join(actions)} to {target},"
        " on both the inference-profile ARN and the underlying foundation-model"
        " ARNs in every region the profile routes to."
        " See docs/troubleshooting.md for the full policy.\n"
        f"{_KIRO_NOTE}\n"
        "Run 'haru doctor --all-roles' to find an account and role that can."
    )


def _message_of(exc: Exception) -> str:
    """Extract botocore's own error message, falling back to str(exc)."""
    response: Any = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            message = error.get("Message")
            if isinstance(message, str) and message:
                return message
    return str(exc)
