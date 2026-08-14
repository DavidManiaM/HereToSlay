"""``GameState`` — everything the game is, as plain data.

Design points that earn their keep later (``docs/architecture_notes.md §2``):

* **Cards live in exactly one zone.** ``GameState.move_card`` is the single
  primitive that moves them, and it keeps the zone and the instance's ``zone``
  field in step. Nothing else in the engine should touch ``Zone.cards``.
* **``owner`` and ``controller`` are separate.** Here to Slay steals Heroes; a
  variant might borrow one until end of turn. Owner is who it came from,
  controller is who is using it now.
* **``flags`` is the mod escape hatch** — a "corruption counter" or a "phase of
  the moon" lives there, and the engine never reads it.
* **The state is snapshot-able and clone-able**, which is what makes replay,
  undo, AI rollouts and state diffs in tests all the same mechanism.

The state holds the whole :class:`ContentRegistry`, not just its ``RuleSet``:
a ``CardInstance`` is a def id plus a location, so anything that reasons about
one needs its ``CardDef``. The registry is immutable, so clones share it for
free. ``state.rules`` remains the shorthand the rest of the engine uses.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import CardDef, RuleSet
from here_to_slay.core.errors import EngineError, ZoneError
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, zone_id
from here_to_slay.core.rng import DeterministicRng
from here_to_slay.core.zones import Position, Zone


@dataclass(slots=True)
class CardInstance:
    """One physical card. Its *behaviour* lives in its ``CardDef``, never here."""

    id: CardId
    def_id: str
    zone: ZoneId
    owner: PlayerId | None = None
    controller: PlayerId | None = None
    #: Items equipped onto this Hero
    attachments: list[CardId] = field(default_factory=list)
    #: the inverse link, so "where did this Item go?" is one lookup
    attached_to: CardId | None = None
    #: "already used this turn" marker
    tapped: bool = False
    #: per-instance scratch space for mods (counters, marks)
    state: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> CardInstance:
        return CardInstance(
            id=self.id,
            def_id=self.def_id,
            zone=self.zone,
            owner=self.owner,
            controller=self.controller,
            attachments=list(self.attachments),
            attached_to=self.attached_to,
            tapped=self.tapped,
            state=copy.deepcopy(self.state),
        )

    def __repr__(self) -> str:
        return f"<Card {self.id} in {self.zone}>"


@dataclass(slots=True)
class PlayerState:
    """A seat. Its cards are in zones, not here — see the module docstring."""

    id: PlayerId
    name: str
    seat: int
    action_points: int = 0
    flags: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> PlayerState:
        return PlayerState(
            id=self.id,
            name=self.name,
            seat=self.seat,
            action_points=self.action_points,
            flags=copy.deepcopy(self.flags),
        )


@dataclass(slots=True)
class GameState:
    """The whole game. Mutated only through the primitives below."""

    content: ContentRegistry
    players: dict[PlayerId, PlayerState]
    turn_order: list[PlayerId]
    active_player: PlayerId
    zones: dict[ZoneId, Zone]
    cards: dict[CardId, CardInstance]
    rng: DeterministicRng
    phase: str = ""
    turn_number: int = 0
    flags: dict[str, Any] = field(default_factory=dict)
    winner: PlayerId | None = None

    # -- content -----------------------------------------------------------

    @property
    def rules(self) -> RuleSet:
        return self.content.rules

    @property
    def content_hash(self) -> str:
        """Identifies the exact content this game is being played with, so a
        replay can refuse to run against edited cards."""
        return self.content.content_hash

    def definition(self, card: CardId | CardInstance) -> CardDef:
        """The ``CardDef`` behind an instance."""
        instance = card if isinstance(card, CardInstance) else self.card(card)
        try:
            return self.content.cards[instance.def_id]
        except KeyError:
            raise EngineError(
                f"card '{instance.id}' refers to unknown definition '{instance.def_id}'"
            ) from None

    # -- lookup ------------------------------------------------------------

    def card(self, card_id: CardId) -> CardInstance:
        try:
            return self.cards[card_id]
        except KeyError:
            raise EngineError(f"no such card instance '{card_id}'") from None

    def zone(self, target: ZoneId | str) -> Zone:
        try:
            return self.zones[ZoneId(str(target))]
        except KeyError:
            raise ZoneError(f"no such zone '{target}'") from None

    def zone_of(self, kind: str, owner: PlayerId | None = None) -> Zone:
        """``zone_of("hand", "p1")`` — the compositional way to reach a zone."""
        return self.zone(zone_id(kind, owner))

    def has_zone(self, kind: str, owner: PlayerId | None = None) -> bool:
        return zone_id(kind, owner) in self.zones

    def zones_of_kind(self, kind: str) -> tuple[Zone, ...]:
        """Every instance of a zone kind — e.g. all players' hands."""
        return tuple(zone for zone in self.zones.values() if zone.kind == kind)

    def cards_in(self, target: ZoneId | str | Zone) -> tuple[CardInstance, ...]:
        zone = target if isinstance(target, Zone) else self.zone(target)
        return tuple(self.cards[card_id] for card_id in zone.cards)

    def zone_owner_of(self, card: CardId) -> PlayerId | None:
        return self.zone(self.card(card).zone).owner

    # -- seats -------------------------------------------------------------

    @property
    def active(self) -> PlayerState:
        return self.players[self.active_player]

    @property
    def action_points(self) -> int:
        """The active player's AP — what ``$action_points`` resolves to."""
        return self.active.action_points

    def player(self, player_id: PlayerId) -> PlayerState:
        try:
            return self.players[player_id]
        except KeyError:
            raise EngineError(f"no such player '{player_id}'") from None

    def seat_order_from(
        self, start: PlayerId | None = None, *, include_start: bool = False
    ) -> tuple[PlayerId, ...]:
        """Seat order beginning **left of** ``start`` (the active player by
        default). Reaction windows poll in exactly this order, so it must never
        be a dict or set iteration (``docs/rules_engine.md §5``)."""
        anchor = start if start is not None else self.active_player
        if anchor not in self.turn_order:
            raise EngineError(f"player '{anchor}' is not in the turn order")
        index = self.turn_order.index(anchor)
        rotated = self.turn_order[index:] + self.turn_order[:index]
        return tuple(rotated if include_start else rotated[1:])

    def opponents_of(self, player: PlayerId) -> tuple[PlayerId, ...]:
        return self.seat_order_from(player)

    def next_player(self, after: PlayerId | None = None) -> PlayerId:
        return self.seat_order_from(after)[0]

    # -- mutation ----------------------------------------------------------

    def register(self, instance: CardInstance, position: Position = "bottom") -> CardInstance:
        """Mint a card into the state and into its zone. Setup's only writer."""
        if instance.id in self.cards:
            raise EngineError(f"card instance '{instance.id}' already exists")
        self.zone(instance.zone).add(instance.id, position)
        self.cards[instance.id] = instance
        return instance

    def move_card(
        self,
        card: CardId,
        to: ZoneId | str,
        position: Position | str = "bottom",
        *,
        set_control: bool = True,
    ) -> CardInstance:
        """Move a card between zones. **The** zone primitive.

        ``position="random"`` consumes one logged RNG call, so a "shuffle this
        back in somewhere" effect stays replayable.

        Control follows location by default: a card in a player-scoped zone is
        controlled by that player, and a card with no owner yet takes the first
        player whose zone it lands in. That is what makes a stolen Hero return
        to its ``owner`` when a variant asks it to.
        """
        instance = self.card(card)
        destination = self.zone(to)
        origin = self.zone(instance.zone)

        if position == "random":
            position = self.rng.below(len(destination) + 1) if destination.ordered else "bottom"

        origin.remove(card)
        try:
            destination.add(card, position)  # type: ignore[arg-type]
        except ZoneError:
            origin.add(card, "top")  # a rejected move must not lose the card
            raise
        instance.zone = destination.id

        if set_control:
            instance.controller = destination.owner
            if destination.owner is not None and instance.owner is None:
                instance.owner = destination.owner
        return instance

    def move_cards(
        self, cards: Iterable[CardId], to: ZoneId | str, position: Position = "bottom"
    ) -> tuple[CardInstance, ...]:
        return tuple(self.move_card(card, to, position) for card in cards)

    def attach(self, item: CardId, host: CardId) -> None:
        """Equip ``item`` onto ``host``, keeping both ends of the link true."""
        item_instance, host_instance = self.card(item), self.card(host)
        if item_instance.attached_to is not None:
            raise EngineError(f"card '{item}' is already attached to '{item_instance.attached_to}'")
        host_instance.attachments.append(item)
        item_instance.attached_to = host

    def detach(self, item: CardId) -> CardId | None:
        item_instance = self.card(item)
        host = item_instance.attached_to
        if host is not None and host in self.cards:
            host_instance = self.card(host)
            if item in host_instance.attachments:
                host_instance.attachments.remove(item)
        item_instance.attached_to = None
        return host

    # -- copying, snapshots, diffs -----------------------------------------

    def clone(self) -> GameState:
        """A fully independent copy. ``content`` is immutable, so it is shared."""
        return GameState(
            content=self.content,
            players={pid: player.clone() for pid, player in self.players.items()},
            turn_order=list(self.turn_order),
            active_player=self.active_player,
            zones={zid: zone.clone() for zid, zone in self.zones.items()},
            cards={cid: instance.clone() for cid, instance in self.cards.items()},
            rng=self.rng.clone(),
            phase=self.phase,
            turn_number=self.turn_number,
            flags=copy.deepcopy(self.flags),
            winner=self.winner,
        )

    def snapshot(self) -> dict[str, Any]:
        """A JSON-able projection. Two runs of the same log must produce equal
        snapshots — that is the whole determinism claim, made assertable."""
        return {
            "turn_number": self.turn_number,
            "phase": self.phase,
            "active_player": self.active_player,
            "winner": self.winner,
            "flags": copy.deepcopy(self.flags),
            "rng": {"seed": self.rng.seed, "advances": self.rng.advances},
            "players": {
                pid: {
                    "name": player.name,
                    "seat": player.seat,
                    "action_points": player.action_points,
                    "flags": copy.deepcopy(player.flags),
                }
                for pid, player in sorted(self.players.items())
            },
            "zones": {zid: list(zone.cards) for zid, zone in sorted(self.zones.items())},
            "cards": {
                cid: {
                    "def_id": instance.def_id,
                    "zone": instance.zone,
                    "owner": instance.owner,
                    "controller": instance.controller,
                    "attachments": list(instance.attachments),
                    "attached_to": instance.attached_to,
                    "tapped": instance.tapped,
                    "state": copy.deepcopy(instance.state),
                }
                for cid, instance in sorted(self.cards.items())
            },
        }

    def __repr__(self) -> str:
        return (
            f"<GameState turn {self.turn_number} phase {self.phase!r} "
            f"active {self.active_player} cards {len(self.cards)}>"
        )


def diff_snapshots(before: Any, after: Any, path: str = "") -> list[str]:
    """Readable differences between two :meth:`GameState.snapshot` results.

    Used by tests and by the invariant reporter: "what changed" is far more
    useful in a failure message than two 400-line dumps.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        for key in sorted({*before, *after}, key=str):
            where = f"{path}.{key}" if path else str(key)
            if key not in before:
                out.append(f"{where}: added {after[key]!r}")
            elif key not in after:
                out.append(f"{where}: removed {before[key]!r}")
            else:
                out.extend(diff_snapshots(before[key], after[key], where))
        return out
    if isinstance(before, list) and isinstance(after, list) and before != after:
        return [f"{path}: {before!r} -> {after!r}"]
    if before != after:
        return [f"{path}: {before!r} -> {after!r}"]
    return []


def iter_cards(state: GameState, zones: Sequence[str]) -> Iterator[CardInstance]:
    """Every card in the named zone kinds, in zone order then card order."""
    for kind in zones:
        for zone in state.zones_of_kind(kind):
            yield from state.cards_in(zone)
