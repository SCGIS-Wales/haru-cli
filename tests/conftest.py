"""Shared pytest fixtures for the haru-cli test suite."""

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    """Return a Click test runner for invoking CLI commands."""
    return CliRunner()
