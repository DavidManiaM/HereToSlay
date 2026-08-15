"""Resources, flags, turn control, and the ops that talk about the game itself.

Action points are written directly rather than through an event. That is the
one deliberate exception to "state changes are events" in the catalogue, and it
is narrow on purpose: AP has no PRE story in the base rules (there is nothing to
cancel about a number going up), while ``action.paid`` already gives Phase 4 a
place for cost reduction to hook. If a variant needs "whenever anyone gains an
action point...", it emits its own event alongside — one line in a plugin op,
no engine change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import zone_id
from here_to_slay.core.interpreter import Flow
from here_to_slay.core.registry import effect

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]

#: game flags the turn machine (Phase 4) reads back
FLAG_END_TURN = "end_turn_requested"
FLAG_EXTRA_TURN = "extra_turn_for"


# ---------------------------------------------------------------------------
# Action points
# ---------------------------------------------------------------------------


@effect("gain_action_points")
def _gain_action_points(ctx: EffectContext, params: Params) -> Outcome:
    player = ctx.state.player(ctx.resolve_player(params.get("target")))
    player.action_points += ctx.resolve_int(params.get("amount"), 1)
    return Outcome.DONE


@effect("spend_action_points")
def _spend_action_points(ctx: EffectContext, params: Params) -> Outcome:
    """Spending never goes below zero — ``action_points >= 0`` is an invariant,
    and *affordability* is the cost system's job, not this op's."""
    player = ctx.state.player(ctx.resolve_player(params.get("target")))
    player.action_points = max(0, player.action_points - ctx.resolve_int(params.get("amount"), 1))
    return Outcome.DONE


@effect("set_action_points")
def _set_action_points(ctx: EffectContext, params: Params) -> Outcome:
    """How a turn starts: ``rules.yaml`` sets AP from a constant it owns."""
    player = ctx.state.player(ctx.resolve_player(params.get("target")))
    player.action_points = max(0, ctx.resolve_int(params.get("value"), 0))
    return Outcome.DONE


# ---------------------------------------------------------------------------
# Turn control
# ---------------------------------------------------------------------------


@effect("end_turn")
def _end_turn(ctx: EffectContext, params: Params) -> Outcome:
    """Request that the turn end after this effect finishes.

    A flag rather than an immediate transition: unwinding the generator stack
    from the middle of a card's effect would skip the rest of that card. The
    turn machine reads the flag at the next safe point.
    """
    ctx.state.flags[FLAG_END_TURN] = True
    return Outcome.DONE


@effect("extra_turn")
def _extra_turn(ctx: EffectContext, params: Params) -> Outcome:
    ctx.state.flags[FLAG_EXTRA_TURN] = ctx.resolve_player(params.get("target"))
    return Outcome.DONE


@effect("enforce_hand_limit")
def _enforce_hand_limit(ctx: EffectContext, params: Params) -> Flow:
    """Discard down to ``rules.turn.hand_limit``. ``null`` means no limit."""
    limit = ctx.rules.turn.hand_limit
    if limit is None:
        return Outcome.DONE
    player = ctx.resolve_player(params.get("target"))
    hand = ctx.state.zone(zone_id("hand", player))
    excess = len(hand) - limit
    if excess <= 0:
        return Outcome.DONE

    chosen = yield from ctx.ask_choose_cards(
        tuple(hand.cards),
        chooser=player,
        minimum=excess,
        maximum=excess,
        prompt=f"Discard {excess} down to your hand limit of {limit}",
        from_zone=hand.id,
    )
    for card in chosen:
        result = yield from ctx.emit(
            "card.discarded", {"player": player, "card": card}, actor=player
        )
        if not result.ok:
            return Outcome.CANCELLED
    return Outcome.DONE


# ---------------------------------------------------------------------------
# Events, flags, endgame
# ---------------------------------------------------------------------------


@effect("cancel_event")
def _cancel_event(ctx: EffectContext, params: Params) -> Outcome:
    """Stop the event being dispatched around us — the Challenge's payload.

    Cancelling also aborts the effect that emitted it, which is why a
    successful Challenge stops a Hero mid-play instead of merely annotating it.
    """
    ctx.cancel_event(
        ctx.resolve_text(params.get("reason")),
        and_discard=ctx.resolve_bool(params.get("and_discard")),
    )
    return Outcome.CANCELLED


@effect("emit")
def _emit(ctx: EffectContext, params: Params) -> Flow:
    """Fire a custom event so other cards can hook it.

    This is the seam that lets a variant invent ``corruption.spread`` and a
    second card react to it, with no engine edit: the validator already accepts
    any event name some loaded card emits.
    """
    name = params.get("event")
    if not isinstance(name, str):
        raise EffectError("'emit' needs an 'event' name")
    payload = ctx.resolve(params.get("payload") or {})
    if not isinstance(payload, dict):
        raise EffectError(f"'emit' payload must be a mapping, got {type(payload).__name__}")
    result = yield from ctx.emit(name, payload)
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("set_flag")
def _set_flag(ctx: EffectContext, params: Params) -> Flow:
    outcome = yield from _change_flag(ctx, params, ctx.resolve(params.get("value", True)))
    return outcome


@effect("clear_flag")
def _clear_flag(ctx: EffectContext, params: Params) -> Flow:
    """Setting and clearing are one event, so "whenever a flag changes" is one
    subscription rather than two."""
    outcome = yield from _change_flag(ctx, params, None)
    return outcome


def _change_flag(ctx: EffectContext, params: Params, value: Any) -> Flow:
    scope = ctx.resolve_text(params.get("scope"), "game")
    target = params.get("target")
    resolved: Any = None
    if scope == "player":
        resolved = ctx.resolve_player(target)
    elif scope == "card":
        resolved = ctx.resolve_card(target) if target is not None else ctx.source
    result = yield from ctx.emit(
        "flag.changed",
        {"scope": scope, "key": params.get("key"), "value": value, "target": resolved},
    )
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("win_game")
def _win_game(ctx: EffectContext, params: Params) -> Flow:
    """Declare a winner. The victory *conditions* live in ``rules.yaml``; this
    is only the announcement, so a card that says "you win" and a rule that says
    "slay 3 monsters" end the game the same way."""
    winner = ctx.resolve_player(params.get("target"))
    result = yield from ctx.emit("player.won", {"player": winner}, actor=winner)
    if not result.ok:
        return Outcome.CANCELLED
    yield from ctx.emit("game.ended", {"winner": winner})
    return Outcome.DONE
