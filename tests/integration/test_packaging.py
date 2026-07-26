"""Packaging smoke tests: the installed distribution exposes the entry point."""

from importlib.metadata import entry_points, metadata

import pytest


@pytest.mark.integration
def test_console_script_entry_point_loads() -> None:
    """The haru console script resolves to a callable entry point."""
    (entry,) = entry_points(group="console_scripts", name="haru")
    assert entry.value == "haru.__main__:main"
    main = entry.load()
    assert callable(main)


@pytest.mark.integration
def test_distribution_metadata() -> None:
    """The distribution carries the expected name and license."""
    meta = metadata("haru-cli")
    assert meta["Name"] == "haru-cli"
    assert meta["License-Expression"] == "Apache-2.0"
