"""Noticing what changed, so the client can animate it.

The engine does not tell the UI anything. It exposes ``engine.view(seat)`` and
expects to be *asked* — a deliberate choice (``docs/architecture_notes.md``):
a renderer that subscribed to the event bus would be a renderer that could
accidentally mutate the game, and an engine that called ``render()`` would be
an engine that could not run headless.

So the client diffs. Every frame it takes a cheap fingerprint of the view and
compares it with the last one; the differences become :class:`BoardChange`
records that the scene turns into card flights, banners and toasts.

The important property is that **this can only see what the seat can see.**
Transitions between two hidden zones are reported as anonymous count changes,
never as named cards — so the animation layer physically cannot leak the top of
the deck, no matter how a future effect is written.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Zone kinds a card can be in, as the view names them.
PLAYER_ZONES = ("hand", "party", "leader", "slain")
SHARED_ZONES = ("main_deck", "discard", "monster_deck", "monster_row", "limbo", "leader_pool")


@dataclass(frozen=True, slots=True)
class Place:
    """Where a card is, from the viewer's point of view."""

    zone: str
    owner: str | None = None

    def __str__(self) -> str:
        return f"{self.zone}:{self.owner}" if self.owner else self.zone


@dataclass(frozen=True, slots=True)
class CardMoved:
    card_id: str
    def_id: str
    frm: Place
    to: Place

    @property
    def kind(self) -> str:
        return "card_moved"


@dataclass(frozen=True, slots=True)
class CardAppeared:
    """A card became visible (drawn into your hand, revealed onto the row)."""

    card_id: str
    def_id: str
    to: Place

    @property
    def kind(self) -> str:
        return "card_appeared"


@dataclass(frozen=True, slots=True)
class CardVanished:
    """A card left the viewer's sight — into a hidden zone, or shuffled away."""

    card_id: str
    def_id: str
    frm: Place

    @property
    def kind(self) -> str:
        return "card_vanished"


@dataclass(frozen=True, slots=True)
class ZoneCountChanged:
    """A hidden zone grew or shrank. All the viewer is entitled to know."""

    zone: str
    owner: str | None
    delta: int
    size: int

    @property
    def kind(self) -> str:
        return "zone_count"


@dataclass(frozen=True, slots=True)
class TurnChanged:
    turn_number: int
    active_player: str
    previous_player: str | None

    @property
    def kind(self) -> str:
        return "turn_changed"


@dataclass(frozen=True, slots=True)
class PhaseChanged:
    phase: str
    previous: str

    @property
    def kind(self) -> str:
        return "phase_changed"


@dataclass(frozen=True, slots=True)
class PointsChanged:
    player: str
    delta: int
    value: int

    @property
    def kind(self) -> str:
        return "points_changed"


@dataclass(frozen=True, slots=True)
class RollHappened:
    roll: Any

    @property
    def kind(self) -> str:
        return "roll"


@dataclass(frozen=True, slots=True)
class RollModified:
    roll: Any
    modifier: Any

    @property
    def kind(self) -> str:
        return "roll_modified"


@dataclass(frozen=True, slots=True)
class GameWon:
    winner: str

    @property
    def kind(self) -> str:
        return "game_won"


BoardChange = (
    CardMoved
    | CardAppeared
    | CardVanished
    | ZoneCountChanged
    | TurnChanged
    | PhaseChanged
    | PointsChanged
    | RollHappened
    | RollModified
    | GameWon
)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass
class _Snapshot:
    places: dict[str, Place] = field(default_factory=dict)
    defs: dict[str, str] = field(default_factory=dict)
    sizes: dict[tuple[str, str | None], int] = field(default_factory=dict)
    points: dict[str, int] = field(default_factory=dict)
    turn: int = 0
    phase: str = ""
    active: str = ""
    winner: str | None = None


