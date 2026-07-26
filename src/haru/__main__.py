"""Module entry point for ``python -m haru`` and the ``haru`` console script."""

from haru.cli import cli


def main() -> None:
    """Run the haru CLI."""
    cli()


if __name__ == "__main__":
    main()
