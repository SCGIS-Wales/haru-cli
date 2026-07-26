"""Tests for steering prompt loading, composition, and resolution."""

from pathlib import Path

import pytest

from haru.errors import ConfigError
from haru.steering.prompts import load_prompts, resolve_prompt

REPO_ROOT = Path(__file__).resolve().parents[2]


def write_prompts(root: Path) -> None:
    """Write a base prompt and a role overlay."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "base.md").write_text("Shared house rules.\n", encoding="utf-8")
    (root / "researcher.md").write_text("\nResearch carefully.\n\n", encoding="utf-8")


def test_load_prompts(tmp_path: Path) -> None:
    """Prompts load keyed by file stem, with surrounding whitespace stripped."""
    write_prompts(tmp_path)
    prompts = load_prompts(tmp_path)
    assert prompts == {"base": "Shared house rules.", "researcher": "Research carefully."}


def test_load_prompts_ignores_non_markdown(tmp_path: Path) -> None:
    """Only *.md files are treated as prompts."""
    write_prompts(tmp_path)
    (tmp_path / "notes.txt").write_text("not a prompt", encoding="utf-8")
    assert "notes" not in load_prompts(tmp_path)


def test_load_prompts_missing_directory(tmp_path: Path) -> None:
    """A missing prompts directory raises ConfigError naming the path."""
    with pytest.raises(ConfigError, match="absent"):
        load_prompts(tmp_path / "absent")


def test_resolve_single_prompt(tmp_path: Path) -> None:
    """A plain ref resolves to that prompt's text."""
    write_prompts(tmp_path)
    assert resolve_prompt("researcher", load_prompts(tmp_path)) == "Research carefully."


def test_resolve_composed_prompt(tmp_path: Path) -> None:
    """A composite ref concatenates base and overlay in order."""
    write_prompts(tmp_path)
    resolved = resolve_prompt("base+researcher", load_prompts(tmp_path))
    assert resolved == "Shared house rules.\n\nResearch carefully."


def test_resolve_missing_ref(tmp_path: Path) -> None:
    """A missing ref raises ConfigError listing available prompts."""
    write_prompts(tmp_path)
    with pytest.raises(ConfigError, match=r"phantom.*base, researcher"):
        resolve_prompt("phantom", load_prompts(tmp_path))


def test_resolve_partially_missing_composition(tmp_path: Path) -> None:
    """A composite ref with one missing part fails, naming the missing part."""
    write_prompts(tmp_path)
    with pytest.raises(ConfigError, match="phantom"):
        resolve_prompt("base+phantom", load_prompts(tmp_path))


@pytest.mark.parametrize("ref", ["", "+", "base+", "+researcher"])
def test_malformed_refs(tmp_path: Path, ref: str) -> None:
    """Empty refs and dangling separators are rejected."""
    write_prompts(tmp_path)
    with pytest.raises(ConfigError, match="Malformed"):
        resolve_prompt(ref, load_prompts(tmp_path))


def test_repo_prompts_cover_agent_refs() -> None:
    """Every system_prompt_ref in the checked-in config has a prompt file."""
    prompts = load_prompts(REPO_ROOT / "config" / "prompts")
    assert {"supervisor", "researcher", "writer"} <= set(prompts)
