"""Shared fixtures: a synthetic snapshot in a temp dir and Settings pointed at it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fpl_analysis.config import load_settings

from .synthetic import build_snapshot


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def workspace(tmp_path_factory) -> Path:
    """One synthetic snapshot + DuckDB file shared by the whole test session (build is slow-ish)."""
    ws = tmp_path_factory.mktemp("fpl")
    build_snapshot(ws / "raw")
    return ws


@pytest.fixture(scope="session")
def settings(workspace, project_root):
    env = {
        "FPL_PATHS_RAW_DIR": str(workspace / "raw"),
        "FPL_PATHS_DUCKDB_PATH": str(workspace / "fpl.duckdb"),
        "FPL_PATHS_REPORTS_DIR": str(workspace / "reports"),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        s = load_settings(project_root / "config" / "settings.toml")
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return s


@pytest.fixture(scope="session")
def built(settings):
    """Run the full build once; tests then query the warehouse."""
    from fpl_analysis.store import build

    return build(settings)
