"""Ops that move cards around: draw, discard, steal, search, reveal, shuffle."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, zone_id
from here_to_slay.core.interpreter import Flow
from here_to_slay.core.registry import effect

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]

DEFAULT_DECK = "main_deck"


def _filtered(ctx: EffectContext, zone: ZoneId, filter_node: Any) -> tuple[CardId, ...]:
    return tuple(
        instance.id
        for instance in ctx.state.cards_in(zone)
        if filter_node is None or ctx.matches(filter_node, instance.id)
    )


def _take_at_random(ctx: EffectContext, pool: Sequence[CardId], count: int) -> tuple[CardId, ...]:
    """Pick without replacement from ``state.rng`` — every call is logged, so a
    "discard a card at random" card still replays exactly."""
    remaining = list(pool)
    picked: list[CardId] = []
    for _ in range(min(count, len(remaining))):
        chosen = ctx.state.rng.choice(remaining)
        remaining.remove(chosen)
        picked.append(chosen)
    return tuple(picked)


def _pick(
    ctx: EffectContext,
    pool: Sequence[CardId],
    *,
    count: int,
    chooser: PlayerId,
    random: bool,
    prompt: str,
    from_zone: ZoneId | None = None,
) -> Flow:
    """Random or chosen, one code path — the difference is a YAML flag."""
    if random:
        return _take_at_random(ctx, pool, count)
    wanted = min(count, len(pool))
    picked = yield from ctx.ask_choose_cards(
        pool,
        chooser=chooser,
        minimum=wanted,
        maximum=wanted,
        prompt=prompt,
        from_zone=from_zone,
        # Picking out of a hand you cannot see is a blind pick: the request says
        # so, and the presenter must render backs rather than card names.
        hidden=from_zone is not None and not ctx.state.zone(from_zone).is_visible_to(chooser),
    )
    return picked


@effect("draw")
def _draw(ctx: EffectContext, params: Params) -> Flow:
    """Draw from the top of a deck.

    An empty deck simply stops the draw. *When* an exhausted deck is refilled
    from the discard is policy, not mechanism, so it belongs to the turn
    structure in ``rules.yaml`` (Phase 4) rather than to this op.
    """
    target = ctx.resolve_player(params.get("target"))
    count = ctx.resolve_int(params.get("count"), 1)
    deck_id = (
        ctx.resolve_zone(params["from"], owner=target)
        if params.get("from") is not None
        else zone_id(DEFAULT_DECK)
    )

    for _ in range(max(0, count)):
        deck = ctx.state.zone(deck_id)
        if deck.is_empty:
            break
        result = yield from ctx.emit(
            "card.drawn",
            {"player": target, "card": deck.top()[0], "from": deck_id},
            actor=target,
        )
        if not result.ok:
            return Outcome.CANCELLED
    return Outcome.DONE


@effect("discard")
def _discard(ctx: EffectContext, params: Params) -> Flow:
    """Discard from a player's hand (or any zone they own)."""
    target = ctx.resolve_player(params.get("target"))
    chooser = ctx.resolve_player(params.get("chooser"), default=target)
    count = ctx.resolve_int(params.get("count"), 1)
    zone = (
        ctx.resolve_zone(params["zone"], owner=target)
        if params.get("zone") is not None
        else zone_id("hand", target)
    )
    pool = _filtered(ctx, zone, params.get("filter"))
    if not pool or count <= 0:
        return Outcome.DONE

    picked = yield from _pick(
        ctx,
        pool,
        count=count,
        chooser=chooser,
        random=ctx.resolve_bool(params.get("random")),
        prompt=f"Discard {min(count, len(pool))}",
        from_zone=zone,
    )
    for card in picked:
        result = yield from ctx.emit(
            "card.discarded", {"player": target, "card": card}, actor=target
        )
        # A prevented discard cancels the effect that asked for it — that is
        # what "your cards cannot be discarded" has to mean, or the rest of the
        # card resolves as though the discard had happened.
        if not result.ok:
            return Outcome.CANCELLED
    return Outcome.DONE


