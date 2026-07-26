"""Registry mapping configuration names to built-in strands_tools implementations.

The registry is a curated allowlist: configuration can only reference tools
listed here, never import arbitrary modules. Destructive or shell-executing
tools are deliberately excluded.
"""

import importlib
from collections.abc import Sequence
from typing import Any

from haru.errors import ConfigError

_BUILTIN_TOOL_MODULES: dict[str, str] = {
    "calculator": "strands_tools.calculator",
    "current_time": "strands_tools.current_time",
    "file_read": "strands_tools.file_read",
    "http_request": "strands_tools.http_request",
    "sleep": "strands_tools.sleep",
}


def available_builtin_tools() -> list[str]:
    """Return the names configuration may reference, sorted."""
    return sorted(_BUILTIN_TOOL_MODULES)


def resolve_builtin_tools(names: Sequence[str]) -> list[Any]:
    """Resolve configured tool names to their strands_tools implementations.

    Raises ConfigError for names outside the allowlist.
    """
    tools: list[Any] = []
    for name in names:
        module_path = _BUILTIN_TOOL_MODULES.get(name)
        if module_path is None:
            available = ", ".join(available_builtin_tools())
            raise ConfigError(f"Unknown built-in tool {name!r}; available tools: {available}")
        module = importlib.import_module(module_path)
        tools.append(getattr(module, name, module))
    return tools
