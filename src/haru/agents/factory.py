"""Strands agent factories built from typed configuration.

Factories return fully-wired Strands agents: model, steering prompt, built-in
tools, and MCP tools. Multi-agent orchestration composes these in
``haru.agents.orchestration``.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import boto3
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from haru.config.schema import HaruConfig
from haru.errors import ConfigError
from haru.models.bedrock import build_model, get_model_config
from haru.steering.prompts import DEFAULT_PROMPTS_ROOT, load_prompts, resolve_prompt
from haru.tools.mcp import collect_tools


def build_agent(
    config: HaruConfig,
    agent_name: str | None,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
) -> Agent:
    """Build the named agent from configuration (default model when unnamed).

    Raises ConfigError for unknown agents, prompt references, or tools.
    """
    if agent_name is None:
        return Agent(model=build_model(get_model_config(config), session))
    model, system_prompt, tools = resolve_agent_parts(
        config, agent_name, session, prompts_root=prompts_root, mcp_clients=mcp_clients
    )
    return Agent(model=model, system_prompt=system_prompt, tools=tools, name=agent_name)


def resolve_agent_parts(
    config: HaruConfig,
    agent_name: str,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
) -> tuple[BedrockModel, str | None, list[Any]]:
    """Resolve an agent's model, steering prompt, and tool list from config."""
    if config.agents is None or agent_name not in config.agents.agents:
        available = ", ".join(sorted(config.agents.agents)) if config.agents else "none"
        raise ConfigError(f"Unknown agent {agent_name!r}; configured agents: {available}")
    agent_cfg = config.agents.agents[agent_name]
    model = build_model(get_model_config(config, agent_cfg.model), session)

    system_prompt: str | None = None
    if agent_cfg.system_prompt_ref is not None:
        root = prompts_root if prompts_root is not None else DEFAULT_PROMPTS_ROOT
        system_prompt = resolve_prompt(agent_cfg.system_prompt_ref, load_prompts(root))

    tools = collect_tools(agent_cfg, None, mcp_clients or {}, mcp_cfg=config.mcp)
    return model, system_prompt, tools