def _snapshot(view: Any, extra_views: Iterable[Any] = ()) -> _Snapshot:
    """Fingerprint one seat's view (plus any extra views a spectator supplied)."""
    snap = _Snapshot(
        turn=int(getattr(view, "turn_number", 0)),
        phase=str(getattr(view, "phase", "")),
        active=str(getattr(view, "active_player", "")),
        winner=getattr(view, "winner", None),
    )

    def absorb(source: Any) -> None:
        for kind, zone in (getattr(source, "zones", {}) or {}).items():
            snap.sizes[(kind, None)] = int(getattr(zone, "size", 0))
            if getattr(zone, "revealed", False):
                for card in getattr(zone, "cards", ()):
                    snap.places[card.id] = Place(kind, None)
                    snap.defs[card.id] = card.def_id
        for pid, player in (getattr(source, "players", {}) or {}).items():
            snap.points[pid] = int(getattr(player, "action_points", 0))
            for kind, zone in (getattr(player, "zones", {}) or {}).items():
                snap.sizes[(kind, pid)] = int(getattr(zone, "size", 0))
                if getattr(zone, "revealed", False):
                    for card in getattr(zone, "cards", ()):
                        snap.places[card.id] = Place(kind, pid)
                        snap.defs[card.id] = card.def_id

    absorb(view)
    for other in extra_views:
        absorb(other)
    return snap


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class BoardTracker:
    """Diffs successive views and yields what changed.

    ``poll`` is called once per frame with the view being rendered. The first
    call establishes a baseline and reports nothing — the opening deal is
    animated deliberately by the scene, not inferred from "everything appeared".
    """

    def __init__(self) -> None:
        self._previous: _Snapshot | None = None
        self._rolls_seen = 0
        self._modifiers_seen: dict[str, int] = {}
        self.frames = 0

    def reset(self) -> None:
        self._previous = None
        self._rolls_seen = 0
        self._modifiers_seen.clear()
        self.frames = 0

    def poll(
        self,
        view: Any,
        *,
        rolls: tuple[Any, ...] = (),
        extra_views: Iterable[Any] = (),
    ) -> list[BoardChange]:
        self.frames += 1
        snap = _snapshot(view, extra_views)
        previous = self._previous
        self._previous = snap
        if previous is None:
            self._rolls_seen = len(rolls)
            for roll in rolls:
                self._modifiers_seen[str(roll.id)] = len(getattr(roll, "modifiers", ()))
            return []

        changes: list[BoardChange] = []
        self._diff_cards(previous, snap, changes)
        self._diff_zones(previous, snap, changes)
        self._diff_meta(previous, snap, changes)
        self._diff_rolls(rolls, changes)
        return changes

    # -- pieces ------------------------------------------------------------

    def _diff_cards(
        self, old: _Snapshot, new: _Snapshot, out: list[BoardChange]
    ) -> None:
        for card_id, place in new.places.items():
            was = old.places.get(card_id)
            def_id = new.defs.get(card_id, "")
            if was is None:
                out.append(CardAppeared(card_id, def_id, place))
            elif was != place:
                out.append(CardMoved(card_id, def_id, was, place))
        for card_id, place in old.places.items():
            if card_id not in new.places:
                out.append(CardVanished(card_id, old.defs.get(card_id, ""), place))

    def _diff_zones(
        self, old: _Snapshot, new: _Snapshot, out: list[BoardChange]
    ) -> None:
        for key, size in new.sizes.items():
            before = old.sizes.get(key)
            if before is not None and before != size:
                out.append(ZoneCountChanged(key[0], key[1], size - before, size))

    def _diff_meta(
        self, old: _Snapshot, new: _Snapshot, out: list[BoardChange]
    ) -> None:
        if new.active != old.active or new.turn != old.turn:
            out.append(TurnChanged(new.turn, new.active, old.active or None))
        if new.phase != old.phase:
            out.append(PhaseChanged(new.phase, old.phase))
        for pid, value in new.points.items():
            before = old.points.get(pid)
            if before is not None and before != value:
                out.append(PointsChanged(pid, value - before, value))
        if new.winner and not old.winner:
            out.append(GameWon(new.winner))

    def _diff_rolls(self, rolls: tuple[Any, ...], out: list[BoardChange]) -> None:
        if len(rolls) > self._rolls_seen:
            for roll in rolls[self._rolls_seen:]:
                out.append(RollHappened(roll))
            self._rolls_seen = len(rolls)
        # A Modifier played into an existing roll changes it without creating a
        # new one, so modifier counts are tracked per roll id.
        for roll in rolls[-6:]:
            key = str(getattr(roll, "id", ""))
            mods = getattr(roll, "modifiers", ()) or ()
            seen = self._modifiers_seen.get(key, 0)
            if len(mods) > seen:
                for mod in mods[seen:]:
                    out.append(RollModified(roll, mod))
                self._modifiers_seen[key] = len(mods)


# ---------------------------------------------------------------------------
# Interpretation helpers — what a move *means*, in words
# ---------------------------------------------------------------------------

#: (from_zone, to_zone) -> (log line template, icon, colour role)
_MOVE_PHRASES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("main_deck", "hand"): ("{who} drew a card", "hand", "dim"),
    ("hand", "party"): ("{who} played {what}", "hero", "good"),
    ("hand", "discard"): ("{who} discarded {what}", "scroll", "dim"),
    ("hand", "limbo"): ("{who} is playing {what}", "bolt", "warn"),
    ("limbo", "party"): ("{what} joined {who}'s party", "hero", "good"),
    ("limbo", "discard"): ("{what} was cancelled", "challenge", "bad"),
    ("party", "discard"): ("{what} was destroyed", "skull", "bad"),
    ("party", "slain"): ("{what} was slain", "skull", "bad"),
    ("monster_row", "slain"): ("{who} slew {what}", "skull", "good"),
    ("monster_deck", "monster_row"): ("{what} appeared", "monster", "warn"),
    ("discard", "hand"): ("{who} recovered {what}", "hand", "good"),
    ("party", "party"): ("{what} changed hands", "target", "warn"),
}


def describe_move(
    change: CardMoved,
    *,
    card_name: str,
    owner_name: str,
) -> tuple[str, str, str] | None:
    """A log line for a card move, or ``None`` if it is not worth reporting."""
    phrase = _MOVE_PHRASES.get((change.frm.zone, change.to.zone))
    if phrase is None:
        return None
    template, icon, role = phrase
    return template.format(who=owner_name, what=card_name), icon, role


__all__ = [
    "PLAYER_ZONES",
    "SHARED_ZONES",
    "BoardChange",
    "BoardTracker",
    "CardAppeared",
    "CardMoved",
    "CardVanished",
    "GameWon",
    "PhaseChanged",
    "Place",
    "PointsChanged",
    "RollHappened",
    "RollModified",
    "TurnChanged",
    "ZoneCountChanged",
    "describe_move",
]
