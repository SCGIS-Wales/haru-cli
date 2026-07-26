"""Load and validate layered YAML configuration.

The loader enforces three invariants before any value reaches typed models:

1. No inline secrets: keys named ``secret``, ``password``, or ``token`` must
   hold environment references (``${env:VAR}`` or ``${VAR}``), never values.
2. Environment references resolve at load time; a missing variable fails fast.
3. Every document validates into the frozen schema, rejecting unknown keys.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from haru.config.schema import (
    AgentsConfig,
    GuardrailsFile,
    HaruConfig,
    LoggingFile,
    MCPConfig,
    ModelsConfig,
)
from haru.errors import ConfigError

DEFAULT_CONFIG_PATH = Path("config") / "haru.yaml"

_ENV_PATTERN = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_REFERENCE = re.compile(r"^\$\{(?:env:)?[A-Za-z_][A-Za-z0-9_]*\}$")
_SECRET_KEY_NAMES = frozenset({"secret", "password", "token"})


def resolve_env(value: str) -> str:
    """Expand ``${env:VAR}`` and ``${VAR}`` references from the environment.

    Raises ConfigError if a referenced variable is not set.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        resolved = os.environ.get(name)
        if resolved is None:
            raise ConfigError(f"Environment variable {name!r} referenced in configuration is unset")
        return resolved

    return _ENV_PATTERN.sub(_replace, value)


def load_config(path: Path | None = None, *, with_includes: bool = True) -> HaruConfig:
    """Load the full configuration from ``path`` (default ``config/haru.yaml``).

    Parses the base document, then resolves every declared include relative to
    the base file's directory. Pass ``with_includes=False`` to load only the
    base document (commands that need just the ``app``/``auth`` sections).
    Raises ConfigError on any missing file, malformed YAML, inline secret,
    unset environment reference, or schema violation.
    """
    config_path = path if path is not None else DEFAULT_CONFIG_PATH
    base = _validate(HaruConfig, _load_yaml(config_path), config_path)
    if not with_includes:
        return base
    config = load_includes(base, config_path.parent)
    _check_cross_references(config)
    return config


def load_includes(base: HaruConfig, root: Path) -> HaruConfig:
    """Return ``base`` with every declared include file loaded and validated.

    Include paths are resolved relative to ``root``. Raises ConfigError with
    the offending path when an include is missing or invalid.
    """
    includes = base.includes
    if includes is None:
        return base

    updates: dict[str, Any] = {}
    if includes.models is not None:
        source = root / includes.models
        updates["models"] = _validate(ModelsConfig, _load_yaml(source), source)
    if includes.agents is not None:
        source = root / includes.agents
        updates["agents"] = _validate(AgentsConfig, _load_yaml(source), source)
    if includes.mcp is not None:
        source = root / includes.mcp
        updates["mcp"] = _validate(MCPConfig, _load_yaml(source), source)
    if includes.guardrails is not None:
        source = root / includes.guardrails
        updates["guardrails"] = _validate(GuardrailsFile, _load_yaml(source), source).guardrails
    if includes.logging is not None:
        source = root / includes.logging
        logging_file = _validate(LoggingFile, _load_yaml(source), source)
        updates["logging"] = logging_file.logging
        updates["observability"] = logging_file.observability
    return base.model_copy(update=updates)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping, reject inline secrets, and resolve env references."""
    if not path.is_file():
        raise ConfigError(f"Missing configuration file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Top-level YAML in {path} must be a mapping")
    _reject_inline_secrets(data, source=path)
    resolved: dict[str, Any] = _resolve_tree(data)
    return resolved


def _resolve_tree(node: Any) -> Any:
    """Recursively expand environment references in every string value."""
    if isinstance(node, str):
        return resolve_env(node)
    if isinstance(node, dict):
        return {key: _resolve_tree(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_tree(item) for item in node]
    return node


def _reject_inline_secrets(node: Any, *, source: Path, key_path: str = "") -> None:
    """Fail if a secret-named key holds anything but an environment reference."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{key_path}.{key}" if key_path else str(key)
            if str(key).lower() in _SECRET_KEY_NAMES and not (
                isinstance(value, str) and _ENV_REFERENCE.match(value)
            ):
                raise ConfigError(
                    f"Inline secret at {child!r} in {source}: use an environment"
                    " reference such as ${env:VAR} instead"
                )
            _reject_inline_secrets(value, source=source, key_path=child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_inline_secrets(item, source=source, key_path=f"{key_path}[{index}]")


def _validate[ModelT: BaseModel](model: type[ModelT], data: dict[str, Any], source: Path) -> ModelT:
    """Validate ``data`` into ``model``, converting pydantic errors to ConfigError."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {source}: {exc}") from exc


def _check_cross_references(config: HaruConfig) -> None:
    """Validate references that span include files (agents -> models)."""
    if config.agents is None or config.models is None:
        return
    for name, agent in config.agents.agents.items():
        if agent.model not in config.models.models:
            raise ConfigError(f"Agent {name!r} references unknown model {agent.model!r}")
