"""Configure application logging from YAML settings and the ``--debug`` flag.

Logging is deliberately conservative: haru never passes credentials to a
logger, and a handler-level redaction filter masks anything that looks like a
token as defence in depth. AWS wire logging (request/response bodies and
headers) is never enabled; ``--debug`` raises botocore only to INFO, which
reports operation names, endpoints, retries, and error codes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from haru.config.schema import LoggingConfig

if TYPE_CHECKING:
    from typing import TextIO

_HANDLER_FLAG = "_haru_handler"
_REDACTED = "***redacted***"
_TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_AWS_LOGGERS = ("boto3", "botocore", "urllib3", "s3transfer")

# Key-carrying secrets: keep the key so logs stay readable, mask the value.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Headers first, and to end of line: an "Authorization: Bearer <token>" value
    # must be masked whole, not just the scheme word.
    re.compile(r"(?im)^(\s*(?:authorization|x-amz-security-token|proxy-authorization)\s*:\s*).+$"),
    re.compile(
        r'(?i)("?\b(?:access_?token|refresh_?token|client_?secret|secret_?access_?key'
        r"|session_?token|access_?key_?id|code_?verifier|code_?challenge|password"
        r'|authorization)\b"?\s*[:=]\s*"?)([^"\s,;}&]+)'
    ),
    re.compile(r"([?&](?:code|state|access_token|id_token)=)([^&\s]+)"),
    re.compile(r"\b((?:AKIA|ASIA))([A-Z0-9]{16})\b"),
)


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per record (structured, audit-friendly logs)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def redact(text: str) -> str:
    """Mask anything that looks like a credential in ``text``."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    return text


def _redaction_filter(record: logging.LogRecord) -> bool:
    """Collapse the record to a redacted message before any handler formats it."""
    record.msg = redact(record.getMessage())
    record.args = ()
    return True


def configure_logging(
    cfg: LoggingConfig | None,
    *,
    debug: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Install haru's log handlers; safe to call repeatedly.

    ``debug`` forces DEBUG for haru's own loggers and raises AWS SDK logging
    to INFO (operation names and error codes only, never bodies).
    """
    settings = cfg if cfg is not None else LoggingConfig()
    level = logging.DEBUG if debug else _level_of(settings.level)

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]:
        root.removeHandler(handler)
        handler.close()

    formatter: logging.Formatter = (
        _JsonFormatter() if settings.format == "json" else logging.Formatter(_TEXT_FORMAT)
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(stream)]
    if settings.file:
        handlers.append(_file_handler(settings.file))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_redaction_filter)
        setattr(handler, _HANDLER_FLAG, True)
        root.addHandler(handler)

    root.setLevel(level)
    aws_level = logging.INFO if debug else logging.WARNING
    for name in _AWS_LOGGERS:
        logging.getLogger(name).setLevel(aws_level)


def _file_handler(path: str) -> logging.Handler:
    """Open a log file with 0600 permissions (logs may contain prompt text)."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return logging.StreamHandler(os.fdopen(descriptor, "a", encoding="utf-8"))


def _level_of(name: str) -> int:
    """Map a configured level name to its logging constant (INFO when unknown)."""
    level = logging.getLevelNamesMapping().get(name.upper())
    return level if level is not None else logging.INFO
