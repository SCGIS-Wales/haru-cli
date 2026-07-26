"""Click command group for the haru CLI."""

import click


@click.group()
@click.version_option(package_name="haru-cli", prog_name="haru")
def cli() -> None:
    """haru: a governed CLI for Amazon Bedrock agents."""
