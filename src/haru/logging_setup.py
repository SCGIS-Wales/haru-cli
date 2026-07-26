"""Configure application logging from YAML settings and the ``--debug`` flag.

Logging is deliberately conservative: haru never passes credentials to a
logger, and a handler-level redaction filter masks anything that looks like a
token as defence in depth.

AWS wire logging is never enabled. The boto3/botocore loggers are *capped* at
INFO even under ``--debug``, because botocore logs full request and response
headers and bodies at DEBUG. That cap is a security control, not an oversight:
visibility into AWS calls comes instead from haru's own DEBUG lines, emitted
through :func:`log_aws_call` and :func:`log_aws_error` at each call site.
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

    ``debug`` forces DEBUG for haru's own loggers. The AWS SDK loggers are
    held at INFO even then, so ``--debug`` can never turn on botocore's
    request/response logging; see the module docstring.
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
    # Capped at INFO deliberately: botocore logs headers and bodies at DEBUG.
    aws_level = logging.INFO if debug else logging.WARNING
    for name in _AWS_LOGGERS:
        logging.getLogger(name).setLevel(aws_level)


def log_aws_call(logger: logging.Logger, api: str, **fields: object) -> None:
    """Log an AWS API call at DEBUG.

    Pass identifiers, counts, regions, booleans, and expiry timestamps only.
    Never pass a token, secret, credential, authorization code, PKCE verifier,
    or prompt text: the handler-level redaction filter is defence in depth,
    not permission. Fields that are None are omitted.
    """
    present = [(key, value) for key, value in fields.items() if value is not None]
    # Keys are developer-supplied literals; values stay as %s args so the
    # handler-level redaction filter still sees them.
    template = "aws %s" + "".join(f" {key}=%s" for key, _ in present)
    logger.debug(template, api, *(value for _, value in present))


def log_aws_error(logger: logging.Logger, api: str, code: str | None) -> None:
    """Log an AWS failure at DEBUG as an error code only, never the response."""
    logger.debug("aws %s failed code=%s", api, code or "unknown")


def _file_handler(path: str) -> logging.Handler:
    """Open a log file with 0600 permissions (logs may contain prompt text)."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return logging.StreamHandler(os.fdopen(descriptor, "a", encoding="utf-8"))


def _level_of(name: str) -> int:
    """Map a configured level name to its logging constant (INFO when unknown)."""
    level = logging.getLevelNamesMapping().get(name.upper())
    return level if level is not None else logging.INFO
