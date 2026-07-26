"""Multi-agent orchestration: supervisor (agents-as-tools), swarm, and graph.

Each pattern is wired from the ``orchestration`` section of the agents
configuration; the agent loop, handoffs, and graph execution are delegated
entirely to Strands.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import boto3
from strands import Agent, tool
from strands.multiagent import GraphBuilder, Swarm
from strands.multiagent.graph import Graph
from strands.tools.mcp import MCPClient

from haru.agents.factory import build_agent, resolve_agent_parts
from haru.auth.session import build_boto3_session
from haru.config.schema import AgentsConfig, HaruConfig, OrchestrationConfig
from haru.errors import ConfigError

SUPERVISOR_AGENT_NAME = "supervisor"


def build_supervisor(
    config: HaruConfig,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
) -> Agent:
    """Build the supervisor agent with every other agent exposed as a tool."""
    agents_cfg = _require_agents(config)
    if SUPERVISOR_AGENT_NAME not in agents_cfg.agents:
        raise ConfigError(
            f"Supervisor orchestration requires an agent named {SUPERVISOR_AGENT_NAME!r}"
        )
    model, system_prompt, tools = resolve_agent_parts(
        config, SUPERVISOR_AGENT_NAME, session, prompts_root=prompts_root, mcp_clients=mcp_clients
    )
    worker_tools = [
        _agent_as_tool(
            name,
            build_agent(config, name, session, prompts_root=prompts_root, mcp_clients=mcp_clients),
        )
        for name in sorted(agents_cfg.agents)
        if name != SUPERVISOR_AGENT_NAME
    ]
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[*tools, *worker_tools],
        name=SUPERVISOR_AGENT_NAME,
    )


def build_swarm(
    config: HaruConfig,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
) -> Swarm:
    """Build a Swarm over the configured member agents."""
    orchestration = _require_orchestration(config)
    if orchestration.swarm is None:
        raise ConfigError("Swarm orchestration requested but no swarm section is configured")
    members = [
        build_agent(config, name, session, prompts_root=prompts_root, mcp_clients=mcp_clients)
        for name in orchestration.swarm.members
    ]
    return Swarm(
        members,
        max_handoffs=orchestration.swarm.max_handoffs,
        execution_timeout=float(orchestration.swarm.execution_timeout_seconds),
    )


def build_graph(
    config: HaruConfig,
    session: boto3.Session,
    *,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
) -> Graph:
    """Build a Graph from the configured nodes and edges via GraphBuilder."""
    orchestration = _require_orchestration(config)
    if orchestration.graph is None:
        raise ConfigError("Graph orchestration requested but no graph section is configured")
    builder = GraphBuilder()
    for node in orchestration.graph.nodes:
        builder.add_node(
            build_agent(
                config, node.agent, session, prompts_root=prompts_root, mcp_clients=mcp_clients
            ),
            node.id,
        )
    for edge in orchestration.graph.edges:
        builder.add_edge(edge.source, edge.target)
    return builder.build()


def run_orchestration(  # noqa: PLR0913 - three keyword-only injection points for tests/callers
    config: HaruConfig,
    prompt: str,
    pattern: str | None = None,
    *,
    session: boto3.Session | None = None,
    prompts_root: Path | None = None,
    mcp_clients: Mapping[str, MCPClient] | None = None,
) -> str:
    """Run ``prompt`` through the requested (or default) orchestration pattern."""
    orchestration = _require_agents(config).orchestration
    selected = pattern if pattern is not None else _default_pattern(orchestration)
    if session is None:
        session = build_boto3_session(config.auth)

    if selected == "supervisor":
        supervisor = build_supervisor(
            config, session, prompts_root=prompts_root, mcp_clients=mcp_clients
        )
        return str(supervisor(prompt))
    if selected == "swarm":
        swarm = build_swarm(config, session, prompts_root=prompts_root, mcp_clients=mcp_clients)
        return _final_text(swarm(prompt))
    if selected == "graph":
        graph = build_graph(config, session, prompts_root=prompts_root, mcp_clients=mcp_clients)
        return _final_text(graph(prompt))
    raise ConfigError(f"Unknown orchestration pattern {selected!r}")


def _default_pattern(orchestration: OrchestrationConfig | None) -> str:
    return orchestration.default_pattern if orchestration is not None else "supervisor"


def _agent_as_tool(name: str, agent: Agent) -> Any:
    """Expose ``agent`` as a tool the supervisor can delegate tasks to."""

    @tool(name=name, description=f"Delegate a task to the {name} specialist agent.")
    def delegate(task: str) -> str:
        """Send a task to the specialist agent and return its answer."""
        return str(agent(task))

    return delegate


def _final_text(result: Any) -> str:
    """Extract the final node's text from a swarm or graph result."""
    order = getattr(result, "node_history", None) or getattr(result, "execution_order", None)
    if order:
        node_id = getattr(order[-1], "node_id", order[-1])
        node_result = result.results.get(node_id)
        if node_result is not None:
            return str(node_result.result)
    return "\n".join(str(node.result) for node in result.results.values())


def _require_agents(config: HaruConfig) -> AgentsConfig:
    if config.agents is None:
        raise ConfigError("Orchestration requires an agents section in configuration")
    return config.agents


def _require_orchestration(config: HaruConfig) -> OrchestrationConfig:
    orchestration = _require_agents(config).orchestration
    if orchestration is None:
        raise ConfigError("No orchestration section configured")
    return orchestration
