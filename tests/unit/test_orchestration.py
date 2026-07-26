"""Tests for supervisor, swarm, and graph orchestration wiring."""

from types import SimpleNamespace
from typing import Any

import pytest

from haru.agents.orchestration import (
    build_graph,
    build_supervisor,
    build_swarm,
    run_orchestration,
)
from haru.config.schema import HaruConfig
from haru.errors import ConfigError

MODEL = {"model_id": "anthropic.a", "region": "us-east-1", "max_tokens": 1024, "temperature": 0.2}

BASE: dict[str, Any] = {
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
    "models": {"default_model": "fast", "models": {"fast": MODEL}},
}

AGENTS: dict[str, Any] = {
    "agents": {
        "supervisor": {"model": "fast"},
        "researcher": {"model": "fast"},
        "writer": {"model": "fast"},
    },
    "orchestration": {
        "default_pattern": "supervisor",
        "swarm": {
            "members": ["researcher", "writer"],
            "max_handoffs": 4,
            "execution_timeout_seconds": 120,
        },
        "graph": {
            "nodes": [
                {"id": "research", "agent": "researcher"},
                {"id": "write", "agent": "writer"},
            ],
            "edges": [{"from": "research", "to": "write"}],
        },
    },
}


def make_config(agents: dict[str, Any] | None = None) -> HaruConfig:
    """Build a config with the standard three-agent orchestration setup."""
    payload = dict(BASE)
    payload["agents"] = agents if agents is not None else AGENTS
    return HaruConfig.model_validate(payload)


