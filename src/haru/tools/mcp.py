"""MCP client construction from typed configuration.

Environment references in commands, arguments, and headers are already
resolved by the configuration loader, so values arrive here as plain strings.
Connections are made lazily by Strands; construction failures honour each
server's ``continue_on_error`` flag so one bad server cannot abort startup.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from haru.config.schema import AgentConfig, MCPConfig, MCPServerConfig
from haru.errors import ToolError
from haru.tools.registry import resolve_builtin_tools

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)


def build_mcp_clients(mcp_cfg: MCPConfig) -> dict[str, MCPClient]:
    """Build an MCPClient per enabled server; skip disabled ones.

    A server that fails to construct is skipped with a warning when its
    ``continue_on_error`` flag is set, and raises ToolError otherwise.
    """
    clients: dict[str, MCPClient] = {}
    for name, server in mcp_cfg.mcp_servers.items():
        if server.disabled:
            logger.info("MCP server %r is disabled; skipping", name)
            continue
        try:
            clients[name] = _build_client(server)
        except Exception as exc:
            if server.continue_on_error:
                logger.warning("MCP server %r failed to construct; continuing: %s", name, exc)
                continue
            raise ToolError(f"MCP server {name!r} failed to construct: {exc}") from exc
    return clients


@contextlib.contextmanager
def started_mcp_clients(mcp_cfg: MCPConfig | None) -> Iterator[dict[str, MCPClient]]:
    """Build and start every enabled MCP client for the duration of the context.

    Startup failures honour each server's ``continue_on_error`` flag; all
    started clients are stopped on exit.
    """
    clients = build_mcp_clients(mcp_cfg) if mcp_cfg is not None else {}
    started: dict[str, MCPClient] = {}
    try:
        for name, client in clients.items():
            try:
                client.start()
            except Exception as exc:
                if _continue_on_error(mcp_cfg, name):
                    logger.warning("MCP server %r failed to start; continuing: %s", name, exc)
                    continue
                raise ToolError(f"MCP server {name!r} failed to start: {exc}") from exc
            started[name] = client
        yield started
    finally:
        for client in started.values():
            with contextlib.suppress(Exception):
                client.stop(None, None, None)


def collect_tools(
    agent_cfg: AgentConfig,
    registry: Mapping[str, Any] | None,
    mcp_clients: Mapping[str, MCPClient],
    *,
    mcp_cfg: MCPConfig | None = None,
) -> list[Any]:
    """Collect an agent's built-in and MCP tools into one list.

    ``registry`` maps built-in tool names to implementations; None resolves
    them from the standard allowlist. MCP tools are listed with
    ``list_tools_sync`` inside the already-started client context; a listing
    failure is tolerated for servers with ``continue_on_error``.
    """
    tools: list[Any] = []
    if registry is None:
        tools.extend(resolve_builtin_tools(agent_cfg.tools))
    else:
        tools.extend(resolve_builtin_tools_from(registry, agent_cfg.tools))

    for name in agent_cfg.mcp_servers:
        client = mcp_clients.get(name)
        if client is None:
            logger.info("MCP server %r unavailable (disabled or failed); skipping", name)
            continue
        try:
            tools.extend(client.list_tools_sync())
        except Exception as exc:
            if _continue_on_error(mcp_cfg, name):
                logger.warning("Listing tools from MCP server %r failed; continuing: %s", name, exc)
                continue
            raise ToolError(f"Listing tools from MCP server {name!r} failed: {exc}") from exc
    return tools


def resolve_builtin_tools_from(registry: Mapping[str, Any], names: tuple[str, ...]) -> list[Any]:
    """Resolve tool names against an explicit registry mapping."""
    missing = [name for name in names if name not in registry]
    if missing:
        raise ToolError(f"Tools not present in registry: {', '.join(sorted(missing))}")
    return [registry[name] for name in names]


def _build_client(server: MCPServerConfig) -> MCPClient:
    from mcp import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    if server.transport == "stdio":
        params = StdioServerParameters(command=str(server.command), args=list(server.args))

        def stdio_transport() -> Any:
            return stdio_client(params)

        return MCPClient(stdio_transport, continue_on_error=server.continue_on_error)

    url = str(server.url)
    headers = dict(server.headers) if server.headers else None

    def http_transport() -> Any:
        return streamablehttp_client(url, headers=headers)

    return MCPClient(http_transport, continue_on_error=server.continue_on_error)


def _continue_on_error(mcp_cfg: MCPConfig | None, name: str) -> bool:
    if mcp_cfg is None:
        return False
    server = mcp_cfg.mcp_servers.get(name)
    return server is not None and server.continue_on_error
