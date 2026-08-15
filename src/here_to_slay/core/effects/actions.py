"""The three ops an action menu is made of: play a card, use an ability, attack.

Each is a *shape*, not a rule. ``play_card_from_hand`` does not know that a Hero
goes to a party and a Magic card goes to the discard — it reads the card's own
``kind`` and its ``equip``/``play`` block and does what those say. That is why
"a Magic card that stays in play" or "an Item you equip to an opponent" are YAML,
not Python.

The path a played card takes (``docs/rules_engine.md §3``)::

    hand ──► limbo ──► card.played ──► [card_played window: a Challenge lands here]
                            │
              cancelled ────┴──► discard, AP not refunded, stop
                            │
                            └──► hero.entered_party / item.equipped / the play block
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.content.schema import EquipDef, PlayDef
from here_to_slay.core.actions import pay_costs
from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import CardId, PlayerId, zone_id
from here_to_slay.core.interpreter import Flow
from here_to_slay.core.registry import effect
from here_to_slay.core.rolls import perform_roll
from here_to_slay.core.windows import cancelled_play

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]

LIMBO = "limbo"
DISCARD = "discard"


def _subject(ctx: EffectContext, params: Params, key: str, intent_field: str) -> CardId:
    """The card an op is about: its own param, or the intent that declared it."""
    if params.get(key) is not None:
        return ctx.resolve_card(params[key])
    if ctx.intent is not None and getattr(ctx.intent, intent_field) is not None:
        return ctx.resolve_card(getattr(ctx.intent, intent_field))
    raise EffectError(f"no card to act on: pass '{key}:' or declare it on the intent")


# ---------------------------------------------------------------------------
# Playing a card
# ---------------------------------------------------------------------------


@effect("play_card_from_hand")
def _play_card_from_hand(ctx: EffectContext, params: Params) -> Flow:
    """Announce a card, let it be challenged, then let its own data resolve it."""
    card = _subject(ctx, params, "card", "card")
    player = ctx.me
    definition = ctx.definition(card)
    kind = ctx.resolve_text(params.get("kind"), definition.kind)
    challengeable = ctx.resolve_bool(params.get("challengeable"), _challengeable(definition))

    if ctx.state.has_zone(LIMBO):
        moved = yield from ctx.emit(
            "card.moved", {"card": card, "to": zone_id(LIMBO)}, actor=player
        )
        if not moved.ok:
            return Outcome.CANCELLED

    played = yield from ctx.emit(
        "card.played",
        {"card": card, "player": player, "kind": kind, "challengeable": challengeable},
        actor=player,
    )
    if not played.ok:
        # Step 7: the card is spent, the action point is not refunded.
        cancelled_play(ctx.state, card)
        return Outcome.CANCELLED

    outcome = yield from _resolve_played(ctx, card, player, params)
    return outcome


def _challengeable(definition: Any) -> bool:
    """What the card itself says. A block that declares nothing is challengeable
    — the base rule — and an action may override either way."""
    for block in (getattr(definition, "play", None), getattr(definition, "equip", None)):
        if block is not None:
            return bool(block.challengeable)
    return True


def _resolve_played(ctx: EffectContext, card: CardId, player: PlayerId, params: Params) -> Flow:
    """Where a played card goes, decided by its own definition."""
    definition = ctx.definition(card)
    equip = getattr(definition, "equip", None)
    play = getattr(definition, "play", None)

    if definition.kind in {"hero", "party_leader"}:
        entered = yield from ctx.emit(
            "hero.entered_party", {"card": card, "player": player}, actor=player
        )
        return Outcome.DONE if entered.ok else Outcome.CANCELLED

    if isinstance(equip, EquipDef):
        outcome = yield from _equip(ctx, card, player, equip, params)
        return outcome

    if isinstance(play, PlayDef):
        outcome = yield from _run_play(ctx, card, player, play)
        return outcome

    raise EffectError(
        f"'{definition.name}' is a {definition.kind} with no 'play' or 'equip' block, "
        f"so there is nothing to do with it once it leaves the hand"
    )


def _equip(
    ctx: EffectContext, item: CardId, player: PlayerId, equip: EquipDef, params: Params
) -> Flow:
    """Attach an Item to a Hero the card's own ``to:`` selector allows."""
    allowed = tuple(ctx.select(equip.to))
    hero = _declared_hero(ctx, params)
    if hero is None:
        if not allowed:
            cancelled_play(ctx.state, item)
            return Outcome.DONE
        chosen = yield from ctx.ask_choose_cards(
            allowed, chooser=player, prompt=f"Equip {ctx.describe(item)} to which Hero?"
        )
        if not chosen:
            cancelled_play(ctx.state, item)
            return Outcome.DONE
        hero = chosen[0]
    elif hero not in allowed:
        raise EffectError(
            f"'{ctx.describe(item)}' cannot be equipped to '{ctx.describe(hero)}' — "
            f"its 'equip.to' does not offer that Hero"
        )

    host = ctx.state.zone(ctx.state.card(hero).zone)
    result = yield from ctx.emit(
        "item.equipped", {"item": item, "hero": hero, "player": host.owner}, actor=player
    )
    if not result.ok:
        cancelled_play(ctx.state, item)
        return Outcome.CANCELLED
    if equip.effect is not None:
        outcome = yield from ctx.bind(hero=hero).run(equip.effect)
        return outcome
    return Outcome.DONE


