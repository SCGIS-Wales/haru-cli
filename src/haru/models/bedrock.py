"""Build Strands BedrockModel instances from typed configuration.

Model transport stays entirely inside Strands; this module only maps validated
configuration onto ``BedrockModel`` construction. Bare model identifiers get
the ``us.`` geographic inference-profile prefix so data residency defaults to
the US; any explicitly configured prefix (including ``global.``) is respected
as written, since configuration is the approval surface.
"""

from typing import Any

import boto3
from strands.models import BedrockModel

from haru.config.schema import (
    GuardrailsConfig,
    HaruConfig,
    ModelConfig,
    ModelsConfig,
    SamplingConfig,
)
from haru.errors import ConfigError
from haru.observability.guardrails import apply_guardrail

_GEO_PREFIXES = ("us.", "eu.", "ap.", "apac.", "au.", "jp.", "global.")
_DEFAULT_GEO_PREFIX = "us."


def resolve_model_id(model_id: str) -> str:
    """Return ``model_id`` with the default ``us.`` prefix applied when bare.

    Identifiers that already carry a geographic prefix, or that are full ARNs,
    are returned unchanged.
    """
    if model_id.startswith("arn:") or model_id.startswith(_GEO_PREFIXES):
        return model_id
    return f"{_DEFAULT_GEO_PREFIX}{model_id}"


def build_model(
    model_cfg: ModelConfig,
    session: boto3.Session,
    guardrails: GuardrailsConfig | None = None,
    sampling: SamplingConfig | None = None,
) -> BedrockModel:
    """Build a Strands BedrockModel from ``model_cfg`` and a boto3 session.

    ``sampling`` overrides the model entry's own sampling fields per-field.
    Unset sampling fields are omitted from the request entirely; ``top_k``
    and ``seed`` travel via Converse ``additionalModelRequestFields``. When
    ``guardrails`` is enabled, the Bedrock Guardrails parameters are attached.
    """
    return BedrockModel(
        boto_session=session,
        region_name=model_cfg.region,
        model_id=resolve_model_id(model_cfg.model_id),
        max_tokens=model_cfg.max_tokens,
        streaming=model_cfg.streaming,
        # Rich tool schemas can fail ConverseStream's strict validation.
        strict_tools=False,
        **_sampling_kwargs(effective_sampling(model_cfg, sampling)),
        **apply_guardrail(model_cfg, guardrails),
    )


def effective_sampling(model_cfg: ModelConfig, override: SamplingConfig | None) -> SamplingConfig:
    """Merge ``override`` over the model entry's sampling fields."""
    base = SamplingConfig(
        temperature=model_cfg.temperature,
        top_p=model_cfg.top_p,
        top_k=model_cfg.top_k,
        seed=model_cfg.seed,
    )
    merged = merge_sampling(base, override)
    return merged if merged is not None else base


def merge_sampling(
    base: SamplingConfig | None, override: SamplingConfig | None
) -> SamplingConfig | None:
    """Merge two sampling configs per-field; ``override`` wins where set."""
    if base is None:
        return override
    if override is None:
        return base
    return SamplingConfig(
        temperature=override.temperature if override.temperature is not None else base.temperature,
        top_p=override.top_p if override.top_p is not None else base.top_p,
        top_k=override.top_k if override.top_k is not None else base.top_k,
        seed=override.seed if override.seed is not None else base.seed,
    )


def sampling_overrides(
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    seed: int | None = None,
) -> SamplingConfig | None:
    """Build a SamplingConfig from CLI options; None when nothing is set."""
    if temperature is None and top_p is None and top_k is None and seed is None:
        return None
    return SamplingConfig(temperature=temperature, top_p=top_p, top_k=top_k, seed=seed)


def _sampling_kwargs(sampling: SamplingConfig) -> dict[str, Any]:
    """Map effective sampling onto BedrockModel kwargs (set fields only)."""
    kwargs: dict[str, Any] = {}
    if sampling.temperature is not None:
        kwargs["temperature"] = sampling.temperature
    if sampling.top_p is not None:
        kwargs["top_p"] = sampling.top_p
    request_fields: dict[str, Any] = {}
    if sampling.top_k is not None:
        request_fields["top_k"] = sampling.top_k
    if sampling.seed is not None:
        request_fields["seed"] = sampling.seed
    if request_fields:
        kwargs["additional_request_fields"] = request_fields
    return kwargs


def get_model_config(config: HaruConfig, name: str | None = None) -> ModelConfig:
    """Return the model entry for ``name`` (or the configured default).

    Raises ConfigError when no models are configured or the key is unknown.
    """
    models = _require_models(config)
    key = name if name is not None else models.default_model
    entry = models.models.get(key)
    if entry is None:
        available = ", ".join(sorted(models.models))
        raise ConfigError(f"Unknown model {key!r}; configured models: {available}")
    return entry


def list_models(config: HaruConfig) -> list[str]:
    """Return the configured model keys, sorted."""
    return sorted(_require_models(config).models)


def _require_models(config: HaruConfig) -> ModelsConfig:
    if config.models is None:
        raise ConfigError("No models configured; add a models include to config/haru.yaml")
    return config.models
