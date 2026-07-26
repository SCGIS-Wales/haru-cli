"""Tests for the haru Click command group."""

from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from haru.auth.sso import ClientRegistration, SsoToken
from haru.cli import cli

BASE_CONFIG = """\
app:
  name: haru
auth:
  sso:
    start_url: https://example.awsapps.com/start
    sso_region: us-east-1
    account_id_env: HARU_AWS_ACCOUNT_ID
    role_name: HaruBedrockInvoke
  bedrock_region: us-east-1
"""


def test_version(runner: CliRunner) -> None:
    """``haru --version`` exits 0 and reports the installed distribution version."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert version("haru-cli") in result.output
    assert "haru" in result.output


def test_login_command(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """``haru login`` runs the flow and reports the cache location, not the token."""
    config_path = tmp_path / "haru.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")
    now = datetime.now(UTC)
    token = SsoToken(
        access_token="access-abc",
        refresh_token=None,
        expires_at=now + timedelta(hours=1),
        registration=ClientRegistration(
            client_id="client-123", client_secret="client-secret", expires_at=now
        ),
    )
    run_login = mocker.patch("haru.commands.login.run_login", return_value=token)
    write_cache = mocker.patch(
        "haru.commands.login.write_token_cache", return_value=tmp_path / "cache.json"
    )

    result = runner.invoke(cli, ["login", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Login successful" in result.output
    assert "access-abc" not in result.output
    auth = run_login.call_args.args[0]
    assert auth.sso.start_url == "https://example.awsapps.com/start"
    write_cache.assert_called_once_with(token, auth.sso.start_url, "us-east-1")


def test_login_opener_echoes_url_and_opens_browser(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """The login opener prints the authorize URL and opens the browser."""
    config_path = tmp_path / "haru.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")
    browser_open = mocker.patch("haru.commands.login.webbrowser.open")
    mocker.patch("haru.commands.login.write_token_cache", return_value=tmp_path / "cache.json")

    def fake_run_login(auth: Any, *, opener: Any) -> Any:
        opener("https://oidc.us-east-1.amazonaws.com/authorize?x=1")
        return mocker.Mock()

    mocker.patch("haru.commands.login.run_login", side_effect=fake_run_login)

    result = runner.invoke(cli, ["login", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "https://oidc.us-east-1.amazonaws.com/authorize?x=1" in result.output
    browser_open.assert_called_once_with("https://oidc.us-east-1.amazonaws.com/authorize?x=1")
