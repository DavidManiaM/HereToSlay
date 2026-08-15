"""Roll ops: ``roll``, ``modify_roll``, ``reroll``, ``set_roll_result``, ``contest_roll``.

Thin wrappers over :mod:`here_to_slay.core.rolls` — the pipeline is there, these
are the words card data uses to reach it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.interpreter import Flow
from here_to_slay.core.registry import effect
from here_to_slay.core.rolls import (
    DEFAULT_DICE,
    Modifier,
    current_roll,
    perform_roll,
    reroll_dice,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]


@effect("roll")
def _roll(ctx: EffectContext, params: Params) -> Flow:
    """Roll dice and run the band that matches the total."""
    roll = yield from perform_roll(
        ctx,
        dice=ctx.resolve_text(params.get("dice"), DEFAULT_DICE),
        kind=ctx.resolve_text(params.get("kind"), "generic"),
        roller=ctx.resolve_player(params.get("roller")),
        source=ctx.source,
        outcomes=params.get("outcomes") or (),
    )
    return Outcome.CANCELLED if roll.cancelled else Outcome.DONE


@effect("modify_roll")
def _modify_roll(ctx: EffectContext, params: Params) -> Flow:
    """The whole of a Modifier card. Note it does *not* know what it modifies."""
    roll = current_roll(ctx, params.get("target_roll"))
    amount = ctx.resolve_int(params.get("amount"))
    source = ctx.resolve_cards(params.get("source"))
    modifier = roll.add(
        Modifier(
            amount=amount,
            source=source[0] if source else ctx.source,
            applied_by=ctx.me,
            label=ctx.describe(source[0]) if source else "",
        )
    )
    yield from ctx.derive(roll=roll).emit(
        "roll.modified",
        {
            "roll": roll,
            "kind": roll.kind,
            "roller": roll.roller,
            "amount": modifier.amount,
            "card": modifier.source,
        },
    )
    return Outcome.DONE


@effect("reroll")
def _reroll(ctx: EffectContext, params: Params) -> Flow:
    """Throw the dice again. Modifiers already applied stay applied."""
    roll = current_roll(ctx, params.get("target_roll"))
    dice = params.get("dice")
    yield from reroll_dice(ctx, roll, ctx.resolve_text(dice) if dice is not None else None)
    return Outcome.DONE


@effect("set_roll_result")
def _set_roll_result(ctx: EffectContext, params: Params) -> Flow:
    """"Treat that roll as a 12" — overrides dice *and* modifiers."""
    roll = current_roll(ctx, params.get("target_roll"))
    roll.forced = ctx.resolve_int(params.get("value"))
    yield from ctx.derive(roll=roll).emit(
        "roll.modified",
        {"roll": roll, "kind": roll.kind, "roller": roll.roller, "set_to": roll.forced},
    )
    return Outcome.DONE


@effect("contest_roll")
def _contest_roll(ctx: EffectContext, params: Params) -> Flow:
    """Two rolls, higher wins — the Challenge card's payload.

    Each side is a full roll, so each opens its own modification window: a
    Challenge can be won by a Modifier, and that falls out rather than being
    special-cased. Ties go to ``on_tie`` (the base rule: the defender).
    """
    roll_a = yield from _side(ctx, params.get("a"), "a")
    if roll_a.cancelled:
        return Outcome.CANCELLED
    roll_b = yield from _side(ctx, params.get("b"), "b")
    if roll_b.cancelled:
        return Outcome.CANCELLED

    if roll_a.total > roll_b.total:
        branch = params.get("on_a_wins")
    elif roll_b.total > roll_a.total:
        branch = params.get("on_b_wins")
    else:
        branch = params.get("on_tie")

    scope = ctx.bind(roll_a=roll_a, roll_b=roll_b)
    outcome = yield from scope.run(branch)
    return outcome


def _side(ctx: EffectContext, spec: Any, which: str) -> Flow:
    """One half of a contest: ``{roller: $self, dice: "2d6", kind: challenge}``."""
    if spec is None:
        raise EffectError(f"'contest_roll' needs a '{which}' side to roll")
    data = ctx.resolve(spec)
    if not isinstance(data, dict):
        raise EffectError(f"'contest_roll.{which}' must be a mapping, got {data!r}")
    roll = yield from perform_roll(
        ctx,
        dice=str(data.get("dice") or DEFAULT_DICE),
        kind=str(data.get("kind") or "contest"),
        roller=ctx.resolve_player(data.get("roller")),
        source=ctx.source,
    )
    return roll
