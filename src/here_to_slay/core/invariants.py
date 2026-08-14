"""The state invariant checker (``docs/rules_engine.md §8``).

Why this exists: in a data-driven engine, a broken *card* is far more likely
than a broken engine. Without these checks, a mod that moves a card twice or
overfills the monster row surfaces as a weird UI glitch three turns later. With
them, it raises at the moment it happens, naming the card and the rule.

Cost is a handful of dict walks over a few hundred cards, so it runs after
every event resolution when ``HTS_STRICT=1`` and at quiescent points otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from here_to_slay.core.errors import EngineInvariantError
from here_to_slay.core.ids import CardId
from here_to_slay.core.state import GameState

STRICT_ENV = "HTS_STRICT"


def strict_mode() -> bool:
    """Whether continuous checking is on. Read per call so a test can toggle it."""
    return os.environ.get(STRICT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def find_violations(state: GameState) -> list[str]:
    """Every broken invariant, as a human-readable line. Empty means healthy."""
    violations: list[str] = []
    violations += _check_seats(state)
    violations += _check_placement(state)
    violations += _check_capacity(state)
    violations += _check_attachments(state)
    violations += _check_resources(state)
    return violations


def check_state(state: GameState, *, recent: Sequence[str] = ()) -> None:
    """Raise :class:`EngineInvariantError` if anything is broken."""
    violations = find_violations(state)
    if violations:
        raise EngineInvariantError(violations, recent)


def check_if_strict(state: GameState, *, recent: Sequence[str] = ()) -> None:
    """Called from hot paths: a no-op unless ``HTS_STRICT`` is set."""
    if strict_mode():
        check_state(state, recent=recent)


# ---------------------------------------------------------------------------
# The individual rules
# ---------------------------------------------------------------------------


def _check_seats(state: GameState) -> list[str]:
    out: list[str] = []
    if not state.turn_order:
        out.append("turn order is empty")
    if set(state.turn_order) != set(state.players):
        out.append(
            f"turn order {list(state.turn_order)} does not match players {sorted(state.players)}"
        )
    if len(set(state.turn_order)) != len(state.turn_order):
        out.append(f"turn order contains a duplicate seat: {list(state.turn_order)}")
    if state.active_player not in state.players:
        out.append(f"active player '{state.active_player}' is not a seat")
    if state.winner is not None and state.winner not in state.players:
        out.append(f"winner '{state.winner}' is not a seat")
    for pid, player in state.players.items():
        if pid != player.id:
            out.append(f"player keyed as '{pid}' but has id '{player.id}'")
    return out


def _check_placement(state: GameState) -> list[str]:
    """Every card is in exactly one zone, and it agrees about which one.

    This is also the conservation check: a card that vanished from every zone
    is reported, so ``remove_from_game`` has to be explicit about it.
    """
    out: list[str] = []
    seen: dict[CardId, list[str]] = {}
    for zone in state.zones.values():
        if len(set(zone.cards)) != len(zone.cards):
            out.append(f"zone '{zone.id}' lists a card twice")
        for card in zone.cards:
            seen.setdefault(card, []).append(str(zone.id))
            if card not in state.cards:
                out.append(f"zone '{zone.id}' holds unknown card '{card}'")

    for card, instance in state.cards.items():
        where = seen.get(card, [])
        if not where:
            out.append(f"card '{card}' is in no zone (it claims '{instance.zone}')")
        elif len(where) > 1:
            out.append(f"card '{card}' is in {len(where)} zones at once: {', '.join(where)}")
        elif where[0] != str(instance.zone):
            out.append(f"card '{card}' claims zone '{instance.zone}' but sits in '{where[0]}'")
        if instance.zone not in state.zones:
            out.append(f"card '{card}' claims zone '{instance.zone}', which does not exist")
    return out


def _check_capacity(state: GameState) -> list[str]:
    return [
        f"zone '{zone.id}' holds {len(zone)} cards, over its capacity of {zone.capacity}"
        for zone in state.zones.values()
        if zone.capacity is not None and len(zone) > zone.capacity
    ]


def _check_attachments(state: GameState) -> list[str]:
    """Equipment links point both ways, or neither."""
    out: list[str] = []
    for card, instance in state.cards.items():
        host = instance.attached_to
        if host is not None:
            if host not in state.cards:
                out.append(f"card '{card}' is attached to unknown card '{host}'")
            elif card not in state.cards[host].attachments:
                out.append(f"card '{card}' claims to be attached to '{host}', which disagrees")
        for attached in instance.attachments:
            if attached not in state.cards:
                out.append(f"card '{card}' lists unknown attachment '{attached}'")
            elif state.cards[attached].attached_to != card:
                out.append(f"card '{card}' lists '{attached}', which is not attached to it")
    return out


def _check_resources(state: GameState) -> list[str]:
    return [
        f"player '{pid}' has {player.action_points} action points"
        for pid, player in state.players.items()
        if player.action_points < 0
    ]
