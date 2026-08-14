"""Identity types, and the naming scheme that keeps a decision log readable.

Ids are strings on purpose. ``NewType`` gives the type checker the distinctions
(a ``ZoneId`` is never a ``CardId``) while keeping every id JSON-able, printable
and diffable — a replay log full of integers would be unreadable when a modder
needs to work out what their card did.

Three shapes::

    PlayerId   "p1"
    ZoneId     "main_deck"  (shared)   |  "hand:p1"  (player-scoped)
    CardId     "base.hero.dodgy_dealer#2"   (definition id + copy number)

The composition helpers below are the only place these shapes are spelled, so a
variant that renames a zone never breaks id parsing.
"""

from __future__ import annotations

from typing import NewType

PlayerId = NewType("PlayerId", str)
CardId = NewType("CardId", str)
ZoneId = NewType("ZoneId", str)

#: separates a zone's kind from its owner: ``hand:p1``
ZONE_SEPARATOR = ":"
#: separates a card definition id from its copy number: ``base.hero.x#2``
COPY_SEPARATOR = "#"


def player_id(seat: int) -> PlayerId:
    """Seat index (0-based) -> ``p1``, ``p2``, ... (1-based, for humans)."""
    return PlayerId(f"p{seat + 1}")


def zone_id(kind: str, owner: PlayerId | None = None) -> ZoneId:
    """``("hand", "p1") -> "hand:p1"``; a shared zone is just its kind."""
    return ZoneId(kind if owner is None else f"{kind}{ZONE_SEPARATOR}{owner}")


def split_zone_id(value: ZoneId | str) -> tuple[str, PlayerId | None]:
    """Inverse of :func:`zone_id`: ``"hand:p1" -> ("hand", "p1")``."""
    kind, separator, owner = str(value).partition(ZONE_SEPARATOR)
    return kind, PlayerId(owner) if separator else None


def zone_kind(value: ZoneId | str) -> str:
    return split_zone_id(value)[0]


def zone_owner(value: ZoneId | str) -> PlayerId | None:
    return split_zone_id(value)[1]


def card_id(def_id: str, copy_number: int) -> CardId:
    """``("base.hero.x", 2) -> "base.hero.x#2"``. Copy numbers are 1-based."""
    return CardId(f"{def_id}{COPY_SEPARATOR}{copy_number}")


def def_id_of(value: CardId | str) -> str:
    """The definition id a card instance was minted from."""
    return str(value).partition(COPY_SEPARATOR)[0]


def copy_number_of(value: CardId | str) -> int:
    """Which copy this instance is. ``0`` if the id carries no copy number."""
    _, separator, number = str(value).partition(COPY_SEPARATOR)
    return int(number) if separator and number.isdigit() else 0
