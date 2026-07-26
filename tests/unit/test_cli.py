"""Tests for the haru Click command group."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from click.testing import CliRunner
from rich.console import Console

from haru.auth.sso import ClientRegistration, SsoToken
from haru.cli import cli
from haru.commands.chat import run_chat
from haru.config.schema import HaruConfig
from haru.errors import AuthExpiredError, ConfigError


class FakeAgent:
    """Agent stand-in yielding a deterministic stream of events."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.prompts: list[str] = []

    def stream_async(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        self.prompts.append(prompt)

        async def _generate() -> AsyncIterator[dict[str, Any]]:
            for event in self._events:
                yield event

        return _generate()


def hello_events(stop_reason: str = "end_turn") -> list[dict[str, Any]]:
    """A two-chunk streamed answer followed by a result event."""
    return [
        {"data": "Hello "},
        {"data": "world"},
        {"result": SimpleNamespace(stop_reason=stop_reason)},
    ]


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


def make_config(tmp_path: Path) -> Path:
    """Write a base config file and return its path."""
    config_path = tmp_path / "haru.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")
    return config_path


def test_run_command_streams_answer(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """``haru run`` prints the full answer and exits 0."""
    mocker.patch("haru.commands.run.build_boto3_session")
    agent = FakeAgent(hello_events())
    mocker.patch("haru.commands.run.build_agent", return_value=agent)

    result = runner.invoke(cli, ["run", "say hi", "--config", str(make_config(tmp_path))])

    assert result.exit_code == 0
    assert "Hello world" in result.output
    assert agent.prompts == ["say hi"]


def test_run_command_surfaces_guardrail(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """A guardrail-intervened result produces a visible warning."""
    mocker.patch("haru.commands.run.build_boto3_session")
    agent = FakeAgent(hello_events(stop_reason="guardrail_intervened"))
    mocker.patch("haru.commands.run.build_agent", return_value=agent)

    result = runner.invoke(cli, ["run", "say hi", "--config", str(make_config(tmp_path))])

    assert result.exit_code == 0
    assert "Guardrail intervened" in result.output


def test_run_command_auth_error_is_clean(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """Auth failures exit non-zero with a clean message, not a traceback."""
    mocker.patch(
        "haru.commands.run.build_boto3_session",
        side_effect=AuthExpiredError("No cached SSO token; run 'haru login'"),
    )

    result = runner.invoke(cli, ["run", "say hi", "--config", str(make_config(tmp_path))])

    assert result.exit_code == 1
    assert "haru login" in result.output
    assert "Traceback" not in result.output


def test_chat_command_streams_and_exits(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """``haru chat`` streams a response and leaves cleanly on 'exit'."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    agent = FakeAgent(hello_events())
    mocker.patch("haru.commands.chat.build_agent", return_value=agent)

    result = runner.invoke(
        cli, ["chat", "--config", str(make_config(tmp_path))], input="say hi\nexit\n"
    )

    assert result.exit_code == 0
    assert "Hello world" in result.output
    assert "Goodbye" in result.output
    assert agent.prompts == ["say hi"]


def test_chat_keyboard_interrupt_is_clean(mocker: Any) -> None:
    """Ctrl-C at the prompt ends the chat loop gracefully."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    mocker.patch("haru.commands.chat.build_agent", return_value=FakeAgent(hello_events()))
    config = HaruConfig.model_validate(
        {
            "app": {"name": "haru"},
            "auth": {
                "sso": {
                    "start_url": "https://example.awsapps.com/start",
                    "sso_region": "us-east-1",
                    "account_id_env": "HARU_AWS_ACCOUNT_ID",
                    "role_name": "HaruBedrockInvoke",
                },
                "bedrock_region": "us-east-1",
            },
        }
    )
    console = Console(record=True, width=100)

    def interrupt(_: str) -> str:
        raise KeyboardInterrupt

    run_chat(config, None, console=console, read_input=interrupt)

    assert "Goodbye" in console.export_text()


def test_chat_skips_blank_lines(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """Blank input lines are ignored rather than sent to the agent."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    agent = FakeAgent(hello_events())
    mocker.patch("haru.commands.chat.build_agent", return_value=agent)

    result = runner.invoke(
        cli, ["chat", "--config", str(make_config(tmp_path))], input="\n\nquit\n"
    )

    assert result.exit_code == 0
    assert agent.prompts == []


CHAT_CONFIG = (
    BASE_CONFIG
    + """\
models:
  default_model: fast
  models:
    fast:
      model_id: anthropic.a
      region: us-east-1
      max_tokens: 1024
      temperature: 0.2
    deep:
      model_id: anthropic.b
      region: us-east-1
      max_tokens: 2048
      temperature: 0.5
agents:
  agents:
    writer:
      model: deep
"""
)


def make_chat_config(tmp_path: Path) -> Path:
    """Write a chat config with models and agents; return its path."""
    config_path = tmp_path / "haru.yaml"
    config_path.write_text(CHAT_CONFIG, encoding="utf-8")
    return config_path


def test_chat_slash_listing_and_help(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """/help, /model, and /agent list commands, models, and agents."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    mocker.patch(
        "haru.commands.chat.build_agent",
        side_effect=lambda *a, **k: FakeAgent(hello_events()),
    )

    result = runner.invoke(
        cli,
        ["chat", "--config", str(make_chat_config(tmp_path))],
        input="/help\n/model\n/agent\n/bogus\nexit\n",
    )

    assert result.exit_code == 0, result.output
    assert "/model <name>" in result.output
    assert "fast" in result.output
    assert "(default, active)" in result.output
    assert "writer" in result.output
    assert "model=deep" in result.output
    assert "Unknown command /bogus" in result.output


def test_chat_slash_switch_model_and_agent(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """/model and /agent rebuild the agent with the selected target."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    build_agent = mocker.patch(
        "haru.commands.chat.build_agent",
        side_effect=lambda *a, **k: FakeAgent(hello_events()),
    )

    result = runner.invoke(
        cli,
        ["chat", "--config", str(make_chat_config(tmp_path))],
        input="/model deep\n/agent writer\nexit\n",
    )

    assert result.exit_code == 0, result.output
    assert "Switched to model 'deep' (conversation reset)." in result.output
    assert "Switched to agent 'writer' (conversation reset)." in result.output
    model_call = build_agent.call_args_list[1]
    assert model_call.args[1] is None
    assert model_call.kwargs["model_name"] == "deep"
    agent_call = build_agent.call_args_list[2]
    assert agent_call.args[1] == "writer"
    assert agent_call.kwargs["model_name"] is None


def test_chat_slash_switch_failure_keeps_agent(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """A failed switch reports the error and keeps the current agent."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    good_agent = FakeAgent(hello_events())

    def agent_factory(config: Any, name: Any, *a: Any, **k: Any) -> FakeAgent:
        if name == "ghost":
            raise ConfigError("Unknown agent 'ghost'")
        return good_agent

    mocker.patch("haru.commands.chat.build_agent", side_effect=agent_factory)

    result = runner.invoke(
        cli,
        ["chat", "--config", str(make_chat_config(tmp_path))],
        input="/agent ghost\nsay hi\nexit\n",
    )

    assert result.exit_code == 0, result.output
    assert "Unknown agent 'ghost'" in result.output
    assert "Hello world" in result.output


def test_chat_session_id_builds_session_manager(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """--session-id wires a session manager into the agent build."""
    mocker.patch("haru.commands.chat.build_boto3_session")
    build_manager = mocker.patch(
        "haru.commands.chat.build_session_manager", return_value="manager-obj"
    )
    build_agent = mocker.patch(
        "haru.commands.chat.build_agent", return_value=FakeAgent(hello_events())
    )

    result = runner.invoke(
        cli,
        ["chat", "--config", str(make_config(tmp_path)), "--session-id", "chat-9"],
        input="exit\n",
    )

    assert result.exit_code == 0
    assert build_manager.call_args.args[1] == "chat-9"
    assert build_agent.call_args.kwargs["session_manager"] == "manager-obj"


def test_session_list_command(runner: CliRunner, tmp_path: Path) -> None:
    """``haru session list`` prints stored ids from the configured directory."""
    sessions_dir = tmp_path / "state"
    (sessions_dir / "session_alpha").mkdir(parents=True)
    (sessions_dir / "session_beta").mkdir()
    config_path = tmp_path / "haru.yaml"
    config_path.write_text(
        BASE_CONFIG + f'sessions:\n  backend: file\n  storage_dir: "{sessions_dir}"\n',
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["session", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert result.output.splitlines() == ["alpha", "beta"]


def test_session_list_empty(runner: CliRunner, tmp_path: Path) -> None:
    """No stored sessions is reported plainly."""
    config_path = tmp_path / "haru.yaml"
    config_path.write_text(
        BASE_CONFIG + f'sessions:\n  backend: file\n  storage_dir: "{tmp_path / "none"}"\n',
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["session", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "No stored sessions" in result.output


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
