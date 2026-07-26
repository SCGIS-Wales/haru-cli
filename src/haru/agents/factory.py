"""Strands agent factories built from typed configuration.

This module builds single agents from the model catalogue and resolves their
steering prompts; tool wiring and multi-agent orchestration are layered on in
later chunks without changing the factory surface.
"""

from pathlib import Path

import boto3
from strands import Agent

from haru.config.schema import HaruConfig
from haru.errors import ConfigError
from haru.models.bedrock import build_model, get_model_config
from haru.steering.prompts import DEFAULT_PROMPTS_ROOT, load_prompts, resolve_prompt


def build_agent(
    config: HaruConfig,
    agent_name: str | None,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
) -> Agent:
    """Build the named agent from configuration (default model when unnamed).

    Steering prompts are loaded from ``prompts_root`` (default
    ``config/prompts``) when the agent declares a ``system_prompt_ref``.
    Raises ConfigError for unknown agents or prompt references.
    """
    if agent_name is None:
        return Agent(model=build_model(get_model_config(config), session))
    if config.agents is None or agent_name not in config.agents.agents:
        available = ", ".join(sorted(config.agents.agents)) if config.agents else "none"
        raise ConfigError(f"Unknown agent {agent_name!r}; configured agents: {available}")
    agent_cfg = config.agents.agents[agent_name]
    model_cfg = get_model_config(config, agent_cfg.model)

    system_prompt: str | None = None
    if agent_cfg.system_prompt_ref is not None:
        root = prompts_root if prompts_root is not None else DEFAULT_PROMPTS_ROOT
        system_prompt = resolve_prompt(agent_cfg.system_prompt_ref, load_prompts(root))

    return Agent(model=build_model(model_cfg, session), system_prompt=system_prompt)
