"""Click command group for the haru CLI."""

import click

from haru.commands.agents import agents
from haru.commands.chat import chat
from haru.commands.config import config
from haru.commands.doctor import doctor
from haru.commands.login import login
from haru.commands.run import run
from haru.commands.session import session
from haru.logging_setup import configure_logging


@click.group()
@click.version_option(package_name="haru-cli", prog_name="haru")
@click.option("--debug", is_flag=True, help="Verbose logging, including AWS API calls, to stderr.")
@click.pass_context
def cli(ctx: click.Context, debug: bool) -> None:
    """haru: a governed CLI for Amazon Bedrock agents."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug
    # Bootstrap before config is read so --debug works even when config is broken.
    configure_logging(None, debug=debug)


cli.add_command(agents)
cli.add_command(chat)
cli.add_command(config)
cli.add_command(doctor)
cli.add_command(login)
cli.add_command(run)
cli.add_command(session)