class FakeWorker:
    """A callable agent stand-in that answers with its name."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, task: str) -> str:
        return f"{self.name} handled: {task}"


def fake_build_agent(config: HaruConfig, name: str | None, session: Any, **_: Any) -> FakeWorker:
    """build_agent replacement returning a FakeWorker per agent name."""
    return FakeWorker(str(name))


def test_supervisor_exposes_workers_as_tools(mocker: Any) -> None:
    """The supervisor gets its own tools plus one delegate tool per worker."""
    agent_cls = mocker.patch("haru.agents.orchestration.Agent")
    mocker.patch(
        "haru.agents.orchestration.resolve_agent_parts",
        return_value=("sup-model", "sup-prompt", ["own-tool"]),
    )
    mocker.patch("haru.agents.orchestration.build_agent", side_effect=fake_build_agent)

    build_supervisor(make_config(), mocker.Mock())

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["model"] == "sup-model"
    assert kwargs["system_prompt"] == "sup-prompt"
    assert kwargs["name"] == "supervisor"
    tools = kwargs["tools"]
    assert tools[0] == "own-tool"
    delegate_names = [tool.tool_name for tool in tools[1:]]
    assert delegate_names == ["researcher", "writer"]


def test_supervisor_delegate_tool_invokes_worker(mocker: Any) -> None:
    """Calling a delegate tool runs the worker agent and returns its text."""
    agent_cls = mocker.patch("haru.agents.orchestration.Agent")
    mocker.patch("haru.agents.orchestration.resolve_agent_parts", return_value=("m", None, []))
    mocker.patch("haru.agents.orchestration.build_agent", side_effect=fake_build_agent)

    build_supervisor(make_config(), mocker.Mock())

    researcher_tool = agent_cls.call_args.kwargs["tools"][0]
    assert researcher_tool("find facts") == "researcher handled: find facts"


def test_supervisor_requires_supervisor_agent(mocker: Any) -> None:
    """Supervisor orchestration without a 'supervisor' agent fails."""
    agents = {"agents": {"writer": {"model": "fast"}}}
    with pytest.raises(ConfigError, match="supervisor"):
        build_supervisor(make_config(agents), mocker.Mock())


def test_swarm_respects_bounds(mocker: Any) -> None:
    """The swarm gets its members in order plus handoff and timeout bounds."""
    swarm_cls = mocker.patch("haru.agents.orchestration.Swarm")
    mocker.patch("haru.agents.orchestration.build_agent", side_effect=fake_build_agent)

    build_swarm(make_config(), mocker.Mock())

    args, kwargs = swarm_cls.call_args
    assert [member.name for member in args[0]] == ["researcher", "writer"]
    assert kwargs["max_handoffs"] == 4
    assert kwargs["execution_timeout"] == 120.0


def test_swarm_requires_swarm_section(mocker: Any) -> None:
    """Swarm orchestration without a swarm section fails."""
    agents = {"agents": {"writer": {"model": "fast"}}, "orchestration": {}}
    with pytest.raises(ConfigError, match="swarm"):
        build_swarm(make_config(agents), mocker.Mock())


def test_graph_respects_nodes_and_edge_order(mocker: Any) -> None:
    """The graph builder receives nodes and edges exactly as configured."""
    builder_cls = mocker.patch("haru.agents.orchestration.GraphBuilder")
    builder = builder_cls.return_value
    builder.build.return_value = "graph-obj"
    mocker.patch("haru.agents.orchestration.build_agent", side_effect=fake_build_agent)

    graph = build_graph(make_config(), mocker.Mock())

    node_calls = builder.add_node.call_args_list
    assert [(call.args[0].name, call.args[1]) for call in node_calls] == [
        ("researcher", "research"),
        ("writer", "write"),
    ]
    builder.add_edge.assert_called_once_with("research", "write")
    assert graph == "graph-obj"


def test_graph_requires_graph_section(mocker: Any) -> None:
    """Graph orchestration without a graph section fails."""
    agents = {"agents": {"writer": {"model": "fast"}}, "orchestration": {}}
    with pytest.raises(ConfigError, match="graph"):
        build_graph(make_config(agents), mocker.Mock())


def test_run_orchestration_default_pattern_supervisor(mocker: Any) -> None:
    """The configured default pattern (supervisor) executes and returns text."""
    supervisor = mocker.Mock(return_value="supervised answer")
    mocker.patch("haru.agents.orchestration.build_supervisor", return_value=supervisor)

    answer = run_orchestration(make_config(), "do the thing", session=mocker.Mock())

    assert answer == "supervised answer"
    supervisor.assert_called_once_with("do the thing")


def test_run_orchestration_swarm(mocker: Any) -> None:
    """The swarm pattern returns the last node's text."""
    swarm_result = SimpleNamespace(
        node_history=[SimpleNamespace(node_id="writer")],
        results={"writer": SimpleNamespace(result="swarm answer")},
    )
    swarm = mocker.Mock(return_value=swarm_result)
    mocker.patch("haru.agents.orchestration.build_swarm", return_value=swarm)

    answer = run_orchestration(make_config(), "go", "swarm", session=mocker.Mock())

    assert answer == "swarm answer"


def test_run_orchestration_graph(mocker: Any) -> None:
    """The graph pattern returns the final node's text via execution order."""
    graph_result = SimpleNamespace(
        execution_order=[
            SimpleNamespace(node_id="research"),
            SimpleNamespace(node_id="write"),
        ],
        results={
            "research": SimpleNamespace(result="notes"),
            "write": SimpleNamespace(result="graph answer"),
        },
    )
    graph = mocker.Mock(return_value=graph_result)
    mocker.patch("haru.agents.orchestration.build_graph", return_value=graph)

    answer = run_orchestration(make_config(), "go", "graph", session=mocker.Mock())

    assert answer == "graph answer"


def test_run_orchestration_unknown_pattern(mocker: Any) -> None:
    """Unknown patterns raise ConfigError."""
    with pytest.raises(ConfigError, match="carousel"):
        run_orchestration(make_config(), "go", "carousel", session=mocker.Mock())


def test_run_orchestration_requires_agents(mocker: Any) -> None:
    """Orchestration without an agents section fails."""
    config = HaruConfig.model_validate(BASE)
    with pytest.raises(ConfigError, match="agents"):
        run_orchestration(config, "go", session=mocker.Mock())
