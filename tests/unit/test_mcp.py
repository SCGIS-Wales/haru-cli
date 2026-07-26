"""Tests for the built-in tool registry and MCP client construction."""

from typing import Any

import pytest

from haru.config.schema import AgentConfig, MCPConfig
from haru.errors import ConfigError, ToolError
from haru.tools.mcp import build_mcp_clients, collect_tools, started_mcp_clients
from haru.tools.registry import available_builtin_tools, resolve_builtin_tools


def make_mcp_config(**servers: dict[str, Any]) -> MCPConfig:
    """Build an MCPConfig from raw server mappings."""
    return MCPConfig.model_validate({"mcp_servers": servers})


STDIO_SERVER: dict[str, Any] = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["some-mcp-server@latest"],
}
HTTP_SERVER: dict[str, Any] = {
    "transport": "streamable-http",
    "url": "https://mcp.example.com/mcp",
    "headers": {"authorization": "resolved-value"},
}


def test_resolve_builtin_tools() -> None:
    """Configured names resolve to strands_tools implementations."""
    tools = resolve_builtin_tools(["calculator", "current_time"])
    assert len(tools) == 2
    assert callable(tools[0]) or hasattr(tools[0], "TOOL_SPEC")


def test_resolve_builtin_tools_unknown_name() -> None:
    """Names outside the allowlist raise ConfigError listing what exists."""
    with pytest.raises(ConfigError, match=r"chainsaw.*calculator"):
        resolve_builtin_tools(["chainsaw"])


def test_allowlist_excludes_shell_tools() -> None:
    """The registry deliberately excludes shell/exec tools."""
    names = available_builtin_tools()
    assert "calculator" in names
    assert "shell" not in names
    assert "python_repl" not in names


def test_build_stdio_client(mocker: Any) -> None:
    """A stdio server constructs MCPClient over stdio_client parameters."""
    mcp_client = mocker.patch("haru.tools.mcp.MCPClient")
    stdio = mocker.patch("haru.tools.mcp.stdio_client")

    clients = build_mcp_clients(make_mcp_config(docs=STDIO_SERVER))

    assert set(clients) == {"docs"}
    assert mcp_client.call_args.kwargs["continue_on_error"] is False
    transport = mcp_client.call_args.args[0]
    transport()
    params = stdio.call_args.args[0]
    assert params.command == "uvx"
    assert params.args == ["some-mcp-server@latest"]


def test_build_streamable_http_client(mocker: Any) -> None:
    """A streamable-http server constructs MCPClient over streamablehttp_client."""
    mcp_client = mocker.patch("haru.tools.mcp.MCPClient")
    http = mocker.patch("haru.tools.mcp.streamablehttp_client")

    clients = build_mcp_clients(make_mcp_config(api=HTTP_SERVER))

    assert set(clients) == {"api"}
    transport = mcp_client.call_args.args[0]
    transport()
    assert http.call_args.args[0] == "https://mcp.example.com/mcp"
    assert http.call_args.kwargs["headers"] == {"authorization": "resolved-value"}


def test_disabled_server_is_skipped(mocker: Any) -> None:
    """Disabled servers never construct a client."""
    mcp_client = mocker.patch("haru.tools.mcp.MCPClient")

    clients = build_mcp_clients(make_mcp_config(off={**STDIO_SERVER, "disabled": True}))

    assert clients == {}
    mcp_client.assert_not_called()


def test_construction_failure_with_continue_on_error(mocker: Any) -> None:
    """A failing server with continue_on_error is skipped, not fatal."""
    mocker.patch("haru.tools.mcp.MCPClient", side_effect=RuntimeError("boom"))

    clients = build_mcp_clients(make_mcp_config(flaky={**STDIO_SERVER, "continue_on_error": True}))

    assert clients == {}


def test_construction_failure_without_continue_on_error(mocker: Any) -> None:
    """A failing server without the flag aborts startup with ToolError."""
    mocker.patch("haru.tools.mcp.MCPClient", side_effect=RuntimeError("boom"))

    with pytest.raises(ToolError, match="strict"):
        build_mcp_clients(make_mcp_config(strict=STDIO_SERVER))


def make_agent_config(
    tools: list[str] | None = None, mcp_servers: list[str] | None = None
) -> AgentConfig:
    """Build an AgentConfig referencing the given tools and servers."""
    return AgentConfig.model_validate(
        {"model": "fast", "tools": tools or [], "mcp_servers": mcp_servers or []}
    )


