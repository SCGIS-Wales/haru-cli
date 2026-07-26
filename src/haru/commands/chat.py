"""The ``haru chat`` command: an interactive streaming REPL.

Besides free-form prompts, the REPL understands slash commands (``/help``,
``/model``, ``/agent``) for discovering and switching between the configured
models and agents mid-session.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import click
from pydantic import ValidationError
from rich.console import Console

from haru.agents.factory import build_agent
from haru.auth.session import build_boto3_session
from haru.commands.streaming import collect_response, surface_guardrail
from haru.config import load_config, resolve_config_path
from haru.config.schema import HaruConfig, SamplingConfig
from haru.errors import ConfigError, HaruError
from haru.models.bedrock import effective_sampling, get_model_config, sampling_overrides
from haru.observability.telemetry import configure_telemetry
from haru.sessions.manager import build_session_manager
from haru.tools.mcp import started_mcp_clients

_EXIT_WORDS = frozenset({"exit", "quit"})
_SAMPLING_FIELDS = ("temperature", "top_p", "top_k", "seed")

_HELP_TEXT = """\
Commands:
  /help                    Show this help
  /model                   List configured models
  /model <name>            Switch the default agent to <name> (resets the conversation)
  /agent                   List configured agents
  /agent <name>            Switch to agent <name> (resets the conversation)
  /sampling                Show effective sampling for the active target
  /sampling k=v [k=v ...]  Override temperature/top_p/top_k/seed (resets the conversation)
  /sampling reset          Clear sampling overrides (resets the conversation)
  exit | quit              Leave the chat (also /exit, /quit, Ctrl-D)"""


def run_chat(  # noqa: PLR0913 - keyword-only wiring points, all optional
    config: HaruConfig,
    agent_name: str | None,
    *,
    console: Console,
    read_input: Callable[[str], str] = input,
    prompts_root: Path | None = None,
    session_id: str | None = None,
    sampling: SamplingConfig | None = None,
) -> None:
    """Run the interactive chat loop until the user exits."""
    session = build_boto3_session(config.auth)
    session_manager = (
        build_session_manager(config, session_id, boto_session=session)
        if session_id is not None
        else None
    )
    with started_mcp_clients(config.mcp) as mcp_clients:
        state: dict[str, Any] = {
            "agent_name": agent_name,
            "model_name": None,
            "sampling": sampling,
        }

        def make_agent(name: str | None, model: str | None) -> Any:
            return build_agent(
                config,
                name,
                session,
                prompts_root=prompts_root,
                mcp_clients=mcp_clients,
                session_manager=session_manager,
                model_name=model,
                sampling=state["sampling"],
            )

        state["agent"] = make_agent(agent_name, None)
        console.print("haru chat - type /help for commands, 'exit' or Ctrl-D to leave.")
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
            if line.startswith("/"):
                if not _handle_slash(line, config, console, state, make_agent):
                    console.print("Goodbye.")
                    return
                continue
            try:
                result = collect_response(
                    state["agent"],
                    line,
                    lambda text: console.print(text, end="", markup=False, highlight=False),
                )
            except KeyboardInterrupt:
                console.print("\n[interrupted]")
                continue
            console.print()
            surface_guardrail(result, console)


def _handle_slash(
    line: str,
    config: HaruConfig,
    console: Console,
    state: dict[str, Any],
    make_agent: Callable[[str | None, str | None], Any],
) -> bool:
    """Handle a ``/command`` line; return False when the REPL should exit."""
    command, _, raw_argument = line[1:].partition(" ")
    name = command.lower()
    argument = raw_argument.strip() or None

    if name in _EXIT_WORDS:
        return False
    if name == "help":
        console.print(_HELP_TEXT)
    elif name == "model":
        if argument is None:
            _print_models(config, console, state)
        else:
            _switch(state, console, make_agent, agent_name=None, model_name=argument)
    elif name == "agent":
        if argument is None:
            _print_agents(config, console, state)
        else:
            _switch(state, console, make_agent, agent_name=argument, model_name=None)
    elif name == "sampling":
        if argument is None:
            _print_sampling(config, console, state)
        else:
            _apply_sampling(argument, config, console, state, make_agent)
    else:
        console.print(f"Unknown command /{command}; type /help for commands.")
    return True


def _parse_sampling(argument: str) -> SamplingConfig:
    """Parse ``key=value`` sampling tokens into a SamplingConfig."""
    values: dict[str, str] = {}
    for token in argument.split():
        key, separator, raw_value = token.partition("=")
        if not separator or key not in _SAMPLING_FIELDS or not raw_value:
            fields = ", ".join(_SAMPLING_FIELDS)
            raise ConfigError(f"Bad sampling token {token!r}; use key=value with keys: {fields}")
        values[key] = raw_value
    try:
        return SamplingConfig.model_validate(values)
    except ValidationError as exc:
        raise ConfigError(f"Invalid sampling values: {exc}") from exc


def _apply_sampling(
    argument: str,
    config: HaruConfig,
    console: Console,
    state: dict[str, Any],
    make_agent: Callable[[str | None, str | None], Any],
) -> None:
    """Apply or clear sampling overrides, keeping the old state on failure."""
    if argument.lower() == "reset":
        override: SamplingConfig | None = None
    else:
        try:
            override = _parse_sampling(argument)
        except ConfigError as exc:
            console.print(f"[red]{exc}[/]")
            return
    previous = state["sampling"]
    state["sampling"] = override
    try:
        agent = make_agent(state["agent_name"], state["model_name"])
    except HaruError as exc:
        state["sampling"] = previous
        console.print(f"[red]{exc}[/]")
        return
    state["agent"] = agent
    if override is None:
        console.print("Sampling overrides cleared (conversation reset).")
    else:
        console.print("Sampling overrides applied (conversation reset).")
    _print_sampling(config, console, state)


def _print_sampling(config: HaruConfig, console: Console, state: dict[str, Any]) -> None:
    """Show model defaults, session overrides, and effective sampling values."""
    agent_name = state["agent_name"]
    model_key = state["model_name"]
    if agent_name is not None and config.agents is not None:
        agent_cfg = config.agents.agents.get(agent_name)
        model_key = agent_cfg.model if agent_cfg is not None else model_key
    try:
        model_cfg = get_model_config(config, model_key)
    except HaruError as exc:
        console.print(f"[red]{exc}[/]")
        return
    override = cast(SamplingConfig | None, state["sampling"])
    effective = effective_sampling(model_cfg, override)
    console.print(f"Sampling for model {model_cfg.model_id!r} (unset = provider default):")
    for field in _SAMPLING_FIELDS:
        model_value = getattr(model_cfg, field)
        override_value = getattr(override, field) if override is not None else None
        effective_value = getattr(effective, field)
        console.print(
            f"  {field:<12} model={_fmt(model_value):<8}"
            f" override={_fmt(override_value):<8} effective={_fmt(effective_value)}"
        )


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def _switch(
    state: dict[str, Any],
    console: Console,
    make_agent: Callable[[str | None, str | None], Any],
    *,
    agent_name: str | None,
    model_name: str | None,
) -> None:
    """Switch the active agent/model, keeping the old one on failure."""
    try:
        agent = make_agent(agent_name, model_name)
    except HaruError as exc:
        console.print(f"[red]{exc}[/]")
        return
    state.update({"agent": agent, "agent_name": agent_name, "model_name": model_name})
    target = f"agent {agent_name!r}" if agent_name is not None else f"model {model_name!r}"
    console.print(f"Switched to {target} (conversation reset).")


def _print_models(config: HaruConfig, console: Console, state: dict[str, Any]) -> None:
    """List the configured models, marking the default and active ones."""
    if config.models is None:
        console.print("No models configured.")
        return
    for name in sorted(config.models.models):
        entry = config.models.models[name]
        markers = []
        if name == config.models.default_model:
            markers.append("default")
        if state["agent_name"] is None and name == (
            state["model_name"] or config.models.default_model
        ):
            markers.append("active")
        suffix = f"  ({', '.join(markers)})" if markers else ""
        console.print(f"  {name:<12} {entry.model_id}{suffix}")


def _print_agents(config: HaruConfig, console: Console, state: dict[str, Any]) -> None:
    """List the configured agents, marking the active one."""
    if config.agents is None or not config.agents.agents:
        console.print("No agents configured; using the default agent.")
        return
    for name in sorted(config.agents.agents):
        entry = config.agents.agents[name]
        active = "  (active)" if name == state["agent_name"] else ""
        prompt_ref = entry.system_prompt_ref or "-"
        console.print(f"  {name:<12} model={entry.model} prompt={prompt_ref}{active}")


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: ./config/haru.yaml, then ~/.config/haru/haru.yaml).",
)
@click.option("--agent", "agent_name", default=None, help="Agent name from configuration.")
@click.option(
    "--session-id",
    "session_id",
    default=None,
    help="Persist and restore this conversation under the given session id.",
)
@click.option("--temperature", type=float, default=None, help="Sampling temperature (0-1).")
@click.option("--top-p", "top_p", type=float, default=None, help="Nucleus sampling (0-1).")
@click.option("--top-k", "top_k", type=int, default=None, help="Top-k sampling (>=1).")
@click.option("--seed", type=int, default=None, help="Seed (models that support it only).")
def chat(  # noqa: PLR0913, PLR0917 - one option per flag; Click passes positionally
    config_path: Path | None,
    agent_name: str | None,
    session_id: str | None,
    temperature: float | None,
    top_p: float | None,
    top_k: int | None,
    seed: int | None,
) -> None:
    """Chat interactively with a Bedrock agent (streaming)."""
    console = Console()
    try:
        resolved = resolve_config_path(config_path)
        config = load_config(resolved)
        configure_telemetry(config.observability)
        run_chat(
            config,
            agent_name,
            console=console,
            prompts_root=resolved.parent / "prompts",
            session_id=session_id,
            sampling=sampling_overrides(
                temperature=temperature, top_p=top_p, top_k=top_k, seed=seed
            ),
        )
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
