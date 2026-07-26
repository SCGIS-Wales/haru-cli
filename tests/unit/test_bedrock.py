"""Tests for the Strands BedrockModel factory."""

from typing import Any

import pytest
from pydantic import ValidationError

from haru.config.schema import HaruConfig, ModelConfig, SamplingConfig
from haru.errors import ConfigError
from haru.models.bedrock import (
    build_model,
    get_model_config,
    list_models,
    merge_sampling,
    resolve_model_id,
    sampling_overrides,
)

BASE = {
    "app": {"name": "haru"},
    "auth": {
        "sso": {
            "start_url": "https://example.awsapps.com/start",
            "sso_region": "us-east-1",
            "account_id_env": "HARU_AWS_ACCOUNT_ID",
            "role_name": "HaruBedrockInvoke",
        },
        "bedrock_region": "us-east-1",
    },
}


def make_config(**models: dict[str, Any]) -> HaruConfig:
    """Build a HaruConfig with the given model entries (first one is default)."""
    payload = dict(BASE)
    if models:
        payload["models"] = {"default_model": next(iter(models)), "models": models}
    return HaruConfig.model_validate(payload)


def make_model_config(**overrides: Any) -> ModelConfig:
    """Build a single ModelConfig for tests."""
    values: dict[str, Any] = {
        "model_id": "anthropic.claude-sonnet-5",
        "region": "us-east-1",
        "max_tokens": 4096,
        "temperature": 0.3,
        **overrides,
    }
    return ModelConfig.model_validate(values)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("anthropic.claude-sonnet-5", "us.anthropic.claude-sonnet-5"),
        ("us.anthropic.claude-opus-5", "us.anthropic.claude-opus-5"),
        ("eu.anthropic.claude-opus-5", "eu.anthropic.claude-opus-5"),
        ("au.anthropic.claude-opus-5", "au.anthropic.claude-opus-5"),
        ("global.anthropic.claude-opus-5", "global.anthropic.claude-opus-5"),
        (
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/x",
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/x",
        ),
    ],
)
def test_resolve_model_id_residency(configured: str, expected: str) -> None:
    """Bare identifiers get the us. prefix; explicit prefixes and ARNs pass through."""
    assert resolve_model_id(configured) == expected


def test_build_model_kwargs(mocker: Any) -> None:
    """build_model constructs BedrockModel with the configured values."""
    bedrock_model = mocker.patch("strands.models.BedrockModel")
    session = mocker.Mock()

    build_model(make_model_config(), session)

    bedrock_model.assert_called_once_with(
        boto_session=session,
        region_name="us-east-1",
        model_id="us.anthropic.claude-sonnet-5",
        max_tokens=4096,
        streaming=True,
        strict_tools=False,
        temperature=0.3,
    )


def test_build_model_omits_unset_sampling(mocker: Any) -> None:
    """No sampling fields set means none are sent (Claude 5-series safe)."""
    bedrock_model = mocker.patch("strands.models.BedrockModel")

    build_model(make_model_config(temperature=None), mocker.Mock())

    kwargs = bedrock_model.call_args.kwargs
    for key in ("temperature", "top_p", "additional_request_fields"):
        assert key not in kwargs


def test_build_model_top_k_and_seed_via_request_fields(mocker: Any) -> None:
    """top_k and seed travel via Converse additionalModelRequestFields."""
    bedrock_model = mocker.patch("strands.models.BedrockModel")

    build_model(make_model_config(temperature=0.2, top_p=0.9, top_k=50, seed=42), mocker.Mock())

    kwargs = bedrock_model.call_args.kwargs
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
    assert kwargs["additional_request_fields"] == {"top_k": 50, "seed": 42}