def test_collect_tools_combines_builtin_and_mcp(mocker: Any) -> None:
    """Built-in tools and MCP-listed tools land in one list."""
    client = mocker.Mock()
    client.list_tools_sync.return_value = ["mcp-tool-1", "mcp-tool-2"]
    agent_cfg = make_agent_config(tools=["calculator"], mcp_servers=["docs"])

    tools = collect_tools(agent_cfg, {"calculator": "builtin-calc"}, {"docs": client})

    assert tools == ["builtin-calc", "mcp-tool-1", "mcp-tool-2"]
    client.list_tools_sync.assert_called_once_with()


def test_collect_tools_default_registry(mocker: Any) -> None:
    """A None registry resolves built-ins from the standard allowlist."""
    tools = collect_tools(make_agent_config(tools=["current_time"]), None, {})
    assert len(tools) == 1


def test_collect_tools_skips_missing_client() -> None:
    """Servers absent from the client map (disabled/failed) are skipped."""
    agent_cfg = make_agent_config(mcp_servers=["ghost"])
    assert collect_tools(agent_cfg, {}, {}) == []


def test_collect_tools_listing_failure_tolerated(mocker: Any) -> None:
    """A listing failure is tolerated when the server allows it."""
    client = mocker.Mock()
    client.list_tools_sync.side_effect = RuntimeError("down")
    agent_cfg = make_agent_config(mcp_servers=["flaky"])
    mcp_cfg = make_mcp_config(flaky={**STDIO_SERVER, "continue_on_error": True})

    tools = collect_tools(agent_cfg, {}, {"flaky": client}, mcp_cfg=mcp_cfg)

    assert tools == []


def test_collect_tools_listing_failure_fatal(mocker: Any) -> None:
    """A listing failure without the flag raises ToolError."""
    client = mocker.Mock()
    client.list_tools_sync.side_effect = RuntimeError("down")
    agent_cfg = make_agent_config(mcp_servers=["strict"])
    mcp_cfg = make_mcp_config(strict=STDIO_SERVER)

    with pytest.raises(ToolError, match="strict"):
        collect_tools(agent_cfg, {}, {"strict": client}, mcp_cfg=mcp_cfg)


def test_collect_tools_registry_missing_name() -> None:
    """An explicit registry missing a configured tool raises ToolError."""
    agent_cfg = make_agent_config(tools=["calculator"])
    with pytest.raises(ToolError, match="calculator"):
        collect_tools(agent_cfg, {}, {})


def test_started_mcp_clients_lifecycle(mocker: Any) -> None:
    """Clients are started on entry and stopped on exit."""
    client = mocker.Mock()
    mocker.patch("haru.tools.mcp.build_mcp_clients", return_value={"docs": client})

    with started_mcp_clients(make_mcp_config(docs=STDIO_SERVER)) as clients:
        assert clients == {"docs": client}
        client.start.assert_called_once_with()
        client.stop.assert_not_called()

    client.stop.assert_called_once_with(None, None, None)


def test_started_mcp_clients_none_config() -> None:
    """A missing MCP section yields no clients."""
    with started_mcp_clients(None) as clients:
        assert clients == {}


def test_started_mcp_clients_start_failure_tolerated(mocker: Any) -> None:
    """A start failure is tolerated for servers with continue_on_error."""
    client = mocker.Mock()
    client.start.side_effect = RuntimeError("no route")
    mocker.patch("haru.tools.mcp.build_mcp_clients", return_value={"flaky": client})
    mcp_cfg = make_mcp_config(flaky={**STDIO_SERVER, "continue_on_error": True})

    with started_mcp_clients(mcp_cfg) as clients:
        assert clients == {}


def test_started_mcp_clients_start_failure_fatal(mocker: Any) -> None:
    """A start failure without the flag raises ToolError and stops started peers."""
    good = mocker.Mock()
    bad = mocker.Mock()
    bad.start.side_effect = RuntimeError("no route")
    mocker.patch("haru.tools.mcp.build_mcp_clients", return_value={"good": good, "strict": bad})
    mcp_cfg = make_mcp_config(good=STDIO_SERVER, strict=STDIO_SERVER)

    with pytest.raises(ToolError, match="strict"), started_mcp_clients(mcp_cfg):
        pass

    good.stop.assert_called_once_with(None, None, None)
