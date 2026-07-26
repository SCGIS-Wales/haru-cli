"""Typed exceptions for haru-cli.

Third-party errors are converted to these types at module boundaries so that
callers only ever handle haru-cli exceptions.
"""


class HaruError(Exception):
    """Base class for all haru-cli errors."""


class ConfigError(HaruError):
    """Raised when configuration is missing, malformed, or insecure."""


class AuthError(HaruError):
    """Raised when authentication fails or is misconfigured."""


class AuthExpiredError(AuthError):
    """Raised when cached credentials are missing or expired; re-login is required."""
