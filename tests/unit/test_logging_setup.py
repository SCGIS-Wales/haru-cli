"""Tests for logging configuration and credential redaction."""

import io
import json
import logging
import stat
from pathlib import Path

import pytest

from haru.auth.sso import _CallbackHandler
from haru.config.schema import LoggingConfig
from haru.logging_setup import configure_logging, log_aws_call, log_aws_error, redact


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    """Leave the root logger clean for other tests."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def emit(cfg: LoggingConfig | None, message: str, *args: object, debug: bool = False) -> str:
    """Configure logging against a buffer and emit one record."""
    stream = io.StringIO()
    configure_logging(cfg, debug=debug, stream=stream)
    logging.getLogger("haru.test").warning(message, *args)
    return stream.getvalue()


def test_text_format() -> None:
    """The text formatter includes level, logger, and message."""
    output = emit(LoggingConfig(format="text"), "hello world")
    assert "WARNING" in output
    assert "haru.test" in output
    assert "hello world" in output


def test_json_format() -> None:
    """The json formatter emits one parseable object per record."""
    output = emit(LoggingConfig(format="json"), "hello world")
    payload = json.loads(output.strip())
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "haru.test"
    assert payload["message"] == "hello world"


def test_level_honoured_and_debug_overrides() -> None:
    """Configured level applies; --debug forces DEBUG."""
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="ERROR", format="text"), stream=stream)
    logging.getLogger("haru.test").warning("suppressed")
    assert stream.getvalue() == ""

    stream = io.StringIO()
    configure_logging(LoggingConfig(level="ERROR", format="text"), debug=True, stream=stream)
    logging.getLogger("haru.test").debug("shown")
    assert "shown" in stream.getvalue()


def test_aws_logger_levels() -> None:
    """AWS SDK logging is capped at INFO even under --debug.

    This is a security control, not a formatting choice: botocore logs full
    request and response headers and bodies at DEBUG. Raising this cap would
    put credentials and prompt text into the log.
    """
    configure_logging(None, stream=io.StringIO())
    assert logging.getLogger("botocore").level == logging.WARNING

    configure_logging(None, debug=True, stream=io.StringIO())
    assert logging.getLogger("botocore").level == logging.INFO
    assert logging.getLogger("botocore").level != logging.DEBUG


def test_log_aws_call_shape() -> None:
    """An AWS call line names the API and its non-sensitive fields."""
    stream = io.StringIO()
    configure_logging(None, debug=True, stream=stream)
    log_aws_call(logging.getLogger("haru.test"), "sso.GetRoleCredentials", account_id="1", role="R")

    assert "aws sso.GetRoleCredentials account_id=1 role=R" in stream.getvalue()


def test_log_aws_call_omits_none_fields() -> None:
    """Absent fields are dropped rather than logged as None."""
    stream = io.StringIO()
    configure_logging(None, debug=True, stream=stream)
    log_aws_call(logging.getLogger("haru.test"), "sso.ListAccounts", count=3, region=None)

    output = stream.getvalue()
    assert "count=3" in output
    assert "region" not in output


def test_log_aws_error_logs_code_only() -> None:
    """A failure line carries the error code and nothing from the response."""
    stream = io.StringIO()
    configure_logging(None, debug=True, stream=stream)
    log_aws_error(logging.getLogger("haru.test"), "bedrock.ListFoundationModels", None)
    log_aws_error(logging.getLogger("haru.test"), "sts.GetCallerIdentity", "AccessDenied")

    output = stream.getvalue()
    assert "aws bedrock.ListFoundationModels failed code=unknown" in output
    assert "aws sts.GetCallerIdentity failed code=AccessDenied" in output


def test_aws_call_values_are_redacted() -> None:
    """Values pass through %s args, so the redaction filter still sees them."""
    stream = io.StringIO()
    configure_logging(None, debug=True, stream=stream)
    log_aws_call(logging.getLogger("haru.test"), "x", blob='{"accessToken": "aoaSECRETvalue"}')

    assert "aoaSECRETvalue" not in stream.getvalue()


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ('{"accessToken": "aoaAAAAsecretvalue"}', "aoaAAAAsecretvalue"),
        ('{"refreshToken": "aorAAAAsecretvalue"}', "aorAAAAsecretvalue"),
        ('{"clientSecret": "eyJraWQiOiJrZXkt"}', "eyJraWQiOiJrZXkt"),
        ('{"secretAccessKey": "abc123def456"}', "abc123def456"),
        ("Authorization: Bearer abc123def456", "abc123def456"),
        ("https://example.com/cb?code=authcode123&x=1", "authcode123"),
        ("key AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    ],
)
def test_redaction_patterns(message: str, secret: str) -> None:
    """Credential-shaped values are masked in log output."""
    assert secret not in redact(message)
    assert secret not in emit(LoggingConfig(format="text"), message)


def test_redaction_survives_interpolated_args() -> None:
    """Secrets passed as %s arguments are masked too."""
    output = emit(LoggingConfig(format="text"), "token=%s", '{"accessToken": "leaky-token-x"}')
    assert "leaky-token-x" not in output


def test_configure_is_idempotent() -> None:
    """Repeated configuration does not stack handlers."""
    for _ in range(3):
        configure_logging(None, stream=io.StringIO())
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_haru_handler", False)]
    assert len(ours) == 1


def test_file_handler_permissions(tmp_path: Path) -> None:
    """A configured log file is created with 0600 permissions."""
    log_file = tmp_path / "haru.log"
    configure_logging(LoggingConfig(format="text", file=str(log_file)), stream=io.StringIO())
    logging.getLogger("haru.test").warning("written")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert log_file.is_file()
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    assert "written" in log_file.read_text(encoding="utf-8")


def test_callback_logging_stays_silenced() -> None:
    """The OAuth callback handler must never log request URLs."""
    stream = io.StringIO()
    configure_logging(None, debug=True, stream=stream)
    assert _CallbackHandler.log_message(None, "%s", "/oauth/callback?code=secret") is None  # type: ignore[arg-type]
    assert stream.getvalue() == ""
