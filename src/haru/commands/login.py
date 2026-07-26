"""The ``haru login`` command: interactive IAM Identity Center sign-in."""

import webbrowser
from pathlib import Path

import click

from haru.auth.cache import write_token_cache
from haru.auth.sso import run_login
from haru.config import load_config
from haru.errors import HaruError


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: config/haru.yaml).",
)
def login(config_path: Path | None) -> None:
    """Sign in to AWS IAM Identity Center via the browser (PKCE)."""
    try:
        config = load_config(config_path, with_includes=False)
        auth = config.auth

        def _open(url: str) -> None:
            click.echo("Complete sign-in in your browser:")
            click.echo(url)
            if auth.sso.browser:
                webbrowser.open(url)

        token = run_login(auth, opener=_open)
        path = write_token_cache(token, auth.sso.start_url, auth.sso.sso_region)
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Login successful. Token cached at {path}.")
