"""Strands agent factories built from typed configuration.

This module currently builds single agents from the model catalogue; system
prompt resolution, tool wiring, and multi-agent orchestration are layered on
in later chunks without changing the factory surface.
"""

import boto3
from strands import Agent

from haru.config.schema import HaruConfig
from haru.errors import ConfigError
from haru.models.bedrock import build_model, get_model_config


def build_agent(config: HaruConfig, agent_name: str | None, session: boto3.Session) -> Agent:
    """Build the named agent from configuration (default model when unnamed).

    Raises ConfigError when ``agent_name`` is not a configured agent.
    """
    if agent_name is None:
        return Agent(model=build_model(get_model_config(config), session))
    if config.agents is None or agent_name not in config.agents.agents:
        available = ", ".join(sorted(config.agents.agents)) if config.agents else "none"
        raise ConfigError(f"Unknown agent {agent_name!r}; configured agents: {available}")
    agent_cfg = config.agents.agents[agent_name]
    model_cfg = get_model_config(config, agent_cfg.model)
    return Agent(model=build_model(model_cfg, session))
