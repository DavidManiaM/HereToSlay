"""``GameView`` — what one seat is allowed to know.

Redaction happens **in the core, not in the UI**. If the CLI or the pygame
client built its own projection, the day someone forgot a check would be the
day hidden information leaked into a renderer — and an AI reading ``GameState``
directly would simply cheat. So both presenters and both agents get the same
fair, read-only object (``docs/architecture_notes.md §8``).

The rule is one line: a zone's *size* is always public; its *contents* are
visible only when :meth:`Zone.is_visible_to` says so. A hidden zone therefore
yields no card ids at all — not shuffled ids, not placeholders with real ids
behind them. ``tests/test_core_view.py`` asserts that by serialising every view
and searching it for every hidden card id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from here_to_slay.core.ids import CardId, PlayerId, ZoneId
from here_to_slay.core.state import GameState
from here_to_slay.core.zones import Visibility, Zone


@dataclass(frozen=True, slots=True)
class CardView:
    """A card the viewer may see. Everything here is safe to render."""

    id: CardId
    def_id: str
    zone: ZoneId
    owner: PlayerId | None = None
    controller: PlayerId | None = None
    attachments: tuple[CardId, ...] = ()
    tapped: bool = False
    state: dict[str, Any] = field(default_factory=dict)

    def as_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "def_id": self.def_id,
            "zone": self.zone,
            "owner": self.owner,
            "controller": self.controller,
            "attachments": list(self.attachments),
            "tapped": self.tapped,
            "state": dict(self.state),
        }


@dataclass(frozen=True, slots=True)
class ZoneView:
    """A zone as seen from one seat: always a size, sometimes the cards."""

    id: ZoneId
    kind: str
    owner: PlayerId | None
    visibility: Visibility
    size: int
    capacity: int | None = None
    #: empty when ``revealed`` is False — the size is all you get
    cards: tuple[CardView, ...] = ()
    revealed: bool = True

    @property
    def hidden_count(self) -> int:
        return 0 if self.revealed else self.size

    def as_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "owner": self.owner,
            "visibility": self.visibility,
            "size": self.size,
            "capacity": self.capacity,
            "revealed": self.revealed,
            "cards": [card.as_data() for card in self.cards],
        }


@dataclass(frozen=True, slots=True)
class PlayerView:
    id: PlayerId
    name: str
    seat: int
    action_points: int
    is_active: bool
    is_you: bool
    flags: dict[str, Any] = field(default_factory=dict)
    zones: dict[str, ZoneView] = field(default_factory=dict)

    def zone(self, kind: str) -> ZoneView | None:
        return self.zones.get(kind)

    def as_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "seat": self.seat,
            "action_points": self.action_points,
            "is_active": self.is_active,
            "is_you": self.is_you,
            "flags": dict(self.flags),
            "zones": {kind: zone.as_data() for kind, zone in self.zones.items()},
        }


@dataclass(frozen=True, slots=True)
class GameView:
    """The board from one seat. Read-only: the only way back is ``engine.submit``."""

    seat: PlayerId
    turn_number: int
    phase: str
    active_player: PlayerId
    winner: PlayerId | None
    content_hash: str
    players: dict[PlayerId, PlayerView] = field(default_factory=dict)
    #: shared zones only; a player's zones hang off their :class:`PlayerView`
    zones: dict[str, ZoneView] = field(default_factory=dict)
    turn_order: tuple[PlayerId, ...] = ()
    flags: dict[str, Any] = field(default_factory=dict)

    # -- convenience for renderers ----------------------------------------

    @property
    def you(self) -> PlayerView:
        return self.players[self.seat]

    @property
    def is_your_turn(self) -> bool:
        return self.seat == self.active_player

    def zone(self, kind: str, owner: PlayerId | None = None) -> ZoneView | None:
        if owner is None:
            return self.zones.get(kind)
        player = self.players.get(owner)
        return player.zone(kind) if player else None

    def opponents(self) -> tuple[PlayerView, ...]:
        """Other seats, in turn order starting left of the viewer."""
        order = [*self.turn_order, *self.turn_order]
        start = self.turn_order.index(self.seat) + 1 if self.seat in self.turn_order else 0
        return tuple(self.players[pid] for pid in order[start : start + len(self.turn_order) - 1])

    def as_data(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "turn_number": self.turn_number,
            "phase": self.phase,
            "active_player": self.active_player,
            "winner": self.winner,
            "content_hash": self.content_hash,
            "turn_order": list(self.turn_order),
            "flags": dict(self.flags),
            "players": {pid: player.as_data() for pid, player in self.players.items()},
            "zones": {kind: zone.as_data() for kind, zone in self.zones.items()},
        }


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build_view(state: GameState, seat: PlayerId) -> GameView:
    """Project ``state`` through ``seat``'s eyes."""
    if seat not in state.players:
        raise KeyError(f"no such player '{seat}'")
    return GameView(
        seat=seat,
        turn_number=state.turn_number,
        phase=state.phase,
        active_player=state.active_player,
        winner=state.winner,
        content_hash=state.content_hash,
        turn_order=tuple(state.turn_order),
        flags=dict(state.flags),
        players={pid: _player_view(state, pid, seat) for pid in state.turn_order},
        zones={
            zone.kind: _zone_view(state, zone, seat)
            for zone in state.zones.values()
            if zone.owner is None
        },
    )


def _player_view(state: GameState, player: PlayerId, seat: PlayerId) -> PlayerView:
    seat_state = state.players[player]
    return PlayerView(
        id=player,
        name=seat_state.name,
        seat=seat_state.seat,
        action_points=seat_state.action_points,
        is_active=player == state.active_player,
        is_you=player == seat,
        flags=dict(seat_state.flags),
        zones={
            zone.kind: _zone_view(state, zone, seat)
            for zone in state.zones.values()
            if zone.owner == player
        },
    )


def _zone_view(state: GameState, zone: Zone, seat: PlayerId) -> ZoneView:
    revealed = zone.is_visible_to(seat)
    return ZoneView(
        id=zone.id,
        kind=zone.kind,
        owner=zone.owner,
        visibility=zone.visibility,
        size=len(zone),
        capacity=zone.capacity,
        revealed=revealed,
        cards=tuple(_card_view(state, card) for card in zone.cards) if revealed else (),
    )


def _card_view(state: GameState, card: CardId) -> CardView:
    instance = state.card(card)
    return CardView(
        id=instance.id,
        def_id=instance.def_id,
        zone=instance.zone,
        owner=instance.owner,
        controller=instance.controller,
        attachments=tuple(instance.attachments),
        tapped=instance.tapped,
        state=dict(instance.state),
    )


def hidden_card_ids(state: GameState, seat: PlayerId) -> frozenset[CardId]:
    """Every card ``seat`` must not be able to name. The test oracle for
    redaction, and cheap enough for an AI's determinisation step later."""
    return frozenset(
        card for zone in state.zones.values() if not zone.is_visible_to(seat) for card in zone.cards
    )


__all__ = [
    "CardView",
    "GameView",
    "PlayerView",
    "ZoneView",
    "build_view",
    "hidden_card_ids",
]
