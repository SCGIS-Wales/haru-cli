"""Tests for the haru module entry point."""

import sys

import pytest

from haru.__main__ import main


def test_main_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` dispatches to the CLI and exits 0 for ``--version``."""
    monkeypatch.setattr(sys, "argv", ["haru", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
