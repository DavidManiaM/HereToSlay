"""New verbs for the Overclock variant — one of each kind the engine has.

Five registries, five additions, none of which required an engine edit:

    @plugin.effect     upload_card   move a card into your cache, announcing it
    @plugin.mutator    cache.uploaded  the one place that move happens
    @plugin.condition  cache_size    "four cards cached" — the new win condition
    @plugin.selector   cached        what the `download` action may target
    @plugin.cost       cache_burn    pay with cached cards instead of actions

Each of them talks to the engine only through ``EffectContext`` and
``GameState``, which is the whole contract a plugin gets. Nothing here imports
anything private, and nothing here knows what the *rest* of the pack does with
these ops — rules.yaml decides that.
"""

from __future__ import annotations

from typing import Any

from here_to_slay.core.conditions.logic import compare_values
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import zone_id
from here_to_slay.core.mutators import move_to
from here_to_slay.modding import Plugin, Role

plugin = Plugin("overclock", doc="cache, latency, and a second way to win")

#: the zone rules.yaml declares. Named once here so a rename is one edit.
CACHE = "cache"
DISCARD = "discard"

#: the event `upload_card` announces and the `cache_upload` window opens on
UPLOADED = "cache.uploaded"


# ---------------------------------------------------------------------------
# effect
# ---------------------------------------------------------------------------


@plugin.effect(
    "upload_card",
    params={"card": (Role.REF, True), "player": Role.REF},
    doc="move a card from hand into that player's cache, challengeably",
)
def upload_card(ctx: Any, params: dict[str, Any]) -> Any:
    """Announce the upload, then let the mutator perform it.

    Emitting rather than moving is what buys the Firewall card its window: the
    bus opens ``cache_upload`` inside this dispatch, so a reaction played there
    cancels the upload before it ever happens. Moving the card directly would
    leave a Firewall with nothing to stop.
    """
    card = ctx.resolve_card(params.get("card"))
    player = ctx.resolve_player(params.get("player"))

    result = yield from ctx.emit(UPLOADED, {"card": card, "player": player}, actor=player)
    if result.ok:
        return Outcome.DONE
    # `cancel_event: {and_discard: true}` — the Firewall burns what it blocked.
    # Same contract the base game's cancelled play honours, and the same reason:
    # being blocked is a real cost, so the card does not go back to the hand.
    if result.discard_source and ctx.state.has_zone(DISCARD):
        move_to(ctx.state, card, zone_id(DISCARD))
    return Outcome.CANCELLED


# ---------------------------------------------------------------------------
# mutator
# ---------------------------------------------------------------------------


@plugin.mutator(UPLOADED)
def uploaded(state: Any, event: Any) -> None:
    """RESOLVE for ``cache.uploaded``: the single place a card enters a cache.

    Registering a mutator is also what declares the event name to the
    validator, which is why ``rules.yaml`` may open a window on it.
    """
    move_to(state, event["card"], zone_id(CACHE, event["player"]))


# ---------------------------------------------------------------------------
# condition
# ---------------------------------------------------------------------------


@plugin.condition(
    "cache_size",
    params={"player": Role.REF, "cmp": Role.VALUE, "value": Role.VALUE},
    doc="how many cards that player has cached",
)
def cache_size(ctx: Any, params: dict[str, Any]) -> bool:
    """``{op: cache_size, player: $player, cmp: ">=", value: 4}``.

    Conditions are pure and may not ask a question — this one only counts.
    """
    player = ctx.resolve_player(params.get("player"))
    if not ctx.state.has_zone(CACHE, player):
        return False
    count = len(ctx.state.zone_of(CACHE, player))
    return compare_values(count, str(params.get("cmp", ">=")), ctx.resolve_int(params.get("value")))


# ---------------------------------------------------------------------------
# selector
# ---------------------------------------------------------------------------


@plugin.selector(
    "cached",
    params={"of": Role.REF},
    doc="the cards in a player's cache, oldest first",
)
def cached(ctx: Any, params: dict[str, Any]) -> tuple[Any, ...]:
    """Selectors yield ids, never live objects — see architecture_notes.md 5."""
    players = ctx.resolve_players(params.get("of"), default=(ctx.me,))
    return tuple(
        instance.id
        for player in players
        if ctx.state.has_zone(CACHE, player)
        for instance in ctx.state.cards_in(zone_id(CACHE, player))
    )


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


@plugin.cost("cache_burn")
def cache_burn(ctx: Any, params: dict[str, Any]) -> bool:
    """Pay by discarding the oldest cards in your cache.

    ``check_only`` must be answered from state alone: ``legal_intents()`` calls
    every cost once per frame to decide what the menu offers, and a cost that
    stopped to ask a question there would deadlock the UI. So the *oldest*
    cards go, rather than ones the player picks.
    """
    player = ctx.resolve_player(params.get("player"))
    amount = ctx.resolve_int(params.get("amount"), 0)
    if not ctx.state.has_zone(CACHE, player):
        return False
    zone = ctx.state.zone_of(CACHE, player)
    if len(zone) < amount:
        return False
    if params.get("check_only"):
        return True
    for card in zone.top(amount):
        move_to(ctx.state, card, zone_id(DISCARD))
    return True
