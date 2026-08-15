"""Ops about the board: parties, equipment, monsters.

Every one of these is a thin arrangement of *choose a target* + *emit an
event*. That thinness is deliberate: "steal a Hero" differs from "destroy a
Hero" only in where the card lands, so both are one emit with a different
destination, and a variant's "banish a Hero to the bottom of the deck" is a
third destination rather than a third code path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import CardId, PlayerId, zone_id
from here_to_slay.core.interpreter import Flow
from here_to_slay.core.registry import effect

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]


def _party_cards(
    ctx: EffectContext, player: PlayerId, kind: str, filter_node: Any
) -> tuple[CardId, ...]:
    return tuple(
        instance.id
        for instance in ctx.state.cards_in(zone_id("party", player))
        if ctx.state.definition(instance).kind == kind
        and (filter_node is None or ctx.matches(filter_node, instance.id))
    )


def _choose_hero(
    ctx: EffectContext, victim: PlayerId, chooser: PlayerId, params: Params, prompt: str
) -> Flow:
    pool = _party_cards(ctx, victim, "hero", params.get("filter"))
    if not pool:
        return None
    chosen = yield from ctx.ask_choose_cards(
        pool,
        chooser=chooser,
        minimum=1,
        maximum=1,
        prompt=prompt,
        from_zone=zone_id("party", victim),
    )
    return chosen[0] if chosen else None


@effect("steal_hero")
def _steal_hero(ctx: EffectContext, params: Params) -> Flow:
    """Take a Hero from another party into your own.

    Two events, one physical move: the Hero *leaves* one party and *enters*
    another, and cards react to those separately ("when a Hero leaves your
    party, draw"). The mutators are idempotent about the destination, so the
    second emit announces without moving anything twice.
    """
    thief = ctx.resolve_player(params.get("to"))
    chooser = ctx.resolve_player(params.get("chooser"), default=thief)
    victim = ctx.resolve_player(params.get("from"))
    if victim == thief:
        raise EffectError("'steal_hero' needs a 'from' player other than the thief")

    hero = yield from _choose_hero(ctx, victim, chooser, params, f"Steal a Hero from {victim}")
    if hero is None:
        return Outcome.DONE

    destination = zone_id("party", thief)
    result = yield from ctx.emit(
        "hero.left_party", {"card": hero, "player": victim, "to": destination}, actor=thief
    )
    if not result.ok:
        return Outcome.CANCELLED
    yield from ctx.emit("hero.entered_party", {"card": hero, "player": thief}, actor=thief)
    return Outcome.DONE


@effect("destroy_hero")
def _destroy_hero(ctx: EffectContext, params: Params) -> Flow:
    """Send a Hero from a party to the discard."""
    victim = ctx.resolve_player(params.get("target"))
    chooser = ctx.resolve_player(params.get("chooser"))
    hero = yield from _choose_hero(ctx, victim, chooser, params, f"Destroy a Hero of {victim}")
    if hero is None:
        return Outcome.DONE
    result = yield from ctx.emit(
        "hero.left_party", {"card": hero, "player": victim, "to": zone_id("discard")}
    )
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("sacrifice")
def _sacrifice(ctx: EffectContext, params: Params) -> Flow:
    """Destroy one of your *own* Heroes — a cost, not an attack, so the owner
    always chooses."""
    owner = ctx.resolve_player(params.get("target"))
    hero = yield from _choose_hero(ctx, owner, owner, params, "Sacrifice a Hero")
    if hero is None:
        return Outcome.DONE
    result = yield from ctx.emit(
        "hero.left_party", {"card": hero, "player": owner, "to": zone_id("discard")}, actor=owner
    )
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("equip_item")
def _equip_item(ctx: EffectContext, params: Params) -> Flow:
    """Attach an Item to a Hero. The Item follows its host into the party."""
    item = ctx.resolve_card(params.get("item"))
    if params.get("hero") is not None:
        hero = ctx.resolve_card(params["hero"])
    else:
        owner = ctx.me
        pool = _party_cards(ctx, owner, "hero", params.get("filter"))
        if not pool:
            return Outcome.DONE
        chosen = yield from ctx.ask_choose_cards(pool, chooser=owner, prompt="Equip which Hero?")
        if not chosen:
            return Outcome.DONE
        hero = chosen[0]

    host = ctx.state.zone(ctx.state.card(hero).zone)
    result = yield from ctx.emit(
        "item.equipped", {"item": item, "hero": hero, "player": host.owner}
    )
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("unequip_item")
def _unequip_item(ctx: EffectContext, params: Params) -> Flow:
    item = ctx.resolve_card(params.get("item"))
    destination = (
        ctx.resolve_zone(params["to_zone"])
        if params.get("to_zone") is not None
        else zone_id("discard")
    )
    result = yield from ctx.emit("item.unequipped", {"item": item, "to": destination})
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("slay_monster")
def _slay_monster(ctx: EffectContext, params: Params) -> Flow:
    """Move a Monster to a player's slain pile and pay out its reward.

    The row is *not* refilled here. When a new Monster turns up is policy — the
    base game does it immediately, a variant might do it at end of turn — so it
    is a ``refill_monster_row`` step in ``rules.yaml``, not a line of Python.
    """
    monster = ctx.resolve_card(params.get("monster"))
    slayer = ctx.resolve_player(params.get("by"))
    result = yield from ctx.emit(
        "monster.slain", {"monster": monster, "card": monster, "by": slayer}, actor=slayer
    )
    if not result.ok:
        return Outcome.CANCELLED

    reward = getattr(ctx.definition(monster), "on_slay", None)
    if reward is not None:
        outcome = yield from ctx.derive(self_player=slayer, source=monster).run(reward)
        return outcome
    return Outcome.DONE


@effect("return_monster")
def _return_monster(ctx: EffectContext, params: Params) -> Flow:
    monster = ctx.resolve_card(params.get("monster"))
    destination = (
        ctx.resolve_zone(params["to"]) if params.get("to") is not None else zone_id("monster_deck")
    )
    result = yield from ctx.emit("card.moved", {"card": monster, "to": destination})
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@effect("refill_monster_row")
def _refill_monster_row(ctx: EffectContext, params: Params) -> Flow:
    """Top the row back up from the Monster deck.

    Running short is not an error — a deck empties late in a normal game, and
    the row simply stays small.
    """
    row = ctx.state.zone(zone_id("monster_row"))
    deck = ctx.state.zone(zone_id("monster_deck"))
    wanted = ctx.rules.setup.monster_row_size
    if row.capacity is not None:
        wanted = min(wanted, row.capacity)

    # Snapshot before moving: the deck shifts under us as cards leave it.
    added = deck.top(max(0, wanted - len(row)))
    for card in added:
        result = yield from ctx.emit("card.moved", {"card": card, "to": row.id})
        if not result.ok:
            return Outcome.CANCELLED
    if added:
        yield from ctx.emit("monster_row.refilled", {"cards": list(added)})
    return Outcome.DONE
