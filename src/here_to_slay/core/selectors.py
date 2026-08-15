"""The selector catalogue — "which things is this card talking about?".

A selector answers with **ids**, and only the base set: ``exclude``, ``where``
and ``limit`` are applied by :meth:`EffectContext.select` for every selector at
once, so a new selector in a variant gets all three for free and cannot get
them subtly wrong.

The typed shortcuts (``heroes``, ``items``, ``monsters``) are sugar over
``cards`` + a filter. They exist because ``{selector: heroes, of: $victim}``
is what a card actually says, and making every card spell out
``{selector: cards, of: {player: $victim, zone: party},
where: {op: card_kind_is, kind: hero}}`` would be a tax on the common case.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from here_to_slay.core.errors import EffectError
from here_to_slay.core.ids import CardId, PlayerId, zone_id
from here_to_slay.core.registry import selector

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

Params = dict[str, Any]


def _players_from(
    ctx: EffectContext, params: Params, *, default_all: bool = False
) -> tuple[PlayerId, ...]:
    """``of:`` as a player list — one seat, several, or everybody."""
    default = tuple(ctx.state.turn_order) if default_all else (ctx.me,)
    return ctx.resolve_players(params.get("of"), default=default)


def _cards_of_kind(
    ctx: EffectContext, players: Sequence[PlayerId], zone: str, kind: str
) -> tuple[CardId, ...]:
    return tuple(
        instance.id
        for player in players
        for instance in ctx.state.cards_in(zone_id(zone, player))
        if ctx.state.definition(instance).kind == kind
    )


@selector("players")
def _players(ctx: EffectContext, params: Params) -> tuple[PlayerId, ...]:
    """Every seat, in turn order. ``exclude: [$self]`` is the usual companion."""
    return tuple(ctx.state.turn_order)


@selector("opponents")
def _opponents(ctx: EffectContext, params: Params) -> tuple[PlayerId, ...]:
    """Everyone but ``of`` (default ``$self``), in seat order from their left."""
    return ctx.opponents(ctx.resolve_player(params.get("of")))


@selector("cards")
def _cards(ctx: EffectContext, params: Params) -> tuple[CardId, ...]:
    """Cards in a zone: ``{selector: cards, of: {player: $self, zone: hand}}``."""
    reference = params.get("of") if params.get("of") is not None else params.get("zone")
    if reference is None:
        raise EffectError("the 'cards' selector needs 'of' to name a zone")
    return tuple(instance.id for instance in ctx.state.cards_in(ctx.resolve_zone(reference)))


@selector("heroes")
def _heroes(ctx: EffectContext, params: Params) -> tuple[CardId, ...]:
    return _cards_of_kind(ctx, _players_from(ctx, params), "party", "hero")


@selector("items")
def _items(ctx: EffectContext, params: Params) -> tuple[CardId, ...]:
    """Items in play — equipped ones live in the party zone beside their host."""
    return _cards_of_kind(ctx, _players_from(ctx, params), "party", "item")


@selector("monsters")
def _monsters(ctx: EffectContext, params: Params) -> tuple[CardId, ...]:
    """Monsters ``of`` a player are the ones they have slain; with no ``of``,
    the face-up row everyone can attack."""
    if params.get("of") is None:
        return tuple(instance.id for instance in ctx.state.cards_in(zone_id("monster_row")))
    return _cards_of_kind(ctx, _players_from(ctx, params), "slain", "monster")


@selector("monster_row")
def _monster_row(ctx: EffectContext, params: Params) -> tuple[CardId, ...]:
    return tuple(instance.id for instance in ctx.state.cards_in(zone_id("monster_row")))


@selector("party_leaders")
def _party_leaders(ctx: EffectContext, params: Params) -> tuple[CardId, ...]:
    return tuple(
        instance.id
        for player in _players_from(ctx, params, default_all=True)
        for instance in ctx.state.cards_in(zone_id("leader", player))
    )
