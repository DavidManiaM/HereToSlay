"""Predicates about the board: parties, hands, piles, requirements."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.core.conditions.logic import compare_values
from here_to_slay.core.ids import PlayerId, zone_id
from here_to_slay.core.registry import condition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]


def _zone_size(ctx: EffectContext, params: Params, kind: str) -> int:
    player = ctx.resolve_player(params.get("player"))
    return len(ctx.state.zone_of(kind, player))


def _counted(ctx: EffectContext, params: Params, count: int) -> bool:
    """``{cmp: ">=", value: 2}`` against a number we just worked out.

    ``cmp`` defaults to ``>=`` because every card that says "if you have N of
    something" means "at least N".
    """
    return compare_values(count, str(params.get("cmp", ">=")), ctx.resolve_int(params.get("value")))


#: Zones whose cards contribute a *class* to a party, and the card kinds in each
#: that count. A Party Leader is not a Hero — it never satisfies "N Heroes of any
#: class" — but it does represent its class, both for a Monster's class
#: requirement and for the six-class win. Keeping that as a table rather than an
#: ``if`` is what lets a variant add a class-bearing zone without editing this.
CLASS_BEARING_ZONES: tuple[tuple[str, frozenset[str]], ...] = (
    ("party", frozenset({"hero"})),
    ("leader", frozenset({"party_leader"})),
)


def effective_card_class(ctx: EffectContext, card_id: Any) -> str | None:
    """Printed class, unless a mask item is equipped — then that class instead.

    Masks are ordinary items tagged ``mask`` with their own ``card_class``.
    The equipped Hero *is considered* that class, not both.
    """
    instance = ctx.state.card(card_id)
    definition = ctx.state.definition(instance)
    printed = getattr(definition, "card_class", None)
    if definition.kind != "hero":
        return printed
    for att_id in instance.attachments:
        att_def = ctx.state.definition(ctx.state.card(att_id))
        tags = {str(tag).lower() for tag in (getattr(att_def, "tags", ()) or ())}
        masked = getattr(att_def, "card_class", None)
        if masked and "mask" in tags:
            return masked
    return printed


def party_classes(ctx: EffectContext, player: PlayerId) -> dict[str, int]:
    """How many cards of each class a player's party represents.

    The shared count behind ``party_has_class`` and
    ``party_covers_all_classes``. Counts Heroes *and* the Party Leader, because
    the rulebook is explicit on both points: a class requirement may be met by
    "either a Hero card or the [class] Party Leader card", and the six-class win
    reads "your Party (including your Party Leader card)".
    """
    counts: dict[str, int] = {}
    for zone, kinds in CLASS_BEARING_ZONES:
        for instance in ctx.state.cards_in(zone_id(zone, player)):
            definition = ctx.state.definition(instance)
            if definition.kind not in kinds:
                continue
            card_class = effective_card_class(ctx, instance.id)
            if card_class:
                counts[card_class] = counts.get(card_class, 0) + 1
    return counts


@condition("party_has_class")
def _party_has_class(ctx: EffectContext, params: Params) -> bool:
    """"Requires 2 Fighters" — the gate in front of every Monster."""
    player = ctx.resolve_player(params.get("player"))
    wanted = str(ctx.resolve(params.get("class")))
    minimum = ctx.resolve_int(params.get("min"), 1)
    return party_classes(ctx, player).get(wanted, 0) >= minimum


@condition("party_covers_all_classes")
def _party_covers_all_classes(ctx: EffectContext, params: Params) -> bool:
    """A base-game victory condition — and pure data: it reads
    ``rules.classes``, so a variant with a seventh class needs no code."""
    player = ctx.resolve_player(params.get("player"))
    classes = set(ctx.rules.classes)
    if not classes:
        return False
    return classes <= set(party_classes(ctx, player))


@condition("party_size")
def _party_size(ctx: EffectContext, params: Params) -> bool:
    return _counted(ctx, params, _zone_size(ctx, params, "party"))


@condition("hand_size")
def _hand_size(ctx: EffectContext, params: Params) -> bool:
    return _counted(ctx, params, _zone_size(ctx, params, "hand"))


@condition("discard_size")
def _discard_size(ctx: EffectContext, params: Params) -> bool:
    """The discard is shared, so ``player`` is ignored here — a variant with a
    per-player discard declares it player-scoped and this follows."""
    zone = ctx.state.zone("discard")
    return _counted(ctx, params, len(zone))


@condition("slain_count")
def _slain_count(ctx: EffectContext, params: Params) -> bool:
    """"Slay 3 Monsters" — the shipping win condition."""
    return _counted(ctx, params, _zone_size(ctx, params, "slain"))


@condition("has_card")
def _has_card(ctx: EffectContext, params: Params) -> bool:
    """Does a zone hold at least ``min`` cards matching ``filter``?

    This is what makes an action's ``requires:`` work: "you may only play a Hero
    if you are holding one" is a filter over your hand, not a Python branch.
    """
    zone = ctx.resolve_zone(
        params.get("zone"), owner=ctx.resolve_player(params.get("player"))
    )
    filter_node = params.get("filter")
    matching = [
        instance
        for instance in ctx.state.cards_in(zone)
        if filter_node is None or ctx.matches(filter_node, instance.id)
    ]
    return len(matching) >= ctx.resolve_int(params.get("min"), 1)
