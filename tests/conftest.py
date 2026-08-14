from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(autouse=True)
def _stable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the things that make CLI output vary between machines.

    * cwd — error paths are reported relative to it
    * console width — rich wraps table cells, which would split long paths
    """
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setenv("COLUMNS", "240")
