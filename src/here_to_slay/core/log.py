"""The decision log, and replaying one.

``Game = f(content_hash, seed, [decision₀, decision₁, ...])``

Because the engine has no ambient randomness and no I/O, that equation holds
exactly — and everything expensive comes free from it (``architecture_notes.md
§7``): replay and undo, network play (send decisions, not state), golden
regression tests per card, and a bug report that is a seed plus a list.

The log stores what was *asked* as well as what was answered. Storing only the
answers would replay fine right up until the content changed, and then silently
apply "option 2" to a different menu. With the request kind recorded, a
divergent replay stops at the first decision that no longer fits and says so.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from here_to_slay.core.errors import ReplayError
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.interpreter import (
    Decision,
    DecisionSource,
    Flow,
    Interpreter,
    Request,
    Status,
    drive,
)
from here_to_slay.core.rng import seed_from
from here_to_slay.core.state import GameState

LOG_FORMAT = 1


@dataclass(frozen=True, slots=True)
class LoggedDecision:
    """One answered question."""

    index: int
    request: str
    requester: PlayerId
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    prompt: str = ""

    def decision(self) -> Decision:
        return Decision.from_data(self.kind, dict(self.data))

    def as_data(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "request": self.request,
            "requester": self.requester,
            "kind": self.kind,
            "data": dict(self.data),
            "prompt": self.prompt,
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> LoggedDecision:
        return cls(
            index=int(data["index"]),
            request=str(data["request"]),
            requester=PlayerId(str(data["requester"])),
            kind=str(data["kind"]),
            data=dict(data.get("data") or {}),
            prompt=str(data.get("prompt", "")),
        )

    def __str__(self) -> str:
        return f"[{self.index}] {self.requester} {self.request} -> {self.kind}{self.data}"


@dataclass(slots=True)
class DecisionLog:
    """Everything needed to replay a game, and nothing else."""

    content_hash: str = ""
    seed: int | str = 0
    players: tuple[str, ...] = ()
    entries: list[LoggedDecision] = field(default_factory=list)
    format: int = LOG_FORMAT

    @classmethod
    def for_game(cls, state: GameState, players: Sequence[str] = ()) -> DecisionLog:
        """Start a log for a state that setup has just built."""
        return cls(
            content_hash=state.content_hash,
            seed=state.rng.seed,
            players=tuple(players) or tuple(player.name for player in state.players.values()),
        )

    # -- recording ---------------------------------------------------------

    def record(self, request: Request, decision: Decision) -> LoggedDecision:
        entry = LoggedDecision(
            index=len(self.entries),
            request=request.kind,
            requester=request.requester,
            kind=decision.kind,
            data=decision.as_data(),
            prompt=request.prompt,
        )
        self.entries.append(entry)
        return entry

    def decisions(self) -> tuple[Decision, ...]:
        return tuple(entry.decision() for entry in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterable[LoggedDecision]:  # type: ignore[override]
        return iter(self.entries)

    def truncated(self, count: int) -> DecisionLog:
        """The first ``count`` decisions — replaying this is "undo to step n"."""
        return DecisionLog(
            content_hash=self.content_hash,
            seed=self.seed,
            players=self.players,
            entries=list(self.entries[:count]),
            format=self.format,
        )

    # -- serialisation -----------------------------------------------------

    def as_data(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "content_hash": self.content_hash,
            "seed": self.seed,
            "players": list(self.players),
            "entries": [entry.as_data() for entry in self.entries],
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> DecisionLog:
        version = int(data.get("format", LOG_FORMAT))
        if version > LOG_FORMAT:
            raise ReplayError(
                f"log format {version} is newer than this engine understands ({LOG_FORMAT})"
            )
        return cls(
            content_hash=str(data.get("content_hash", "")),
            seed=data.get("seed", 0),
            players=tuple(data.get("players") or ()),
            entries=[LoggedDecision.from_data(entry) for entry in data.get("entries") or ()],
            format=version,
        )

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.as_data(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> DecisionLog:
        return cls.from_data(json.loads(text))

    def save(self, path: Path | str) -> Path:
        destination = Path(path)
        destination.write_text(self.to_json(), encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: Path | str) -> DecisionLog:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class LogSource(DecisionSource):
    """A :class:`DecisionSource` that answers from a log instead of a player."""

    def __init__(self, log: DecisionLog, *, start: int = 0) -> None:
        self.log = log
        self.index = start

    def answer(self, request: Request) -> Decision:
        if self.index >= len(self.log.entries):
            raise ReplayError(
                f"the log has {len(self.log.entries)} decision(s) but the game asked for "
                f"another: {request.kind} for '{request.requester}'"
            )
        entry = self.log.entries[self.index]
        if entry.request != request.kind:
            raise ReplayError(
                f"decision {self.index} was recorded for a '{entry.request}' request but "
                f"replay reached a '{request.kind}' — the content or the engine has changed"
            )
        if entry.requester != request.requester:
            raise ReplayError(
                f"decision {self.index} was '{entry.requester}'s, but replay is asking "
                f"'{request.requester}'"
            )
        self.index += 1
        return entry.decision()

    @property
    def exhausted(self) -> bool:
        return self.index >= len(self.log.entries)


def check_content(log: DecisionLog, state: GameState) -> None:
    """Refuse to replay a log against edited cards.

    A replay that quietly runs against different content is worse than one that
    refuses: it produces a plausible, wrong game and a bug report nobody can
    reproduce.
    """
    if log.content_hash and log.content_hash != state.content_hash:
        raise ReplayError(
            "this log was recorded against different content "
            f"(log {log.content_hash[:12]}…, game {state.content_hash[:12]}…)"
        )
    if seed_from(log.seed) != seed_from(state.rng.seed):
        raise ReplayError(f"this log was recorded with seed {log.seed!r}, not {state.rng.seed!r}")


def replay(log: DecisionLog, state: GameState, flow: Flow, *, verify: bool = True) -> Status:
    """Re-run ``flow`` against ``state``, taking every answer from ``log``."""
    if verify:
        check_content(log, state)
    return drive(Interpreter(state), flow, LogSource(log))


__all__ = [
    "DecisionLog",
    "LogSource",
    "LoggedDecision",
    "check_content",
    "replay",
]
