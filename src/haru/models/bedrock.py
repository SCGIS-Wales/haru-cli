"""Build Strands BedrockModel instances from typed configuration.

Model transport stays entirely inside Strands; this module only maps validated
configuration onto ``BedrockModel`` construction. Bare model identifiers get
the ``us.`` geographic inference-profile prefix so data residency defaults to
the US; any explicitly configured prefix (including ``global.``) is respected
as written, since configuration is the approval surface.
"""

import boto3
from strands.models import BedrockModel

from haru.config.schema import HaruConfig, ModelConfig, ModelsConfig
from haru.errors import ConfigError

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


def build_model(model_cfg: ModelConfig, session: boto3.Session) -> BedrockModel:
    """Build a Strands BedrockModel from ``model_cfg`` and a boto3 session."""
    return BedrockModel(
        boto_session=session,
        region_name=model_cfg.region,
        model_id=resolve_model_id(model_cfg.model_id),
        max_tokens=model_cfg.max_tokens,
        temperature=model_cfg.temperature,
        streaming=model_cfg.streaming,
        # Rich tool schemas can fail ConverseStream's strict validation.
        strict_tools=False,
    )


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
