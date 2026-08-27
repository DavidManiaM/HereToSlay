"""Saving and loading a game — which is the decision log, and nothing else.

``architecture_notes.md §4`` settled the shape of this years of debugging ago:
a suspended generator stack is not serialisable, so a save can only be taken at
a point where nothing is mid-flight, and what it stores is not a board but the
*inputs* to one::

    Game = f(content_hash, seed, max_turns, [decision₀, decision₁, …])

Loading is therefore replaying. That is not a shortcut — it is the only version
that cannot drift: a snapshot of ``GameState`` would need every future card's
per-instance scratch, every flag a mod invents and every subscription the bus
holds, and would silently load a *different* game the day one of them was
forgotten. Replaying reruns the same engine over the same inputs, so a loaded
game is the game, or it refuses to load at all.

What this module adds on top of :class:`~here_to_slay.core.log.DecisionLog` is
the part a human needs: when it was saved, what it was called, which packs it
wants, and a summary the load screen can list without replaying anything.

The one cost is honest and worth naming: restoring a long game replays it, so
loading is O(decisions) rather than O(1). At a few hundred decisions of pure
Python that is milliseconds, and it buys a save file that cannot lie.
"""

from __future__ import annotations

import contextlib
import datetime
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EngineError, ReplayExhausted
from here_to_slay.core.log import DecisionLog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine

SAVE_FORMAT = 1

#: Suffix every save file gets, so a directory listing is unambiguous.
SAVE_SUFFIX = ".hts.json"


class SaveError(EngineError):
    """A game could not be saved, or a save file could not be loaded."""


