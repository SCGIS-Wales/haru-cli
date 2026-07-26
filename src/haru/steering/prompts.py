"""Load and resolve reusable system prompts.

Prompts live in versioned markdown files under ``config/prompts/`` and are
referenced by agents via ``system_prompt_ref`` — never inlined in code. A ref
may compose several prompts with ``+`` (for example ``base+researcher``),
which concatenates them in order: a shared base plus a role overlay.
"""

from pathlib import Path

from haru.errors import ConfigError

DEFAULT_PROMPTS_ROOT = Path("config") / "prompts"

_COMPOSE_SEPARATOR = "+"


def load_prompts(root: Path) -> dict[str, str]:
    """Load every ``*.md`` prompt under ``root``, keyed by file stem."""
    if not root.is_dir():
        raise ConfigError(f"Prompts directory not found: {root}")
    return {
        path.stem: path.read_text(encoding="utf-8").strip() for path in sorted(root.glob("*.md"))
    }


def resolve_prompt(ref: str, prompts: dict[str, str]) -> str:
    """Resolve ``ref`` (optionally composite, ``a+b``) to prompt text.

    Raises ConfigError when any referenced prompt is missing.
    """
    parts = [part.strip() for part in ref.split(_COMPOSE_SEPARATOR)]
    if not all(parts):
        raise ConfigError(f"Malformed prompt reference {ref!r}")
    missing = [part for part in parts if part not in prompts]
    if missing:
        available = ", ".join(sorted(prompts)) or "none"
        raise ConfigError(
            f"Unknown prompt reference(s) {', '.join(missing)!s} in {ref!r};"
            f" available prompts: {available}"
        )
    return "\n\n".join(prompts[part] for part in parts)