def test_sampling_override_beats_model_entry(mocker: Any) -> None:
    """A per-field override wins; unset override fields keep model values."""
    bedrock_model = mocker.patch("strands.models.BedrockModel")
    override = SamplingConfig(temperature=0.0, top_k=1)

    build_model(make_model_config(temperature=0.7, top_p=0.9), mocker.Mock(), sampling=override)

    kwargs = bedrock_model.call_args.kwargs
    assert kwargs["temperature"] == 0.0
    assert kwargs["top_p"] == 0.9
    assert kwargs["additional_request_fields"] == {"top_k": 1}


def test_merge_sampling_per_field() -> None:
    """merge_sampling composes per-field with override precedence."""
    base = SamplingConfig(temperature=0.5, top_k=10)
    override = SamplingConfig(top_k=99, seed=7)

    merged = merge_sampling(base, override)

    assert merged == SamplingConfig(temperature=0.5, top_k=99, seed=7)
    assert merge_sampling(None, override) == override
    assert merge_sampling(base, None) == base
    assert merge_sampling(None, None) is None


def test_sampling_overrides_builder() -> None:
    """sampling_overrides returns None when nothing is set."""
    assert sampling_overrides() is None
    assert sampling_overrides(top_k=5) == SamplingConfig(top_k=5)


def test_sampling_config_bounds() -> None:
    """Sampling fields validate their ranges."""
    with pytest.raises(ValidationError):
        SamplingConfig(temperature=1.5)
    with pytest.raises(ValidationError):
        SamplingConfig(top_k=0)


def test_build_model_streaming_defaults_on(mocker: Any) -> None:
    """Streaming is on unless the entry disables it explicitly."""
    bedrock_model = mocker.patch("strands.models.BedrockModel")
    build_model(make_model_config(), mocker.Mock())
    assert bedrock_model.call_args.kwargs["streaming"] is True

    bedrock_model.reset_mock()
    build_model(make_model_config(streaming=False), mocker.Mock())
    assert bedrock_model.call_args.kwargs["streaming"] is False


def test_build_model_for_every_configured_entry(mocker: Any) -> None:
    """Every entry in a config's model catalogue builds a model."""
    bedrock_model = mocker.patch("strands.models.BedrockModel")
    config = make_config(
        fast={"model_id": "anthropic.a", "region": "us-east-1", "max_tokens": 1, "temperature": 0},
        deep={
            "model_id": "eu.anthropic.b",
            "region": "eu-west-1",
            "max_tokens": 2,
            "temperature": 1,
        },
    )
    assert config.models is not None
    for entry in config.models.models.values():
        build_model(entry, mocker.Mock())
    assert bedrock_model.call_count == 2


def test_get_model_config_default_and_named() -> None:
    """Lookup uses the configured default when no name is given."""
    config = make_config(
        fast={"model_id": "anthropic.a", "region": "us-east-1", "max_tokens": 1, "temperature": 0},
        deep={"model_id": "anthropic.b", "region": "us-east-1", "max_tokens": 2, "temperature": 1},
    )
    assert get_model_config(config).model_id == "anthropic.a"
    assert get_model_config(config, "deep").model_id == "anthropic.b"


def test_get_model_config_unknown_key() -> None:
    """Unknown model keys raise ConfigError listing what is configured."""
    config = make_config(
        fast={"model_id": "anthropic.a", "region": "us-east-1", "max_tokens": 1, "temperature": 0},
    )
    with pytest.raises(ConfigError, match=r"ghost.*fast"):
        get_model_config(config, "ghost")


def test_no_models_section_raises() -> None:
    """A config without a models section raises ConfigError."""
    config = make_config()
    with pytest.raises(ConfigError, match="No models configured"):
        get_model_config(config)
    with pytest.raises(ConfigError, match="No models configured"):
        list_models(config)


def test_list_models_sorted() -> None:
    """list_models returns the catalogue keys sorted."""
    config = make_config(
        zeta={"model_id": "anthropic.z", "region": "us-east-1", "max_tokens": 1, "temperature": 0},
        alpha={"model_id": "anthropic.a", "region": "us-east-1", "max_tokens": 1, "temperature": 0},
    )
    assert list_models(config) == ["alpha", "zeta"]
