"""The ``haru run`` command: one-shot prompt execution."""

from pathlib import Path

import click
from rich.console import Console

from haru.agents.factory import build_agent
from haru.auth.session import build_boto3_session
from haru.commands._common import load_cli_config
from haru.commands.streaming import (
    build_bedrock_context,
    collect_response,
    surface_guardrail,
)
from haru.config.schema import HaruConfig, SamplingConfig
from haru.errors import HaruError
from haru.models.bedrock import sampling_overrides
from haru.observability.telemetry import configure_telemetry
from haru.tools.mcp import started_mcp_clients


def run_prompt(  # noqa: PLR0913 - keyword-only wiring points, all optional
    config: HaruConfig,
    prompt: str,
    agent_name: str | None = None,
    *,
    console: Console | None = None,
    prompts_root: Path | None = None,
    sampling: SamplingConfig | None = None,
) -> str:
    """Execute ``prompt`` once and return the full response text.

    Guardrail interventions are surfaced on ``console`` (stderr by default).
    """
    session = build_boto3_session(config.auth)
    with started_mcp_clients(config.mcp) as mcp_clients:
        agent = build_agent(
            config,
            agent_name,
            session,
            prompts_root=prompts_root,
            mcp_clients=mcp_clients,
            sampling=sampling,
        )
        chunks: list[str] = []
        result = collect_response(
            agent, prompt, chunks.append, context=build_bedrock_context(config, agent_name)
        )
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
@click.option("--temperature", type=float, default=None, help="Sampling temperature (0-1).")
@click.option("--top-p", "top_p", type=float, default=None, help="Nucleus sampling (0-1).")
@click.option("--top-k", "top_k", type=int, default=None, help="Top-k sampling (>=1).")
@click.option("--seed", type=int, default=None, help="Seed (models that support it only).")
def run(  # noqa: PLR0913, PLR0917 - one option per flag; Click passes positionally
    prompt: str,
    config_path: Path | None,
    agent_name: str | None,
    temperature: float | None,
    top_p: float | None,
    top_k: int | None,
    seed: int | None,
) -> None:
    """Run a single prompt and print the answer."""
    try:
        resolved, config = load_cli_config(config_path)
        configure_telemetry(config.observability)
        answer = run_prompt(
            config,
            prompt,
            agent_name,
            prompts_root=resolved.parent / "prompts",
            sampling=sampling_overrides(
                temperature=temperature, top_p=top_p, top_k=top_k, seed=seed
            ),
        )
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(answer)