def _declared_hero(ctx: EffectContext, params: Params) -> CardId | None:
    if params.get("hero") is not None:
        return ctx.resolve_card(params["hero"])
    if ctx.intent is not None and ctx.intent.target is not None:
        return ctx.resolve_card(ctx.intent.target)
    return None


def _run_play(ctx: EffectContext, card: CardId, player: PlayerId, play: PlayDef) -> Flow:
    """A ``play:`` block: an optional roll, an effect, and where the card ends up."""
    if play.roll is not None:
        roll = yield from perform_roll(
            ctx,
            dice=play.roll.dice,
            kind=play.roll.kind,
            roller=ctx.resolve_player(play.roll.roller, default=player),
            source=card,
            outcomes=play.roll.outcomes,
        )
        if roll.cancelled:
            cancelled_play(ctx.state, card)
            return Outcome.CANCELLED

    outcome = Outcome.DONE
    if play.effect is not None:
        outcome = yield from ctx.run(play.effect)

    if play.then is not None:
        # The card says where it goes — a Magic that stays in play, an Item that
        # returns to the hand. Anything it does not move, the default catches.
        yield from ctx.run(play.then)
    if ctx.state.card(card).zone == zone_id(LIMBO):
        cancelled_play(ctx.state, card)
    return outcome


# ---------------------------------------------------------------------------
# Using a Hero's ability
# ---------------------------------------------------------------------------


@effect("use_ability")
def _use_ability(ctx: EffectContext, params: Params) -> Flow:
    """Tap a Hero for its ability: pay, roll if it rolls, then do what it says."""
    card = _subject(ctx, params, "card", "card")
    user = ctx.resolve_player(params.get("user"))
    definition = ctx.definition(card)
    ability = getattr(definition, "ability", None)
    if ability is None:
        raise EffectError(f"'{definition.name}' has no ability to use")

    instance = ctx.state.card(card)
    if ability.once_per_turn and instance.tapped:
        raise EffectError(f"'{definition.name}' has already been used this turn")

    paid = yield from pay_costs(ctx, ability.cost, user)
    if not paid:
        return Outcome.CANCELLED
    if ability.once_per_turn:
        # Marked before resolving: an ability that ends up cancelled still cost
        # the Hero its turn, and the marker is per-instance so it clones and
        # snapshots with the state.
        instance.tapped = True

    if ability.roll is not None:
        roll = yield from perform_roll(
            ctx,
            dice=ability.roll.dice,
            kind=ability.roll.kind,
            roller=ctx.resolve_player(ability.roll.roller, default=user),
            source=card,
            outcomes=ability.roll.outcomes,
        )
        if roll.cancelled:
            return Outcome.CANCELLED

    if ability.effect is not None:
        outcome = yield from ctx.derive(self_player=user, source=card).run(ability.effect)
        return outcome
    return Outcome.DONE


# ---------------------------------------------------------------------------
# Attacking a Monster
# ---------------------------------------------------------------------------


@effect("attack_monster")
def _attack_monster(ctx: EffectContext, params: Params) -> Flow:
    """Roll against a Monster in the row and run the band the total lands in.

    Whether the Monster dies is entirely in its ``outcomes`` — this op never
    compares anything to a threshold.
    """
    monster = _subject(ctx, params, "monster", "target")
    attacker = ctx.resolve_player(params.get("attacker"))
    definition = ctx.definition(monster)
    if definition.kind != "monster":
        raise EffectError(f"'{definition.name}' is a {definition.kind}, not a Monster to attack")

    scope = ctx.derive(self_player=attacker, source=monster)
    if not scope.test(getattr(definition, "requirement", None)):
        raise EffectError(
            f"'{attacker}' does not meet the requirement for '{definition.name}'"
            + (f" ({definition.requirement_text})" if definition.requirement_text else "")
        )

    attacked = yield from scope.emit(
        "monster.attacked", {"monster": monster, "card": monster, "player": attacker},
        actor=attacker,
    )
    if not attacked.ok:
        return Outcome.CANCELLED

    roll = yield from perform_roll(
        scope,
        dice=definition.roll.dice,
        kind=definition.roll.kind,
        roller=ctx.resolve_player(definition.roll.roller, default=attacker),
        source=monster,
        outcomes=definition.roll.outcomes,
    )
    if roll.cancelled:
        return Outcome.CANCELLED

    # "Did it die?" is a question about where the card is now, not about the
    # number — so a band that banishes the Monster somewhere else still counts.
    if ctx.state.card(monster).zone == zone_id("monster_row"):
        yield from scope.emit(
            "monster.failed",
            {"monster": monster, "card": monster, "player": attacker, "total": roll.total},
            actor=attacker,
        )
    return Outcome.DONE
