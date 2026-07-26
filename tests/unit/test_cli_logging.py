"""Tests that commands re-apply logging from configuration.

``cli`` configures logging before any configuration is read, so ``--debug``
survives a broken configuration. Without a second call after loading,
``logging.yaml`` would never take effect -- which is the bug these cover.
"""

from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from haru.cli import cli
from haru.commands._common import debug_flag
from haru.config import load_config, load_logging

CONFIG = """\
app:
  name: haru
auth:
  sso:
    start_url: https://example.awsapps.com/start
    sso_region: us-east-1
  bedrock_region: us-east-1
includes:
  logging: logging.yaml
"""

LOGGING_INCLUDE = """\
logging:
  level: ERROR
  format: json
  file: null
observability:
  otel:
    enabled: false
    endpoint: null
    service_name: haru-cli
    console_export: false
"""


def write_config(tmp_path: Path, *, with_logging: bool = True) -> Path:
    """Write a config whose logging include sets level ERROR and json format."""
    path = tmp_path / "haru.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    if with_logging:
        (tmp_path / "logging.yaml").write_text(LOGGING_INCLUDE, encoding="utf-8")
    return path


@pytest.fixture
def runner() -> CliRunner:
    """A Click test runner."""
    return CliRunner()


def test_configured_logging_is_applied(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """The second configure_logging call carries the loaded LoggingConfig."""
    configure = mocker.patch("haru.commands._common.configure_logging")

    result = runner.invoke(cli, ["agents", "--config", str(write_config(tmp_path))])

    assert result.exit_code == 0, result.output
    cfg = configure.call_args.args[0]
    assert cfg is not None
    assert cfg.level == "ERROR"
    assert cfg.format == "json"
    assert configure.call_args.kwargs["debug"] is False


def test_debug_flag_reaches_the_reload(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """--debug is read from the root context, not threaded through signatures."""
    configure = mocker.patch("haru.commands._common.configure_logging")

    runner.invoke(cli, ["--debug", "agents", "--config", str(write_config(tmp_path))])

    assert configure.call_args.kwargs["debug"] is True


def test_logging_include_is_fail_soft(tmp_path: Path) -> None:
    """A missing or invalid logging include yields None, never an exception.

    ``login`` and ``session list`` load without includes precisely so a broken
    sibling file cannot block sign-in; honouring logging.yaml must not
    reintroduce that coupling.
    """
    write_config(tmp_path, with_logging=False)
    base = load_config(tmp_path / "haru.yaml", with_includes=False)
    assert load_logging(base, tmp_path) is None

    (tmp_path / "logging.yaml").write_text("logging: {level: 12}", encoding="utf-8")
    assert load_logging(base, tmp_path) is None


def test_logging_include_is_loaded_without_other_includes(tmp_path: Path) -> None:
    """A valid include is honoured even when the base was loaded bare."""
    write_config(tmp_path)
    base = load_config(tmp_path / "haru.yaml", with_includes=False)

    cfg = load_logging(base, tmp_path)

    assert cfg is not None
    assert cfg.level == "ERROR"


def test_debug_flag_outside_a_click_context() -> None:
    """debug_flag is safe to call with no ambient context."""
    assert debug_flag() is False


def test_debug_flag_with_no_context_object() -> None:
    """A context whose obj is not the expected dict reads as not-debug."""
    with click.Context(click.Command("x")):
        assert debug_flag() is False
