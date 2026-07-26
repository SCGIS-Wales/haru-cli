"""Tests for session manager construction and persistence."""

import json
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from pydantic import ValidationError
from strands.session import FileSessionManager, S3SessionManager

from haru.config.schema import HaruConfig
from haru.sessions.manager import build_session_manager, list_sessions

BASE: dict[str, Any] = {
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


def make_config(sessions: dict[str, Any] | None) -> HaruConfig:
    """Build a config with the given sessions section."""
    payload = dict(BASE)
    if sessions is not None:
        payload["sessions"] = sessions
    return HaruConfig.model_validate(payload)


def file_config(tmp_path: Path) -> HaruConfig:
    """A file-backend config rooted in tmp_path."""
    return make_config({"backend": "file", "storage_dir": str(tmp_path / "sessions")})


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake AWS credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def test_file_session_persists_and_restores(tmp_path: Path) -> None:
    """A file session survives a 'process restart' (a fresh manager)."""
    config = file_config(tmp_path)

    first = build_session_manager(config, "chat-1")
    assert isinstance(first, FileSessionManager)

    restored = build_session_manager(config, "chat-1")
    session = restored.read_session("chat-1")
    assert session is not None
    assert session.session_id == "chat-1"


def test_file_sessions_use_project_local_dir(tmp_path: Path) -> None:
    """Sessions land in the configured project-local directory, not tempdir."""
    config = file_config(tmp_path)
    build_session_manager(config, "chat-1")
    assert (tmp_path / "sessions" / "session_chat-1").is_dir()


def test_default_backend_is_project_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a sessions section, the default is ./.haru/sessions."""
    monkeypatch.chdir(tmp_path)
    manager = build_session_manager(make_config(None), "chat-1")
    assert isinstance(manager, FileSessionManager)
    assert (tmp_path / ".haru" / "sessions" / "session_chat-1").is_dir()


def test_list_file_sessions(tmp_path: Path) -> None:
    """list_sessions returns stored ids sorted."""
    config = file_config(tmp_path)
    build_session_manager(config, "beta")
    build_session_manager(config, "alpha")
    assert list_sessions(config) == ["alpha", "beta"]


def test_list_file_sessions_empty(tmp_path: Path) -> None:
    """A missing storage directory lists as no sessions."""
    assert list_sessions(file_config(tmp_path)) == []


def test_s3_backend_requires_bucket() -> None:
    """An s3 backend without a bucket fails validation."""
    with pytest.raises(ValidationError, match="bucket"):
        make_config({"backend": "s3"})


@pytest.mark.usefixtures("aws_credentials")
@mock_aws
def test_s3_backend_constructs() -> None:
    """An s3 backend builds an S3SessionManager against the configured bucket."""
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="haru-sessions")
    config = make_config(
        {
            "backend": "s3",
            "bucket": "haru-sessions",
            "prefix": "haru/sessions",
            "region": "us-east-1",
        }
    )

    manager = build_session_manager(
        config, "chat-1", boto_session=boto3.Session(region_name="us-east-1")
    )

    assert isinstance(manager, S3SessionManager)
    restored = manager.read_session("chat-1")
    assert restored is not None
    assert restored.session_id == "chat-1"


@pytest.mark.usefixtures("aws_credentials")
@mock_aws
def test_list_s3_sessions() -> None:
    """list_sessions extracts session ids from S3 keys under the prefix."""
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="haru-sessions")
    for key in (
        "haru/sessions/session_alpha/session.json",
        "haru/sessions/session_beta/agents/agent_x/agent.json",
        "unrelated/key.json",
    ):
        client.put_object(Bucket="haru-sessions", Key=key, Body=json.dumps({}))
    config = make_config(
        {
            "backend": "s3",
            "bucket": "haru-sessions",
            "prefix": "haru/sessions",
            "region": "us-east-1",
        }
    )

    ids = list_sessions(config, boto_session=boto3.Session(region_name="us-east-1"))

    assert ids == ["alpha", "beta"]
