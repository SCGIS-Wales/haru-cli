"""Tests for the ``haru config`` and ``haru agents`` commands."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from haru.cli import cli
from haru.config import load_config

INIT_ARGS = [
    "config",
    "init",
    "--start-url",
    "https://example.awsapps.com/start",
    "--sso-region",
    "us-east-1",
    "--bedrock-region",
    "eu-west-1",
    "--role-name",
    "HaruBedrockInvoke",
]


def run_init(runner: CliRunner, target: Path, *extra: str) -> None:
    """Run config init into ``target`` with a blank guardrail id."""
    result = runner.invoke(cli, [*INIT_ARGS, "--guardrail-id", "", "--dir", str(target), *extra])
    assert result.exit_code == 0, result.output


def test_init_writes_loadable_config(runner: CliRunner, tmp_path: Path) -> None:
    """config init scaffolds a complete tree that load_config accepts."""
    target = tmp_path / "haru-config"
    run_init(runner, target)

    for name in (
        "haru.yaml",
        "models.yaml",
        "agents.yaml",
        "mcp.yaml",
        "guardrails.yaml",
        "logging.yaml",
    ):
        assert (target / name).is_file(), name
    assert (target / "prompts" / "supervisor.md").is_file()

    config = load_config(target / "haru.yaml")
    assert config.auth.sso.start_url == "https://example.awsapps.com/start"
    assert config.auth.bedrock_region == "eu-west-1"
    assert config.models is not None
    assert config.guardrails is not None
    assert config.guardrails.enabled is False


def test_init_with_guardrail_id_enables_guardrails(runner: CliRunner, tmp_path: Path) -> None:
    """A provided guardrail id produces enabled guardrails with that id."""
    target = tmp_path / "haru-config"
    result = runner.invoke(cli, [*INIT_ARGS, "--guardrail-id", "gr-123", "--dir", str(target)])
    assert result.exit_code == 0, result.output

    config = load_config(target / "haru.yaml")
    assert config.guardrails is not None
    assert config.guardrails.enabled is True
    assert config.guardrails.guardrail_id == "gr-123"


def test_init_refuses_overwrite_without_force(runner: CliRunner, tmp_path: Path) -> None:
    """Re-running init without --force fails; with --force succeeds."""
    target = tmp_path / "haru-config"
    run_init(runner, target)

    result = runner.invoke(cli, [*INIT_ARGS, "--guardrail-id", "", "--dir", str(target)])
    assert result.exit_code == 1
    assert "--force" in result.output

    run_init(runner, target, "--force")


def test_config_show(runner: CliRunner, tmp_path: Path) -> None:
    """config show prints the resolved path and a secret-free summary."""
    target = tmp_path / "haru-config"
    run_init(runner, target)

    result = runner.invoke(cli, ["config", "show", "--config", str(target / "haru.yaml")])

    assert result.exit_code == 0, result.output
    assert str(target / "haru.yaml") in result.output
    assert "sonnet-5 (default)" in result.output
    assert "researcher" in result.output
    assert "Guardrails:   disabled" in result.output


def test_config_show_without_any_config(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config show without any config gives the init hint, not a traceback."""
    monkeypatch.delenv("HARU_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 1
    assert "haru config init" in result.output
    assert "Traceback" not in result.output


def test_agents_command_lists_agents(runner: CliRunner, tmp_path: Path) -> None:
    """haru agents lists each agent with its model and prompt."""
    target = tmp_path / "haru-config"
    run_init(runner, target)

    result = runner.invoke(cli, ["agents", "--config", str(target / "haru.yaml")])

    assert result.exit_code == 0, result.output
    for name in ("supervisor", "researcher", "writer"):
        assert name in result.output
    assert "model=opus-5" in result.output
    assert "Default orchestration pattern: supervisor" in result.output


def test_login_without_config_is_clean(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """haru login without any config exits cleanly with the init hint."""
    monkeypatch.delenv("HARU_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["login"])

    assert result.exit_code == 1
    assert "haru config init" in result.output
    assert "Traceback" not in result.output
