"""Typed exceptions for haru-cli.

Third-party errors are converted to these types at module boundaries so that
callers only ever handle haru-cli exceptions.
"""


class HaruError(Exception):
    """Base class for all haru-cli errors."""


class ConfigError(HaruError):
    """Raised when configuration is missing, malformed, or insecure."""
