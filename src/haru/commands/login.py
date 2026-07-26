"""The ``haru login`` command: interactive IAM Identity Center sign-in.

Browser-only sign-in: no IAM role or account id needs to be known upfront.
After the token exchange, the accessible accounts and roles are discovered
from Identity Center, auto-selected when unambiguous, and remembered.
"""

import webbrowser
from pathlib import Path

import click

from haru.auth.cache import write_token_cache
from haru.auth.identity import select_identity
from haru.auth.sso import run_login
from haru.config import load_config
from haru.errors import HaruError


def _prompt_choice(question: str, labels: list[str]) -> int:
    """Interactive numbered chooser for accounts and roles."""
    click.echo(f"{question}:")
    for number, label in enumerate(labels, start=1):
        click.echo(f"  {number}. {label}")
    choice: int = click.prompt("Enter a number", type=click.IntRange(1, len(labels)))
    return choice - 1


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: ./config/haru.yaml, then ~/.config/haru/haru.yaml).",
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
        identity = select_identity(auth.sso, token, chooser=_prompt_choice, notify=click.echo)
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Login successful. Token cached at {path}.")
    account_label = (
        f"{identity.account_id} ({identity.account_name})"
        if identity.account_name
        else identity.account_id
    )
    account_note = (
        "" if identity.account_source == "login selection" else (f" [{identity.account_source}]")
    )
    role_note = "" if identity.role_source == "login selection" else (f" [{identity.role_source}]")
    click.echo(
        f"Using account {account_label}{account_note}, role {identity.role_name}{role_note}."
    )
