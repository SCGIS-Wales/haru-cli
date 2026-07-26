"""Tests for the guardrail-to-BedrockModel kwarg mapping."""

from typing import Any

import pytest

from haru.config.schema import GuardrailsConfig, ModelConfig
from haru.errors import ConfigError
from haru.models.bedrock import build_model
from haru.observability.guardrails import apply_guardrail

MODEL_CFG = ModelConfig.model_validate(
    {"model_id": "anthropic.a", "region": "us-east-1", "max_tokens": 1024, "temperature": 0.2}
)


def make_guardrails(**overrides: Any) -> GuardrailsConfig:
    """Build a GuardrailsConfig with an id plus overrides."""
    return GuardrailsConfig.model_validate({"guardrail_id": "gr-123", **overrides})


def test_mapping_to_bedrock_kwargs() -> None:
    """Guardrail settings map onto the BedrockModel guardrail kwargs."""
    kwargs = apply_guardrail(MODEL_CFG, make_guardrails(guardrail_version="7", trace="disabled"))
    assert kwargs == {
        "guardrail_id": "gr-123",
        "guardrail_version": "7",
        "guardrail_trace": "disabled",
        "guardrail_redact_input": True,
        "guardrail_redact_input_message": "[redacted]",
        "guardrail_redact_output": False,
    }


def test_redaction_defaults_enforced() -> None:
    """Input redaction defaults on with the standard message."""
    kwargs = apply_guardrail(MODEL_CFG, make_guardrails())
    assert kwargs["guardrail_redact_input"] is True
    assert kwargs["guardrail_redact_input_message"] == "[redacted]"
    assert kwargs["guardrail_version"] == "DRAFT"
    assert kwargs["guardrail_trace"] == "enabled"


def test_disabled_guardrails_map_to_nothing() -> None:
    """Explicitly disabled guardrails contribute no kwargs."""
    assert apply_guardrail(MODEL_CFG, make_guardrails(enabled=False)) == {}


def test_absent_guardrails_map_to_nothing() -> None:
    """No guardrail section contributes no kwargs."""
    assert apply_guardrail(MODEL_CFG, None) == {}


def test_enabled_without_id_fails_closed() -> None:
    """Enabled guardrails without an id raise ConfigError naming the model."""
    with pytest.raises(ConfigError, match=r"guardrail_id.*anthropic\.a"):
        apply_guardrail(MODEL_CFG, GuardrailsConfig())


def test_build_model_attaches_guardrail_kwargs(mocker: Any) -> None:
    """build_model forwards guardrail kwargs into BedrockModel."""
    bedrock_model = mocker.patch("haru.models.bedrock.BedrockModel")

    build_model(MODEL_CFG, mocker.Mock(), guardrails=make_guardrails())

    kwargs = bedrock_model.call_args.kwargs
    assert kwargs["guardrail_id"] == "gr-123"
    assert kwargs["guardrail_redact_input"] is True
    assert kwargs["streaming"] is True


def test_build_model_without_guardrails_adds_nothing(mocker: Any) -> None:
    """build_model without guardrails passes no guardrail kwargs."""
    bedrock_model = mocker.patch("haru.models.bedrock.BedrockModel")

    build_model(MODEL_CFG, mocker.Mock())

    assert not any(key.startswith("guardrail") for key in bedrock_model.call_args.kwargs)
