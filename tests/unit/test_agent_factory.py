"""Tests for the (single-agent) Strands agent factory."""

from typing import Any

import pytest

from haru.agents.factory import build_agent
from haru.config.schema import HaruConfig
from haru.errors import ConfigError

CONFIG_PAYLOAD: dict[str, Any] = {
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
    "models": {
        "default_model": "fast",
        "models": {
            "fast": {
                "model_id": "anthropic.a",
                "region": "us-east-1",
                "max_tokens": 1024,
                "temperature": 0.2,
            },
            "deep": {
                "model_id": "anthropic.b",
                "region": "us-east-1",
                "max_tokens": 2048,
                "temperature": 0.5,
            },
        },
    },
    "agents": {"agents": {"writer": {"model": "deep"}}},
}


def make_config() -> HaruConfig:
    """Build a config with a model catalogue and one agent."""
    return HaruConfig.model_validate(CONFIG_PAYLOAD)


def test_default_agent_uses_default_model(mocker: Any) -> None:
    """No agent name builds an agent on the default model."""
    agent_cls = mocker.patch("haru.agents.factory.Agent")
    build_model = mocker.patch("haru.agents.factory.build_model", return_value="model-obj")
    session = mocker.Mock()

    build_agent(make_config(), None, session)

    assert build_model.call_args.args[0].model_id == "anthropic.a"
    agent_cls.assert_called_once_with(model="model-obj")


def test_named_agent_uses_its_model(mocker: Any) -> None:
    """A named agent resolves its own model key."""
    mocker.patch("haru.agents.factory.Agent")
    build_model = mocker.patch("haru.agents.factory.build_model", return_value="model-obj")

    build_agent(make_config(), "writer", mocker.Mock())

    assert build_model.call_args.args[0].model_id == "anthropic.b"


def test_unknown_agent_raises(mocker: Any) -> None:
    """An unknown agent name raises ConfigError listing configured agents."""
    mocker.patch("haru.agents.factory.Agent")
    with pytest.raises(ConfigError, match=r"ghost.*writer"):
        build_agent(make_config(), "ghost", mocker.Mock())
