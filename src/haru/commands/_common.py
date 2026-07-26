"""Shared configuration loading for Click commands.

``cli`` configures logging before any configuration is read, so ``--debug``
still works when the configuration is broken. That bootstrap call cannot know
``logging.yaml``, so every command re-applies logging once its configuration is
loaded; :func:`load_cli_config` is the single place that does both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from haru.config import HaruConfig, load_config, load_logging, resolve_config_path
from haru.logging_setup import configure_logging

if TYPE_CHECKING:
    from pathlib import Path


def load_cli_config(
    config_path: Path | None, *, with_includes: bool = True
) -> tuple[Path, HaruConfig]:
    """Resolve and load configuration, then apply its logging settings.

    Returns the resolved path alongside the configuration. The global
    ``--debug`` flag still wins over the configured level.
    """
    resolved = resolve_config_path(config_path)
    config = load_config(resolved, with_includes=with_includes)
    configure_logging(load_logging(config, resolved.parent), debug=debug_flag())
    return resolved, config


def debug_flag() -> bool:
    """Read the root ``--debug`` flag from the ambient Click context.

    Using the ambient context keeps every command's signature unchanged; the
    flag is stored on the root context object by ``cli``.
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return False
    obj = ctx.find_root().obj
    return bool(obj.get("debug")) if isinstance(obj, dict) else False