def _now() -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class SaveSummary:
    """What a load screen shows without replaying a single decision.

    Derived at save time on purpose. Recomputing it would mean restoring the
    game to describe it, and a directory of twenty saves would replay twenty
    games to draw one menu.
    """

    players: tuple[str, ...] = ()
    turn_number: int = 0
    phase: str = ""
    active_player: str = ""
    decisions: int = 0
    winner: str | None = None
    #: what the game was waiting for when it was saved, for the menu subtitle
    pending: str = ""

    @property
    def finished(self) -> bool:
        return self.winner is not None

    def as_data(self) -> dict[str, Any]:
        return {
            "players": list(self.players),
            "turn_number": self.turn_number,
            "phase": self.phase,
            "active_player": self.active_player,
            "decisions": self.decisions,
            "winner": self.winner,
            "pending": self.pending,
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> SaveSummary:
        return cls(
            players=tuple(str(name) for name in data.get("players") or ()),
            turn_number=int(data.get("turn_number", 0) or 0),
            phase=str(data.get("phase", "")),
            active_player=str(data.get("active_player", "")),
            decisions=int(data.get("decisions", 0) or 0),
            winner=(str(data["winner"]) if data.get("winner") else None),
            pending=str(data.get("pending", "")),
        )

    def describe(self) -> str:
        who = ", ".join(self.players) or "?"
        if self.winner:
            return f"finished - turn {self.turn_number} - {who}"
        return f"turn {self.turn_number} - {who}"


@dataclass(slots=True)
class SaveGame:
    """A decision log plus everything a human needs to recognise it again."""

    log: DecisionLog
    summary: SaveSummary = field(default_factory=SaveSummary)
    saved_at: str = ""
    label: str = ""
    #: pack ids the game was played with. Recorded so a load against the wrong
    #: packs says *which* ones to pass rather than only that the hash differs.
    packs: tuple[str, ...] = ()
    format: int = SAVE_FORMAT
    #: where this came from, when it came from a file. Never serialised.
    path: Path | None = None

    # -- capture -----------------------------------------------------------

    @classmethod
    def capture(cls, engine: Engine, *, label: str = "") -> SaveGame:
        """Snapshot a game. Legal only at a savepoint (see :meth:`Engine.savepoint`).

        The log's entries are copied rather than referenced: the engine keeps
        playing, and a save that grew afterwards would describe a position the
        player never chose to keep.
        """
        if not engine.savepoint:
            raise SaveError(
                "a game can only be saved between decisions, not while the engine is "
                "resolving one - a suspended effect is not serialisable "
                "(docs/architecture_notes.md §4)"
            )
        state = engine.state
        pending = engine.pending
        return cls(
            log=engine.log.truncated(len(engine.log)),
            summary=SaveSummary(
                players=tuple(
                    state.players[pid].name for pid in state.turn_order if pid in state.players
                ),
                turn_number=state.turn_number,
                phase=state.phase,
                active_player=str(state.active_player),
                decisions=len(engine.log),
                winner=str(state.winner) if state.winner else None,
                pending=pending.kind if pending is not None else "",
            ),
            saved_at=_now(),
            label=label,
            packs=tuple(pack.id for pack in state.content.packs),
        )

    # -- restore -----------------------------------------------------------

    def restore(self, content: ContentRegistry, *, max_turns: int | None = None) -> Engine:
        """Rebuild the game and hand it back exactly where it was left.

        The returned engine has already been started and is sitting on the same
        unanswered request the save was taken at, with its own log holding every
        replayed decision — so the caller resumes with a plain
        ``engine.run(source)`` and the next save continues the same history.

        Refuses a save recorded against different content or a different seed,
        via :func:`~here_to_slay.core.log.check_content`: a game that quietly
        loads against edited cards is worse than one that will not load.
        """
        from here_to_slay.core.engine import Engine as _Engine

        engine, source = _Engine.replaying(content, self.log, max_turns=max_turns)
        # ReplayExhausted is the *expected* end of a save: the log answered
        # everything it had, and the game is now asking the question the player
        # stopped on. A save of a finished game simply never raises.
        with contextlib.suppress(ReplayExhausted):
            engine.run(source)
        return engine

    # -- serialisation -----------------------------------------------------

    def as_data(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "saved_at": self.saved_at,
            "label": self.label,
            "packs": list(self.packs),
            "summary": self.summary.as_data(),
            "log": self.log.as_data(),
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> SaveGame:
        version = int(data.get("format", SAVE_FORMAT))
        if version > SAVE_FORMAT:
            raise SaveError(
                f"save format {version} is newer than this build understands ({SAVE_FORMAT})"
            )
        try:
            log = DecisionLog.from_data(dict(data["log"]))
        except KeyError:
            raise SaveError("this file has no decision log - it is not a save game") from None
        return cls(
            log=log,
            summary=SaveSummary.from_data(dict(data.get("summary") or {})),
            saved_at=str(data.get("saved_at", "")),
            label=str(data.get("label", "")),
            packs=tuple(str(pack) for pack in data.get("packs") or ()),
            format=version,
        )

    def to_json(self, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.as_data(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> SaveGame:
        import json

        try:
            data = json.loads(text)
        except ValueError as exc:
            raise SaveError(f"not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise SaveError("a save game must be a JSON object")
        return cls.from_data(data)

    def save(self, path: Path | str) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.to_json(), encoding="utf-8")
        self.path = destination
        return destination

    @classmethod
    def load(cls, path: Path | str) -> SaveGame:
        source = Path(path)
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise SaveError(f"could not read {source}: {exc}") from None
        game = cls.from_json(text)
        game.path = source
        return game

    # -- presentation ------------------------------------------------------

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        if self.path is not None:
            return self.path.name[: -len(SAVE_SUFFIX)] if (
                self.path.name.endswith(SAVE_SUFFIX)
            ) else self.path.stem
        return "save"

    def describe(self) -> str:
        when = self.saved_at.replace("T", " ")[:16] if self.saved_at else "?"
        return f"{self.summary.describe()} - {when}"

    def __str__(self) -> str:
        return f"{self.title} - {self.describe()}"


# ---------------------------------------------------------------------------
# A directory of saves
# ---------------------------------------------------------------------------


def save_path(directory: Path | str, name: str) -> Path:
    """A save file path for ``name``, with the suffix applied exactly once."""
    stem = str(name).strip() or "save"
    for bad in '\\/:*?"<>|':
        stem = stem.replace(bad, "_")
    if stem.endswith(SAVE_SUFFIX):
        return Path(directory) / stem
    return Path(directory) / f"{stem}{SAVE_SUFFIX}"


def list_saves(directory: Path | str) -> tuple[SaveGame, ...]:
    """Every readable save in ``directory``, newest first.

    Unreadable and truncated files are skipped rather than raised: one corrupt
    file must not be able to hide the twenty good ones next to it.
    """
    folder = Path(directory)
    if not folder.is_dir():
        return ()
    found: list[tuple[float, SaveGame]] = []
    for path in sorted(folder.glob(f"*{SAVE_SUFFIX}")):
        try:
            game = SaveGame.load(path)
        except SaveError:
            continue
        try:
            when = path.stat().st_mtime
        except OSError:  # pragma: no cover - raced with a delete
            when = 0.0
        found.append((when, game))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(game for _, game in found)


def autosave_name(players: Sequence[str] = (), turn: int = 0) -> str:
    """A default file name for a save nobody bothered to name."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    who = "".join(name[:1] for name in players) or "game"
    return f"{stamp}_{who}_t{turn}"


__all__ = [
    "SAVE_FORMAT",
    "SAVE_SUFFIX",
    "SaveError",
    "SaveGame",
    "SaveSummary",
    "autosave_name",
    "list_saves",
    "save_path",
]
