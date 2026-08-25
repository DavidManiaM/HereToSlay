"""RESOLVE handlers: the only code that writes state during a dispatch.

One mutator per event name, registered with ``@mutator``. Everything else in
the engine — effects, conditions, triggers — either *asks* for a change by
emitting an event or reads what happened. That is what makes a PRE subscriber's
``cancel_event`` meaningful: at the moment PRE runs, nothing has happened yet.

Two conventions hold throughout:

* **Mutators are total and dull.** They take the payload the event already
  carries and apply it. No decisions, no conditions, no asking — those all
  belong to the effect that emitted the event.
* **Moves are idempotent about their destination.** A card that is already
  where the event says to put it stays put rather than raising. Composite
  effects (steal a Hero: it *leaves* one party and *enters* another) emit two
  events about one physical move, and both should be announceable.
"""

from __future__ import annotations

from typing import Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Event
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, zone_id
from here_to_slay.core.registry import mutator
from here_to_slay.core.state import GameState
from here_to_slay.core.zones import Position


def move_to(
    state: GameState,
    card: CardId,
    zone: ZoneId | str,
    position: Position | str = "bottom",
    *,
    set_control: bool = True,
) -> None:
    """The shared primitive. Already-there is a no-op, not an error."""
    instance = state.card(card)
    if str(instance.zone) == str(zone):
        return
    state.move_card(card, zone, position, set_control=set_control)


def _required(event: Event, key: str) -> Any:
    value = event.get(key)
    if value is None:
        raise EffectError(f"event '{event.name}' needs a '{key}' in its payload")
    return value


def _zone_for(
    state: GameState, event: Event, key: str, kind: str, owner_key: str = "player"
) -> ZoneId:
    """Read a destination out of the payload, or build the default one.

    A player-scoped default belongs to the event's player (``hand:p1``); a
    shared one is just its kind (``discard``). Which is which comes from the
    rule set's declared zones, so a variant that makes the discard per-player
    needs no change here.
    """
    explicit = event.get(key)
    if explicit is not None:
        return ZoneId(str(explicit))
    owner = event.get(owner_key)
    if owner is None and owner_key == "player":
        owner = event.actor
    if owner is not None:
        scoped = zone_id(kind, PlayerId(str(owner)))
        if scoped in state.zones:
            return scoped
    return zone_id(kind)


# ---------------------------------------------------------------------------
# Cards & zones
# ---------------------------------------------------------------------------


@mutator("card.moved")
def _card_moved(state: GameState, event: Event) -> None:
    move_to(
        state,
        CardId(_required(event, "card")),
        ZoneId(str(_required(event, "to"))),
        event.get("position", "bottom"),
        set_control=bool(event.get("set_control", True)),
    )


@mutator("card.drawn")
def _card_drawn(state: GameState, event: Event) -> None:
    move_to(state, CardId(_required(event, "card")), _zone_for(state, event, "to", "hand"))


@mutator("card.discarded")
def _card_discarded(state: GameState, event: Event) -> None:
    move_to(state, CardId(_required(event, "card")), _zone_for(state, event, "to", "discard"))


# ---------------------------------------------------------------------------
# Party & board
# ---------------------------------------------------------------------------


@mutator("hero.entered_party")
def _hero_entered(state: GameState, event: Event) -> None:
    card = CardId(_required(event, "card"))
    move_to(state, card, _zone_for(state, event, "to", "party"))
    # "It arrived this turn" — read by `card_entered_play_this_turn`, which is
    # what makes a just-played Hero's ability free. Per-instance, so it clones
    # and snapshots with the state and the replay stays exact.
    state.card(card).state["entered_turn"] = state.turn_number


@mutator("hero.left_party")
def _hero_left(state: GameState, event: Event) -> None:
    """``to`` says where it goes — a discard, another party, back to a hand."""
    move_to(state, CardId(_required(event, "card")), _zone_for(state, event, "to", "discard"))


@mutator("item.equipped")
def _item_equipped(state: GameState, event: Event) -> None:
    item = CardId(_required(event, "item"))
    hero = CardId(_required(event, "hero"))
    host = state.card(hero)
    move_to(state, item, host.zone)
    if state.card(item).attached_to != hero:
        state.attach(item, hero)


@mutator("item.unequipped")
def _item_unequipped(state: GameState, event: Event) -> None:
    item = CardId(_required(event, "item"))
    state.detach(item)
    move_to(state, item, _zone_for(state, event, "to", "discard"))


@mutator("monster.slain")
def _monster_slain(state: GameState, event: Event) -> None:
    monster = CardId(_required(event, "monster"))
    slayer = event.get("by") or event.actor
    if slayer is None:
        raise EffectError("'monster.slain' needs a 'by' player in its payload")
    move_to(state, monster, zone_id("slain", PlayerId(slayer)))


# ---------------------------------------------------------------------------
# Flags & endgame
# ---------------------------------------------------------------------------


@mutator("flag.changed")
def _flag_changed(state: GameState, event: Event) -> None:
    """``scope`` picks the bag: the game, a player, or a card instance.

    ``value: None`` clears the key — ``clear_flag`` and ``set_flag`` are the
    same event, which keeps "whenever a flag changes" a single subscription.
    """
    scope = str(_required(event, "scope"))
    key = str(_required(event, "key"))
    value = event.get("value")
    target = event.get("target")

    match scope:
        case "game":
            flags = state.flags
        case "player":
            if target is None:
                raise EffectError("a 'player' flag needs a target player")
            flags = state.player(PlayerId(str(target))).flags
        case "card":
            if target is None:
                raise EffectError("a 'card' flag needs a target card")
            flags = state.card(CardId(str(target))).state
        case _:
            raise EffectError(f"unknown flag scope '{scope}' (game, player or card)")

    if value is None:
        flags.pop(key, None)
    else:
        flags[key] = value


@mutator("player.won")
def _player_won(state: GameState, event: Event) -> None:
    winner = event.get("player") or event.actor
    if winner is None:
        raise EffectError("'player.won' needs a player")
    state.winner = PlayerId(str(winner))


__all__ = ["move_to"]
