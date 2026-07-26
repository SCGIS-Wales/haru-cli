"""Map guardrail configuration onto Strands BedrockModel keyword arguments.

Guardrails fail closed: when enabled, a guardrail id is mandatory, and input
redaction defaults on. Disabling guardrails must be an explicit configuration
choice, never a fallback.
"""

from typing import Any

from haru.config.schema import GuardrailsConfig, ModelConfig
from haru.errors import ConfigError


def apply_guardrail(
    model_cfg: ModelConfig, guardrail_cfg: GuardrailsConfig | None
) -> dict[str, Any]:
    """Return the guardrail kwargs for BedrockModel construction.

    Returns an empty mapping when guardrails are absent or explicitly
    disabled. Raises ConfigError when guardrails are enabled without an id.
    """
    if guardrail_cfg is None or not guardrail_cfg.enabled:
        return {}
    if not guardrail_cfg.guardrail_id:
        raise ConfigError(
            f"Guardrails are enabled but no guardrail_id is configured"
            f" (model {model_cfg.model_id!r}); set guardrails.guardrail_id"
        )
    return {
        "guardrail_id": guardrail_cfg.guardrail_id,
        "guardrail_version": guardrail_cfg.guardrail_version,
        "guardrail_trace": guardrail_cfg.trace,
        "guardrail_redact_input": guardrail_cfg.redact_input,
        "guardrail_redact_input_message": guardrail_cfg.redact_input_message,
        "guardrail_redact_output": guardrail_cfg.redact_output,
    }
