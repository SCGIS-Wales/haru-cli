"""Strands agent factories built from typed configuration.

Factories return fully-wired Strands agents: model, steering prompt, built-in
tools, and MCP tools. Multi-agent orchestration composes these in
``haru.agents.orchestration``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from haru.config.schema import HaruConfig, SamplingConfig
from haru.errors import ConfigError
from haru.models.bedrock import build_model, get_model_config, merge_sampling
from haru.steering.prompts import DEFAULT_PROMPTS_ROOT, load_prompts, resolve_prompt
from haru.tools.mcp import collect_tools

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import boto3
    from strands import Agent
    from strands.models import BedrockModel
    from strands.session.session_manager import SessionManager
    from strands.tools.mcp import MCPClient


def build_agent(  # noqa: PLR0913 - keyword-only wiring points, all optional
    config: HaruConfig,
    agent_name: str | None,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
    session_manager: SessionManager | None = None,
    model_name: str | None = None,
    sampling: SamplingConfig | None = None,
) -> Agent:
    """Build the named agent from configuration (default model when unnamed).

    ``model_name`` overrides the model for the default (unnamed) agent only;
    named agents pin their own model. ``sampling`` overrides sampling fields
    (precedence: this override, then the agent's ``sampling`` block, then the
    model entry). Raises ConfigError for unknown agents, prompt references,
    tools, or an invalid override.
    """
    from strands import Agent

    if agent_name is None:
        model_cfg = get_model_config(config, model_name)
        model = build_model(model_cfg, session, guardrails=config.guardrails, sampling=sampling)
        return Agent(model=model, session_manager=session_manager)
    if model_name is not None:
        raise ConfigError(
            f"Agent {agent_name!r} pins its own model; model overrides apply"
            " to the default agent only"
        )
    model, system_prompt, tools = resolve_agent_parts(
        config,
        agent_name,
        session,
        prompts_root=prompts_root,
        mcp_clients=mcp_clients,
        sampling=sampling,
    )
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        name=agent_name,
        session_manager=session_manager,
    )


def resolve_agent_parts(  # noqa: PLR0913 - keyword-only wiring points, all optional
    config: HaruConfig,
    agent_name: str,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
    sampling: SamplingConfig | None = None,
) -> tuple[BedrockModel, str | None, list[Any]]:
    """Resolve an agent's model, steering prompt, and tool list from config."""
    if config.agents is None or agent_name not in config.agents.agents:
        available = ", ".join(sorted(config.agents.agents)) if config.agents else "none"
        raise ConfigError(f"Unknown agent {agent_name!r}; configured agents: {available}")
    agent_cfg = config.agents.agents[agent_name]
    model = build_model(
        get_model_config(config, agent_cfg.model),
        session,
        guardrails=config.guardrails,
        sampling=merge_sampling(agent_cfg.sampling, sampling),
    )

    system_prompt: str | None = None
    if agent_cfg.system_prompt_ref is not None:
        root = prompts_root if prompts_root is not None else DEFAULT_PROMPTS_ROOT
        system_prompt = resolve_prompt(agent_cfg.system_prompt_ref, load_prompts(root))

    tools = collect_tools(agent_cfg, None, mcp_clients or {}, mcp_cfg=config.mcp)
    return model, system_prompt, tools
