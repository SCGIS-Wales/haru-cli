"""Tests for the haru Click command group."""

from importlib.metadata import version

from click.testing import CliRunner

from haru.cli import cli


def test_version(runner: CliRunner) -> None:
    """``haru --version`` exits 0 and reports the installed distribution version."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert version("haru-cli") in result.output
    assert "haru" in result.output
