"""Reaction windows (``docs/rules_engine.md §5``).

A window is a named, re-entrant polling loop: "anyone want to respond?", asked
in a deterministic seat order, re-opened whenever somebody acts, and skipped
entirely when nobody holds a legal reaction.

The one structural decision here is **where** a window opens. It is *inside the
dispatch of the event it reacts to* — the bus opens declared windows during the
named phase (see :func:`here_to_slay.core.bus.dispatch`), which is what makes a
Challenge's ``{op: cancel_event}`` reach the ``card.played`` frame and stop the
Hero mid-play. Opening the window after the dispatch returned would leave the
Challenge with nothing to cancel.

That also keeps windows *data*: ``rules.yaml`` says

.. code-block:: yaml

    windows:
      card_played: {on: card.played, timing: pre, order: seat_left_of_active}

so a variant adds a ``damage_prevention`` window and a card that reacts to it
with no engine edit at all.

Properties that matter, all tested:

* **Deterministic order** — seat order from an anchor seat, never a dict or set.
* **Re-entrancy with a depth cap** (``rules.max_reaction_depth``): a window
  declines to open once the stack is that deep, so a pathological mod card ends
  the chain instead of hanging the engine.
* **Skipped when nobody can act**, so a two-player game with no Challenge in
  hand costs zero prompts and neither UI ever sees a window it cannot act in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.content.schema import ReactionDef, WindowDef
from here_to_slay.core.errors import EngineError
from here_to_slay.core.events import Event, Outcome
from here_to_slay.core.ids import CardId, PlayerId, zone_id
from here_to_slay.core.interpreter import PASS, Flow, Option, ReactionPrompt
from here_to_slay.core.mutators import move_to
from here_to_slay.core.state import GameState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

#: hard stop on re-opening. Each play removes a card from a hand, so the loop
#: already terminates; this only catches a plugin whose reaction puts the card
#: back.
MAX_ROUNDS = 32

#: where a reaction card is played from
REACTION_ZONE = "hand"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def window_seats(state: GameState, window: WindowDef, event: Event | None) -> tuple[PlayerId, ...]:
    """Every seat that may act, in the order they are asked.

    ``seat_left_of_active`` polls left of the active player and asks them last —
    the table order everyone at a real table expects. ``seat_left_of_actor``
    anchors on whoever caused the event instead, which is what a window on
    somebody *else's* card wants.
    """
    match window.order:
        case "seat_left_of_active":
            anchor = state.active_player
        case "seat_left_of_actor":
            actor = event.actor if event is not None else None
            anchor = actor if actor in state.players else state.active_player
        case "active_first":
            return state.seat_order_from(state.active_player, include_start=True)
        case "turn_order":
            return tuple(state.turn_order)
        case unknown:
            raise EngineError(
                f"unknown reaction window order '{unknown}' — "
                f"one of seat_left_of_active, seat_left_of_actor, active_first, turn_order"
            )
    return (*state.seat_order_from(anchor), anchor)


# ---------------------------------------------------------------------------
# What a seat may play
# ---------------------------------------------------------------------------


def reaction_of(ctx: EffectContext, card: CardId) -> ReactionDef | None:
    return getattr(ctx.definition(card), "reaction", None)


def playable_reactions(
    ctx: EffectContext, player: PlayerId, window: str, event: Event | None
) -> tuple[CardId, ...]:
    """Cards this seat may play into ``window`` right now.

    Each candidate's own ``condition`` is evaluated in a context that is already
    the one it would resolve in — ``$self`` is the would-be reactor, ``$card`` is
    the reaction card, ``$event`` is what is being reacted to. That is why a
    Challenge's ``played_by: {op: not_self}`` gate simply works, and why nobody
    is ever offered a card they could not legally play.
    """
    if not ctx.state.has_zone(REACTION_ZONE, player):
        return ()
    out: list[CardId] = []
    for instance in ctx.state.cards_in(zone_id(REACTION_ZONE, player)):
        reaction = reaction_of(ctx, instance.id)
        if reaction is None or reaction.window != window:
            continue
        scope = ctx.derive(
            self_player=player, source=instance.id, event=event, intent=None, bindings={}
        )
        if scope.test(reaction.condition):
            out.append(instance.id)
    return tuple(out)


def any_reaction_possible(ctx: EffectContext, window: str, event: Event | None) -> bool:
    """Whether opening ``window`` would ask anybody anything."""
    return any(
        playable_reactions(ctx, player, window, event) for player in ctx.state.turn_order
    )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def open_window(ctx: EffectContext, name: str, event: Event | None = None) -> Flow:
    """Poll every seat for a reaction until nobody acts.

    Returns ``CANCELLED`` if a reaction cancelled the event being dispatched —
    the caller (the bus) stops there, which is the whole point of a Challenge.
    """
    window = ctx.rules.windows.get(name)
    if window is None:
        return Outcome.DONE
    if ctx.execution.depth >= ctx.rules.max_reaction_depth:
        # The cap ends the chain gracefully rather than raising: a
        # challenge-of-a-challenge-of-… that runs this deep should stop being
        # answerable, not crash the game somebody is playing.
        return Outcome.DONE

    seats = window_seats(ctx.state, window, event)
    acted = True
    rounds = 0
    while acted and rounds < MAX_ROUNDS:
        acted = False
        rounds += 1
        for player in seats:
            if ctx.aborted:
                return Outcome.CANCELLED
            options = playable_reactions(ctx, player, name, event)
            if not options:
                continue
            chosen = yield ReactionPrompt(
                requester=player,
                prompt=_prompt_for(ctx, name, event),
                window=name,
                options=tuple(
                    Option(key=str(card), label=ctx.describe(card), card=card) for card in options
                ),
            )
            if chosen is PASS:
                continue
            yield from play_reaction(ctx, player, chosen, name, event)
            if ctx.aborted:
                return Outcome.CANCELLED
            acted = window.reopen_on_action
            break  # restart the poll from the top of the seat order
    return Outcome.DONE


def _prompt_for(ctx: EffectContext, name: str, event: Event | None) -> str:
    subject = ""
    if event is not None and event.card is not None and event.card in ctx.state.cards:
        subject = f" ({ctx.describe(event.card)})"
    return f"Respond to {name.replace('_', ' ')}{subject}?"


def play_reaction(
    ctx: EffectContext, player: PlayerId, card: CardId, window: str, event: Event | None
) -> Flow:
    """Play one reaction card for free, out of turn.

    A reaction is announced as a ``card.played`` like any other card, so a
    variant whose Challenges are themselves challengeable gets that by setting
    ``challengeable: true`` in the card's ``reaction`` block — the window that
    opens on ``card.played`` does the rest, and the depth cap bounds it.
    """
    reaction = reaction_of(ctx, card)
    if reaction is None:
        raise EngineError(f"card '{card}' has no reaction block to play into '{window}'")

    scope = ctx.derive(self_player=player, source=card, event=event, intent=None, bindings={})
    limbo = _limbo(ctx.state)
    if limbo is not None:
        move_to(ctx.state, card, limbo)

    played = yield from scope.emit(
        "card.played",
        {
            "card": card,
            "player": player,
            "kind": ctx.definition(card).kind,
            "window": window,
            "challengeable": reaction.challengeable,
        },
        actor=player,
    )
    if not played.ok:
        _spend(ctx.state, card)
        return Outcome.DONE

    outcome = yield from scope.run(reaction.effect)
    _spend(ctx.state, card)
    return outcome


def _limbo(state: GameState) -> Any:
    """``limbo`` is where an in-flight card waits. A rule set that declares no
    such zone simply plays reaction cards straight out of the hand."""
    return zone_id("limbo") if state.has_zone("limbo") else None


def _spend(state: GameState, card: CardId) -> None:
    """A resolved reaction card is spent.

    Moved directly rather than through ``card.discarded``: by this point the
    card is in limbo, not in anybody's hand, and a "your cards cannot be
    discarded" trigger cancelling it would strand it there forever. Cards that
    care about a spent reaction subscribe to its ``card.played``.
    """
    if state.has_zone("discard"):
        move_to(state, card, zone_id("discard"))


def cancelled_play(state: GameState, card: CardId) -> None:
    """Send a card whose play was cancelled to the discard (``§3`` step 7).

    The action point is *not* refunded — being challenged is a real cost.
    """
    _spend(state, card)


__all__ = [
    "any_reaction_possible",
    "cancelled_play",
    "open_window",
    "play_reaction",
    "playable_reactions",
    "window_seats",
]
