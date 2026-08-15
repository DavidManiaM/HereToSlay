"""Control flow: ``seq``, ``if``, ``choose``, ``repeat``, ``for_each``...

These are the ops that make the effect tree a *language* rather than a list of
verbs. Everything here is generic — none of it knows what a Hero is — which is
why a variant can build a card the base game never imagined out of the same
seven words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.core.context import Binding
from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.interpreter import Flow, Option
from here_to_slay.core.registry import effect

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]

#: how many iterations a `repeat` may run before we assume the card is broken
MAX_REPEAT = 1000


@effect("noop")
def _noop(ctx: EffectContext, params: Params) -> Outcome:
    """Explicit "nothing happens" — a band that is deliberately a miss."""
    return Outcome.DONE


@effect("seq")
def _seq(ctx: EffectContext, params: Params) -> Flow:
    outcome = yield from ctx.run_sequence(params.get("steps") or ())
    return outcome


@effect("if")
def _if(ctx: EffectContext, params: Params) -> Flow:
    branch = params.get("then") if ctx.test(params.get("condition")) else params.get("else")
    outcome = yield from ctx.run(branch)
    return outcome


@effect("optional")
def _optional(ctx: EffectContext, params: Params) -> Flow:
    """Ask first. A "you may" card that never asks is a bug players notice."""
    chooser = ctx.resolve_player(params.get("chooser"))
    prompt = ctx.resolve_text(params.get("prompt"), "Do it?")
    if not (yield from ctx.ask_confirm(prompt, chooser=chooser)):
        return Outcome.DONE
    outcome = yield from ctx.run(params.get("effect"))
    return outcome


@effect("repeat")
def _repeat(ctx: EffectContext, params: Params) -> Flow:
    times = ctx.resolve_int(params.get("times"), 0)
    if times > MAX_REPEAT:
        raise EffectError(f"'repeat' asked for {times} iterations (cap is {MAX_REPEAT})")
    node = params.get("effect")
    for _ in range(max(0, times)):
        outcome = yield from ctx.run(node)
        if outcome.is_cancelled:
            return Outcome.CANCELLED
    return Outcome.DONE


@effect("for_each")
def _for_each(ctx: EffectContext, params: Params) -> Flow:
    """Run a body once per selected thing, with it bound.

    The selection is taken *once*, up front: a card that says "for each Hero in
    your party, do X" must not loop forever when X adds a Hero.
    """
    name = params.get("bind")
    if not isinstance(name, str):
        raise EffectError("'for_each' needs a 'bind' name for the current item")
    items = ctx.select(params.get("over"))
    node = params.get("effect")
    for item in items:
        outcome = yield from ctx.bind(**{name: item}).run(node)
        if outcome.is_cancelled:
            return Outcome.CANCELLED
    return Outcome.DONE


@effect("choose_effect")
def _choose_effect(ctx: EffectContext, params: Params) -> Flow:
    """"Choose one:" — the player picks a branch.

    Options whose ``condition`` fails are not offered at all, so an impossible
    mode never appears in the menu. If nothing is possible, nothing happens.
    """
    chooser = ctx.resolve_player(params.get("chooser"))
    available: dict[str, Any] = {}
    options: list[Option] = []
    for index, raw in enumerate(params.get("options") or ()):
        entry = dict(raw)
        if not ctx.test(entry.get("condition")):
            continue
        key = str(entry.get("key", index))
        available[key] = entry.get("effect")
        options.append(Option(key=key, label=str(entry.get("label", key))))

    if not options:
        return Outcome.DONE
    chosen = yield from ctx.ask_choose_option(
        options, chooser=chooser, prompt=ctx.resolve_text(params.get("prompt"), "Choose one")
    )
    outcome = yield from ctx.run(available[str(chosen)])
    return outcome


@effect("choose")
def _choose(ctx: EffectContext, params: Params) -> Flow:
    """Ask a player to pick from a selector, and bind the result.

    The bound value is a single id when at most one thing was asked for, and a
    tuple otherwise — because ``target: $victim`` reads badly if ``$victim`` is
    a one-element list, and every card that asks for one thing wants one thing.
    """
    name = params.get("bind")
    if not isinstance(name, str):
        raise EffectError("'choose' needs a 'bind' name to store the choice under")

    candidates = ctx.select(params.get("from"))
    chooser = ctx.resolve_player(params.get("chooser"))
    prompt = ctx.resolve_text(params.get("prompt"), "Choose")

    count = params.get("count")
    minimum = ctx.resolve_int(params.get("min"), ctx.resolve_int(count, 1))
    maximum = ctx.resolve_int(params.get("max"), ctx.resolve_int(count, 1))
    if ctx.resolve_bool(params.get("optional")):
        minimum = 0

    single = maximum <= 1
    if _all_players(ctx, candidates):
        chosen: Any = yield from ctx.ask_choose_player(candidates, chooser=chooser, prompt=prompt)
        picked: tuple[Any, ...] = () if chosen is None else (chosen,)
    else:
        picked = yield from ctx.ask_choose_cards(
            candidates, chooser=chooser, minimum=minimum, maximum=maximum, prompt=prompt
        )

    if len(picked) < minimum:
        return Outcome.CANCELLED  # not enough legal targets: the card fizzles
    return Binding(name, picked[0] if single and picked else () if single else picked)


def _all_players(ctx: EffectContext, candidates: tuple[Any, ...]) -> bool:
    return bool(candidates) and all(
        isinstance(item, str) and item in ctx.state.players for item in candidates
    )
