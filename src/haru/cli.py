"""Click command group for the haru CLI."""

import click

from haru.commands.chat import chat
from haru.commands.login import login
from haru.commands.run import run
from haru.commands.session import session


@click.group()
@click.version_option(package_name="haru-cli", prog_name="haru")
def cli() -> None:
    """haru: a governed CLI for Amazon Bedrock agents."""


cli.add_command(chat)
cli.add_command(login)
cli.add_command(run)
cli.add_command(session)
