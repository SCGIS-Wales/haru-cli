"""Tests for translating Bedrock runtime errors into haru errors."""

import pytest

from haru.errors import AuthError, AuthExpiredError, ConfigError, HaruError
from haru.models.errors import BedrockContext, error_code, translate_bedrock_error

CONTEXT = BedrockContext(
    model_id="us.anthropic.claude-sonnet-5",
    region="us-east-1",
    account_id="881490127383",
    role_name="TRPAmazonQUsers",
)


def aws_error(code: str, message: str = "denied") -> Exception:
    """Build an exception shaped like a botocore ClientError."""
    exc = Exception(f"{code}: {message}")
    exc.response = {"Error": {"Code": code, "Message": message}}  # type: ignore[attr-defined]
    return exc


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("AccessDeniedException", AuthError),
        ("ResourceNotFoundException", ConfigError),
        ("ValidationException", ConfigError),
        ("ThrottlingException", HaruError),
        ("ServiceQuotaExceededException", HaruError),
        ("ExpiredTokenException", AuthExpiredError),
    ],
)
def test_error_codes_map_to_types(code: str, expected: type[HaruError]) -> None:
    """Each recognised AWS error code maps to the right haru error type."""
    translated = translate_bedrock_error(aws_error(code), CONTEXT)
    assert isinstance(translated, expected)


def test_access_denied_names_actions_model_and_role() -> None:
    """An AccessDenied message carries everything an admin needs."""
    translated = translate_bedrock_error(aws_error("AccessDeniedException"), CONTEXT)
    assert translated is not None
    message = str(translated)
    assert "us.anthropic.claude-sonnet-5" in message
    assert "us-east-1" in message
    assert "TRPAmazonQUsers" in message
    assert "881490127383" in message
    assert "bedrock:InvokeModelWithResponseStream" in message
    assert "bedrock:InvokeModel" in message
    assert "bedrock:Converse" not in message
    assert "Kiro" in message


def test_access_denied_mentions_guardrail_action_when_configured() -> None:
    """A configured guardrail adds bedrock:ApplyGuardrail to the remediation."""
    context = BedrockContext(
        model_id="us.anthropic.claude-sonnet-5", region="us-east-1", guardrail_id="gr-1"
    )
    translated = translate_bedrock_error(aws_error("AccessDeniedException"), context)
    assert translated is not None
    assert "bedrock:ApplyGuardrail" in str(translated)


def test_unrecognised_error_returns_none() -> None:
    """Non-AWS exceptions are left alone so real bugs keep their traceback."""
    assert translate_bedrock_error(ValueError("boom"), CONTEXT) is None
    assert translate_bedrock_error(aws_error("SomeNewException"), CONTEXT) is None


def test_translation_without_context() -> None:
    """Translation still works when no context is available."""
    translated = translate_bedrock_error(aws_error("AccessDeniedException"), None)
    assert translated is not None
    assert "the configured model" in str(translated)


def test_error_code_extraction() -> None:
    """error_code reads the botocore shape and tolerates anything else."""
    assert error_code(aws_error("Throttling")) == "Throttling"
    assert error_code(ValueError("no response")) is None
    bad = Exception("x")
    bad.response = {"Error": "not-a-dict"}  # type: ignore[attr-defined]
    assert error_code(bad) is None
