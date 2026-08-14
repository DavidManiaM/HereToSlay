"""The generic card container.

There is no ``player.hand: list[Card]`` *and* ``player.party: list[Card]`` in
this engine. Both are :class:`Zone`s in a dict, built from the ``zones:`` table
in ``rules.yaml``. That is the whole reason a variant can add a "Vault" or a
"Graveyard" without an engine edit (``docs/architecture_notes.md §2.2``).

A ``Zone`` knows how to hold cards and nothing else — no knowledge of decks,
hands or the rules that move cards between them. Position semantics:

* ``"top"`` is index 0, so a deck draws from the front and ``top(3)`` is the
  next three cards.
* ``"bottom"`` appends. It is the default because most zones (a party, a hand,
  the monster row) are places you *add to*, not decks you stack.
* an ``int`` inserts at that index.
* unordered zones ignore position entirely and append, which keeps their
  contents in insertion order — deterministic, never a ``set``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal

from here_to_slay.content.schema import ZoneDef
from here_to_slay.core.errors import ZoneCapacityError, ZoneError
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, zone_id

Visibility = Literal["hidden", "public", "owner"]
Position = int | Literal["top", "bottom"]


@dataclass(slots=True)
class Zone:
    """An ordered-or-not bag of card ids with declared visibility."""

    id: ZoneId
    kind: str
    owner: PlayerId | None = None
    ordered: bool = True
    visibility: Visibility = "public"
    capacity: int | None = None
    cards: list[CardId] = field(default_factory=list)

    @classmethod
    def from_def(cls, definition: ZoneDef, owner: PlayerId | None = None) -> Zone:
        """Instantiate a declared zone. ``owner`` is required for player scope."""
        if definition.scope == "player" and owner is None:
            raise ZoneError(f"zone '{definition.id}' is player-scoped and needs an owner")
        if definition.scope == "shared" and owner is not None:
            raise ZoneError(f"zone '{definition.id}' is shared and cannot have an owner")
        return cls(
            id=zone_id(definition.id, owner),
            kind=definition.id,
            owner=owner,
            ordered=definition.ordered,
            visibility=definition.visibility,
            capacity=definition.capacity,
        )

    # -- container protocol ------------------------------------------------

    def __len__(self) -> int:
        return len(self.cards)

    def __iter__(self) -> Iterator[CardId]:
        return iter(self.cards)

    def __contains__(self, card: CardId) -> bool:
        return card in self.cards

    def __bool__(self) -> bool:
        # Without this, `if zone:` would be False for an empty zone *and* for a
        # missing one — a bug worth designing out.
        return True

    @property
    def is_empty(self) -> bool:
        return not self.cards

    @property
    def is_full(self) -> bool:
        return self.capacity is not None and len(self.cards) >= self.capacity

    @property
    def free_space(self) -> int | None:
        return None if self.capacity is None else max(0, self.capacity - len(self.cards))

    # -- mutation ----------------------------------------------------------
    #
    # In a live game these are reached through GameState.move_card, which keeps
    # each CardInstance's `zone` field in step. Call them directly only when
    # building a state from scratch (setup) or in a unit test.

    def add(self, card: CardId, position: Position = "bottom") -> None:
        if card in self.cards:
            raise ZoneError(f"card '{card}' is already in zone '{self.id}'")
        if self.is_full:
            raise ZoneCapacityError(
                f"zone '{self.id}' is at capacity {self.capacity} and cannot take '{card}'"
            )
        if not self.ordered or position == "bottom":
            self.cards.append(card)
        elif position == "top":
            self.cards.insert(0, card)
        else:
            self.cards.insert(int(position), card)

    def remove(self, card: CardId) -> None:
        try:
            self.cards.remove(card)
        except ValueError:
            raise ZoneError(f"card '{card}' is not in zone '{self.id}'") from None

    def extend(self, cards: Iterable[CardId], position: Position = "bottom") -> None:
        for card in cards:
            self.add(card, position)

    def clear(self) -> list[CardId]:
        """Empty the zone, returning what was in it."""
        removed, self.cards = self.cards, []
        return removed

    # -- queries -----------------------------------------------------------

    def top(self, count: int = 1) -> tuple[CardId, ...]:
        """The next ``count`` cards, without removing them. Never raises: a deck
        that is running out returns fewer, and the caller decides whether that
        is a problem."""
        return tuple(self.cards[:count])

    def bottom(self, count: int = 1) -> tuple[CardId, ...]:
        return tuple(self.cards[-count:]) if count else ()

    def index_of(self, card: CardId) -> int:
        try:
            return self.cards.index(card)
        except ValueError:
            raise ZoneError(f"card '{card}' is not in zone '{self.id}'") from None

    def is_visible_to(self, seat: PlayerId | None) -> bool:
        """Whether ``seat`` may see this zone's *contents* (not just its size)."""
        if self.visibility == "public":
            return True
        if self.visibility == "owner":
            return seat is not None and seat == self.owner
        return False

    def clone(self) -> Zone:
        return Zone(
            id=self.id,
            kind=self.kind,
            owner=self.owner,
            ordered=self.ordered,
            visibility=self.visibility,
            capacity=self.capacity,
            cards=list(self.cards),
        )

    def __repr__(self) -> str:
        return f"<Zone {self.id} [{len(self.cards)}]>"
