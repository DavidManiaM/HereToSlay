"""Predicates about a card, an event, or a roll."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.ids import CardId, PlayerId
from here_to_slay.core.registry import condition

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]


def subject(ctx: EffectContext, params: Params, key: str = "card") -> CardId:
    """The card a predicate is about.

    Defaults matter here: inside a ``filter`` the subject is ``$candidate``
    (bound per candidate), and everywhere else it is ``$card`` (the card whose
    ability is running). One node therefore reads correctly in both places,
    which is why ``{op: card_kind_is, kind: hero}`` needs no ``card:`` at all.
    """
    if params.get(key) is not None:
        return ctx.resolve_card(params[key])
    if "candidate" in ctx.bindings:
        return ctx.resolve_card("$candidate")
    if ctx.source is not None:
        return ctx.source
    raise EffectError("no card to test: pass 'card:' or use this inside a filter")


def _values(raw: Any) -> tuple[str, ...]:
    """``kind_in: hero`` and ``kind_in: [hero, item]`` mean the same thing."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Iterable):
        return tuple(str(item) for item in raw)
    return (str(raw),)


@condition("card_kind_is")
def _card_kind_is(ctx: EffectContext, params: Params) -> bool:
    definition = ctx.definition(subject(ctx, params))
    return definition.kind in _values(ctx.resolve(params.get("kind")))


@condition("card_class_is")
def _card_class_is(ctx: EffectContext, params: Params) -> bool:
    definition = ctx.definition(subject(ctx, params))
    card_class = getattr(definition, "card_class", None)
    return card_class is not None and card_class in _values(ctx.resolve(params.get("class")))


@condition("card_has_tag")
def _card_has_tag(ctx: EffectContext, params: Params) -> bool:
    """``tags`` are unconstrained on purpose — the cheapest way for a variant to
    say "everything tagged cursed costs +1" without an engine change."""
    definition = ctx.definition(subject(ctx, params))
    wanted = set(_values(ctx.resolve(params.get("tag"))))
    return bool(wanted & set(definition.tags))


@condition("card_tapped")
def _card_tapped(ctx: EffectContext, params: Params) -> bool:
    """"Already used this turn" — what keeps a once-per-turn Hero out of the menu."""
    return bool(ctx.state.card(subject(ctx, params)).tapped)


@condition("card_has_ability")
def _card_has_ability(ctx: EffectContext, params: Params) -> bool:
    """Does this card declare an activated ability?

    Without it, ``use_hero_ability`` would offer every Hero in the party and then
    fail on the ones that are just a body — the menu has to be honest.
    """
    ability = getattr(ctx.definition(subject(ctx, params)), "ability", None)
    if ability is None:
        return False
    wanted = _values(ctx.resolve(params.get("activation")))
    return not wanted or ability.activation in wanted


@condition("requirement_met")
def _requirement_met(ctx: EffectContext, params: Params) -> bool:
    """Evaluate a card's *own* ``requirement`` block for a player.

    This is the Monster gate ("Requires 2 Fighters"), asked from outside the
    card: the action menu needs to know which Monsters a seat may attack, and
    only the Monster knows what it demands.
    """
    card = subject(ctx, params)
    requirement = getattr(ctx.definition(card), "requirement", None)
    if requirement is None:
        return True
    player = ctx.resolve_player(params.get("player"))
    return ctx.derive(self_player=player, source=card).test(requirement)


@condition("event_actor_is")
def _event_actor_is(ctx: EffectContext, params: Params) -> bool:
    if ctx.event is None:
        return False
    actor = ctx.event.player
    if actor is None:
        return False
    return PlayerId(str(actor)) in ctx.resolve_players(params.get("player"))


@condition("event_matches")
def _event_matches(ctx: EffectContext, params: Params) -> bool:
    """The Challenge card's gate: "a Hero, an Item or a Magic card, played by
    somebody who isn't me".

    Every clause is optional and they AND together, so a card names only what it
    cares about.
    """
    event = ctx.event
    if event is None:
        return False

    if params.get("card") is not None:
        wanted = ctx.resolve(params["card"])
        if wanted is None or event.card is None or str(event.card) != str(wanted):
            return False

    card_id = event.card
    definition = ctx.definition(CardId(card_id)) if card_id in ctx.state.cards else None

    kinds = _values(ctx.resolve(params.get("kind_in")))
    if kinds and (definition is None or definition.kind not in kinds):
        return False

    classes = _values(ctx.resolve(params.get("class_in")))
    if classes and (definition is None or getattr(definition, "card_class", None) not in classes):
        return False

    tags = set(_values(ctx.resolve(params.get("tag_in"))))
    if tags and (definition is None or not tags & set(definition.tags)):
        return False

    played_by = params.get("played_by")
    if played_by is not None:
        # Either a nested predicate ({op: not_self}) or a player reference.
        if isinstance(played_by, dict) and "op" in played_by:
            if not ctx.test(played_by):
                return False
        elif event.player is None or PlayerId(str(event.player)) not in ctx.resolve_players(
            played_by
        ):
            return False

    return True


@condition("roll_is")
def _roll_is(ctx: EffectContext, params: Params) -> bool:
    """Matches the roll in flight. False when there is none, so a leader's
    "+1 to your hero rolls" simply does not apply outside a roll."""
    roll = ctx.roll
    if roll is None:
        return False
    wanted_kind = params.get("kind")
    if wanted_kind is not None and getattr(roll, "kind", None) not in _values(
        ctx.resolve(wanted_kind)
    ):
        return False
    wanted_roller = params.get("roller")
    if wanted_roller is not None:
        roller = getattr(roll, "roller", None)
        if roller is None or PlayerId(str(roller)) not in ctx.resolve_players(wanted_roller):
            return False
    # `tag:` is how a card says "…on a *successful* roll". A roll has no notion
    # of success; the band that ran does, if its author tagged it. Only true
    # once a band has actually been selected, so this matches on `roll.banded`
    # and not a frame earlier.
    wanted_tag = params.get("tag")
    if wanted_tag is None:
        return True
    return getattr(roll, "band_tag", None) in _values(ctx.resolve(wanted_tag))
