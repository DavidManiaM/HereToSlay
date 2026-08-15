"""Boolean combinators, comparison, and flags."""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.ids import CardId, PlayerId
from here_to_slay.core.registry import condition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]

COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def compare_values(left: Any, cmp: str, right: Any) -> bool:
    """The one comparison implementation, shared by every counting condition.

    Mixed types are coerced to strings rather than raising: content compares a
    class name to a class name far more often than it compares an int to a
    string, and a ``TypeError`` from deep inside a filter is a miserable way to
    learn about a typo.
    """
    try:
        operation = COMPARATORS[cmp]
    except KeyError:
        raise EffectError(f"unknown comparator '{cmp}' (one of {sorted(COMPARATORS)})") from None
    if isinstance(left, bool) or isinstance(right, bool):
        left, right = bool(left), bool(right)
    elif isinstance(left, int | float) != isinstance(right, int | float):
        left, right = str(left), str(right)
    return operation(left, right)


@condition("always")
def _always(ctx: EffectContext, params: Params) -> bool:
    return True


@condition("never")
def _never(ctx: EffectContext, params: Params) -> bool:
    return False


@condition("all")
def _all(ctx: EffectContext, params: Params) -> bool:
    return all(ctx.test(node) for node in params.get("of") or ())


@condition("any")
def _any(ctx: EffectContext, params: Params) -> bool:
    return any(ctx.test(node) for node in params.get("of") or ())


@condition("not")
def _not(ctx: EffectContext, params: Params) -> bool:
    return not ctx.test(params.get("of"))


@condition("not_self")
def _not_self(ctx: EffectContext, params: Params) -> bool:
    """"Somebody else did this" — the gate on every Challenge card."""
    actor = ctx.event.player if ctx.event is not None else None
    return actor is not None and PlayerId(str(actor)) != ctx.me


@condition("compare")
def _compare(ctx: EffectContext, params: Params) -> bool:
    return compare_values(
        ctx.resolve(params.get("left")),
        str(params.get("cmp", "==")),
        ctx.resolve(params.get("right")),
    )


@condition("flag_set")
def _flag_set(ctx: EffectContext, params: Params) -> bool:
    """Read the mod escape hatch: a game, player or card flag.

    With no ``value:`` this asks "is it truthy?"; with one it asks "is it
    exactly this?", so ``{key: night, value: false}`` is expressible.
    """
    flags = flag_bag(ctx, params)
    key = str(params.get("key", ""))
    if "value" not in params:
        return bool(flags.get(key))
    return flags.get(key) == ctx.resolve(params["value"])


def flag_bag(ctx: EffectContext, params: Params) -> dict[str, Any]:
    """Which bag a ``scope``/``target`` pair points at."""
    scope = str(params.get("scope", "game"))
    target = params.get("target")
    match scope:
        case "game":
            return ctx.state.flags
        case "player":
            return ctx.state.player(ctx.resolve_player(target)).flags
        case "card":
            card = ctx.resolve_card(target) if target is not None else ctx.source
            if card is None:
                raise EffectError("a card flag needs a target card")
            return ctx.state.card(CardId(card)).state
        case _:
            raise EffectError(f"unknown flag scope '{scope}' (game, player or card)")