@effect("move_card")
def _move_card(ctx: EffectContext, params: Params) -> Flow:
    """The generic mover. ``position: random`` costs one logged RNG call."""
    cards = ctx.resolve_cards(params.get("card"))
    destination = ctx.resolve_zone(params.get("to"))
    position = ctx.resolve(params.get("position")) or "bottom"
    for card in cards:
        result = yield from ctx.emit(
            "card.moved", {"card": card, "to": destination, "position": position}
        )
        if not result.ok:
            return Outcome.CANCELLED
    return Outcome.DONE


@effect("steal_card")
def _steal_card(ctx: EffectContext, params: Params) -> Flow:
    """Take cards out of another player's hand.

    ``random: true`` is the honest version — the thief picks blind, from the
    RNG, and cannot see what they took until it arrives.
    """
    victim = ctx.resolve_player(params.get("from"))
    thief = ctx.resolve_player(params.get("to"))
    chooser = ctx.resolve_player(params.get("chooser"), default=thief)
    count = ctx.resolve_int(params.get("count"), 1)
    source = zone_id("hand", victim)
    pool = ctx.state.zone(source).cards

    if not pool:
        return Outcome.DONE
    picked = yield from _pick(
        ctx,
        tuple(pool),
        count=count,
        chooser=chooser,
        random=ctx.resolve_bool(params.get("random"), True),
        prompt=f"Take {min(count, len(pool))} card(s)",
        from_zone=source,
    )
    for card in picked:
        result = yield from ctx.emit(
            "card.moved", {"card": card, "to": zone_id("hand", thief), "from": source}, actor=thief
        )
        if not result.ok:
            return Outcome.CANCELLED
    return Outcome.DONE


@effect("search")
def _search(ctx: EffectContext, params: Params) -> Flow:
    """Look through a zone for cards matching a filter, then do something.

    ``bind:`` names what was found so ``then:`` can act on it — without it the
    op could only ever be "look, then forget".
    """
    zone = ctx.resolve_zone(params.get("zone"))
    chooser = ctx.resolve_player(params.get("chooser"))
    count = ctx.resolve_int(params.get("count"), 1)
    pool = _filtered(ctx, zone, params.get("filter"))
    if not pool:
        return Outcome.DONE

    found = yield from ctx.ask_choose_cards(
        pool,
        chooser=chooser,
        minimum=0,
        maximum=min(count, len(pool)),
        prompt=f"Search {zone}",
        from_zone=zone,
    )
    if not found:
        return Outcome.DONE

    name = params.get("bind")
    scope = ctx.bind(**{str(name): found[0] if count == 1 else found}) if name else ctx
    outcome = yield from scope.run(params.get("then"))
    return outcome


@effect("reveal")
def _reveal(ctx: EffectContext, params: Params) -> Flow:
    """Show cards to an audience.

    Purely an announcement: nothing moves, so there is no mutator. Cards that
    care ("whenever a Monster is revealed...") subscribe to ``card.revealed``,
    and the UI renders it because the event is in the log.
    """
    cards = list(ctx.resolve_cards(params.get("card")))
    if params.get("zone") is not None:
        zone = ctx.resolve_zone(params["zone"])
        count = ctx.resolve_int(params.get("count"), 1)
        cards.extend(ctx.state.zone(zone).top(count))
    if not cards:
        return Outcome.DONE
    audience = ctx.resolve_players(params.get("to"), default=tuple(ctx.state.turn_order))
    yield from ctx.emit("card.revealed", {"cards": list(cards), "to": list(audience)})
    return Outcome.DONE


@effect("shuffle")
def _shuffle(ctx: EffectContext, params: Params) -> Outcome:
    """Shuffle a zone in place.

    No event: a shuffle has nothing to cancel and nothing to react to, and the
    permutation is already recorded in the RNG log, which is what a replay
    needs. It is the one state change in the catalogue that does not emit.
    """
    zone = ctx.state.zone(ctx.resolve_zone(params.get("zone")))
    if not zone.ordered:
        raise EffectError(f"zone '{zone.id}' is unordered — shuffling it means nothing")
    if len(zone) > 1:
        ctx.state.rng.shuffle(zone.cards)
    return Outcome.DONE
