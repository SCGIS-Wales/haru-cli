"""The ``haru session`` command group: inspect persisted conversations."""

from pathlib import Path

import click

from haru.commands._common import load_cli_config
from haru.config import HaruConfig
from haru.errors import ConfigError, HaruError
from haru.sessions.manager import list_sessions


@click.group()
def session() -> None:
    """Manage persisted chat sessions."""


@session.command(name="list")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: config/haru.yaml).",
)
def list_command(config_path: Path | None) -> None:
    """List stored session ids."""
    config: HaruConfig | None = None
    try:
        _, config = load_cli_config(config_path, with_includes=False)
    except ConfigError:
        if config_path is not None:
            raise click.ClickException("Could not load the given configuration.") from None
        # No config anywhere: fall back to the default file-backend location.
    try:
        session_ids = list_sessions(config)
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    if not session_ids:
        click.echo("No stored sessions.")
        return
    for session_id in session_ids:
        click.echo(session_id)
