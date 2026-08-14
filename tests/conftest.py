from __future__ import annotations

from pathlib import Path

import pytest

from here_to_slay.content import ContentRegistry, load_pack

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


# Registries are immutable, so one load per session is safe to share.


@pytest.fixture(scope="session")
def table_content() -> ContentRegistry:
    """Base rules + enough fixture cards to deal a real table."""
    return load_pack(FIXTURES / "table", search_paths=[PROJECT_ROOT / "data"])


@pytest.fixture(scope="session")
def small_content() -> ContentRegistry:
    """The 2-player fixture rule set: 2-card hands, a 1-monster row."""
    return load_pack(FIXTURES / "good")


@pytest.fixture(scope="session")
def base_content() -> ContentRegistry:
    """The shipping rule set — no cards until Phase 6."""
    return load_pack(PROJECT_ROOT / "data" / "base")


@pytest.fixture(autouse=True)
def _stable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the things that make CLI output vary between machines.

    * cwd — error paths are reported relative to it
    * console width — rich wraps table cells, which would split long paths
    """
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setenv("COLUMNS", "240")
