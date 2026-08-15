from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from here_to_slay.content import ContentRegistry, load_pack
from here_to_slay.core import (
    Decision,
    DecisionLog,
    GameState,
    Interpreter,
    PlayerId,
    Request,
    ScriptedSource,
    Status,
    drive,
    new_game,
)
from here_to_slay.core.context import EffectContext

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


@pytest.fixture(scope="session")
def trigger_content() -> ContentRegistry:
    """Base rules + Heroes that exist only to subscribe to something."""
    return load_pack(FIXTURES / "triggers", search_paths=[PROJECT_ROOT / "data"])


@pytest.fixture
def table_state(table_content: ContentRegistry) -> GameState:
    """A dealt four-player game on the shipping rules."""
    return new_game(table_content, ["Ann", "Bob", "Cid", "Dee"], seed="phase3")


@pytest.fixture
def trigger_state(trigger_content: ContentRegistry) -> GameState:
    return new_game(trigger_content, ["Ann", "Bob"], seed="phase3")


@dataclass(slots=True)
class EffectRun:
    """What running one effect tree produced — the tests' unit of work."""

    status: Status
    requests: tuple[Request, ...]
    events: tuple[str, ...]
    log: DecisionLog

    @property
    def asked(self) -> int:
        return len(self.requests)

    def emitted(self, name: str) -> int:
        return self.events.count(name)


RunEffect = Callable[..., EffectRun]


@pytest.fixture
def run_effect() -> RunEffect:
    """Run an effect tree to completion, answering from a scripted list.

    This is the shape every effect test wants: build a state, hand over a node
    and the decisions a player would have made, get back what was asked and a
    log that can be replayed.
    """

    def _run(
        state: GameState,
        node: Any,
        *,
        decisions: Sequence[Decision] = (),
        player: PlayerId | str | None = None,
        source: str | None = None,
        bindings: dict[str, Any] | None = None,
        log: DecisionLog | None = None,
    ) -> EffectRun:
        record = log if log is not None else DecisionLog.for_game(state)
        ctx = EffectContext.root(
            state,
            player=PlayerId(player) if player else None,
            source=source,  # type: ignore[arg-type]
            bindings=bindings,
        )
        script = ScriptedSource(decisions)
        status = drive(Interpreter(state, log=record), ctx.run(node), script)
        return EffectRun(
            status=status,
            requests=tuple(script.seen),
            events=tuple(event.name for event in ctx.execution.history),
            log=record,
        )

    return _run


@pytest.fixture(autouse=True)
def _stable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the things that make CLI output vary between machines.

    * cwd — error paths are reported relative to it
    * console width — rich wraps table cells, which would split long paths
    """
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setenv("COLUMNS", "240")
