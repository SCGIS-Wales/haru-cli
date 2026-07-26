"""The ``haru agents`` command: list configured agents."""

from pathlib import Path

import click

from haru.config import load_config, resolve_config_path
from haru.errors import HaruError


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: ./config/haru.yaml, then ~/.config/haru/haru.yaml).",
)
def agents(config_path: Path | None) -> None:
    """List configured agents (model, prompt, tools, MCP servers)."""
    try:
        loaded = load_config(resolve_config_path(config_path))
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc

    if loaded.agents is None or not loaded.agents.agents:
        click.echo("No agents configured; chat uses the default agent.")
        return
    for name in sorted(loaded.agents.agents):
        entry = loaded.agents.agents[name]
        parts = [f"model={entry.model}"]
        if entry.system_prompt_ref:
            parts.append(f"prompt={entry.system_prompt_ref}")
        if entry.tools:
            parts.append(f"tools={','.join(entry.tools)}")
        if entry.mcp_servers:
            parts.append(f"mcp={','.join(entry.mcp_servers)}")
        click.echo(f"{name:<14} {'  '.join(parts)}")
    if loaded.agents.orchestration is not None:
        click.echo(
            f"\nDefault orchestration pattern: {loaded.agents.orchestration.default_pattern}"
        )
