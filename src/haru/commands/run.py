"""The ``haru run`` command: one-shot prompt execution."""

from pathlib import Path

import click
from rich.console import Console

from haru.agents.factory import build_agent
from haru.auth.session import build_boto3_session
from haru.commands.streaming import collect_response, surface_guardrail
from haru.config import load_config
from haru.config.schema import HaruConfig
from haru.errors import HaruError


def run_prompt(
    config: HaruConfig,
    prompt: str,
    agent_name: str | None = None,
    *,
    console: Console | None = None,
) -> str:
    """Execute ``prompt`` once and return the full response text.

    Guardrail interventions are surfaced on ``console`` (stderr by default).
    """
    session = build_boto3_session(config.auth)
    agent = build_agent(config, agent_name, session)
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
        config = load_config(config_path)
        answer = run_prompt(config, prompt, agent_name)
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(answer)
