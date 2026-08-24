"""One version string, so a citation trail cannot understate the engine that ran."""

from __future__ import annotations

import importlib.metadata

import atobenchmark


def test_module_version_matches_distribution_metadata() -> None:
    # The module attribute is the single source: hatchling reads it out of
    # atobenchmark/__init__.py at build time, so the installed distribution
    # metadata can only disagree with an install that predates the current
    # source. Published 0.1.3 carried __version__ = "0.1.2", and downstream
    # callers report that string as the engine version beside their numbers.
    assert atobenchmark.__version__ == importlib.metadata.version("ato-benchmark-compare")
