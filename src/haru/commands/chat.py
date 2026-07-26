"""The ``haru chat`` command: an interactive streaming REPL."""

from collections.abc import Callable
from pathlib import Path

import click
from rich.console import Console

from haru.agents.factory import build_agent
from haru.auth.session import build_boto3_session
from haru.commands.streaming import collect_response, surface_guardrail
from haru.config import load_config
from haru.config.schema import HaruConfig
from haru.errors import HaruError
from haru.sessions.manager import build_session_manager
from haru.tools.mcp import started_mcp_clients

_EXIT_WORDS = frozenset({"exit", "quit"})


def run_chat(  # noqa: PLR0913 - keyword-only wiring points, all optional
    config: HaruConfig,
    agent_name: str | None,
    *,
    console: Console,
    read_input: Callable[[str], str] = input,
    prompts_root: Path | None = None,
    session_id: str | None = None,
) -> None:
    """Run the interactive chat loop until the user exits."""
    session = build_boto3_session(config.auth)
    session_manager = (
        build_session_manager(config, session_id, boto_session=session)
        if session_id is not None
        else None
    )
    with started_mcp_clients(config.mcp) as mcp_clients:
        agent = build_agent(
            config,
            agent_name,
            session,
            prompts_root=prompts_root,
            mcp_clients=mcp_clients,
            session_manager=session_manager,
        )
        console.print("haru chat - type 'exit' or press Ctrl-D to leave.")
        while True:
            try:
                line = read_input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nGoodbye.")
                return
            if not line:
                continue
            if line.lower() in _EXIT_WORDS:
                console.print("Goodbye.")
                return
            try:
                result = collect_response(
                    agent,
                    line,
                    lambda text: console.print(text, end="", markup=False, highlight=False),
                )
            except KeyboardInterrupt:
                console.print("\n[interrupted]")
                continue
            console.print()
            surface_guardrail(result, console)


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: config/haru.yaml).",
)
@click.option("--agent", "agent_name", default=None, help="Agent name from configuration.")
@click.option(
    "--session-id",
    "session_id",
    default=None,
    help="Persist and restore this conversation under the given session id.",
)
def chat(config_path: Path | None, agent_name: str | None, session_id: str | None) -> None:
    """Chat interactively with a Bedrock agent (streaming)."""
    console = Console()
    try:
        config = load_config(config_path)
        prompts_root = config_path.parent / "prompts" if config_path is not None else None
        run_chat(
            config,
            agent_name,
            console=console,
            prompts_root=prompts_root,
            session_id=session_id,
        )
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
