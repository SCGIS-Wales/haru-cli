"""The ``haru run`` command: one-shot prompt execution."""

from pathlib import Path

import click
from rich.console import Console

from haru.agents.factory import build_agent
from haru.auth.session import build_boto3_session
from haru.commands.streaming import collect_response, surface_guardrail
from haru.config import load_config, resolve_config_path
from haru.config.schema import HaruConfig
from haru.errors import HaruError
from haru.observability.telemetry import configure_telemetry
from haru.tools.mcp import started_mcp_clients


def run_prompt(
    config: HaruConfig,
    prompt: str,
    agent_name: str | None = None,
    *,
    console: Console | None = None,
    prompts_root: Path | None = None,
) -> str:
    """Execute ``prompt`` once and return the full response text.

    Guardrail interventions are surfaced on ``console`` (stderr by default).
    """
    session = build_boto3_session(config.auth)
    with started_mcp_clients(config.mcp) as mcp_clients:
        agent = build_agent(
            config, agent_name, session, prompts_root=prompts_root, mcp_clients=mcp_clients
        )
        chunks: list[str] = []
        result = collect_response(agent, prompt, chunks.append)
    surface_guardrail(result, console if console is not None else Console(stderr=True))
    return "".join(chunks)


@click.command()
@click.argument("prompt")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: config/haru.yaml).",
)
@click.option("--agent", "agent_name", default=None, help="Agent name from configuration.")
def run(prompt: str, config_path: Path | None, agent_name: str | None) -> None:
    """Run a single prompt and print the answer."""
    try:
        resolved = resolve_config_path(config_path)
        config = load_config(resolved)
        configure_telemetry(config.observability)
        answer = run_prompt(config, prompt, agent_name, prompts_root=resolved.parent / "prompts")
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(answer)
