"""Build a game from content + a player list + a seed.

Everything here is driven by ``rules.yaml``: which zones exist, how big a hand
is, how many monsters sit face up. The only thing this module hardcodes is the
*order* in which the RNG is consumed, and that order is part of the replay
contract, so it is spelled out explicitly:

1. shuffle every shared, ordered, hidden zone, in sorted zone-id order
2. deal one Party Leader to each player, in seat order
3. deal starting hands, one player at a time, in seat order
4. fill the monster row

Change that order and old replay logs stop reproducing — so don't, without
bumping the content hash's meaning.

No events are emitted here. Setup produces a *quiescent* state; the turn
machine (Phase 4) is what starts the first turn.
"""

from __future__ import annotations

from collections.abc import Sequence

from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import DECK_FOR_KIND, ZoneDef
from here_to_slay.core.errors import SetupError
from here_to_slay.core.ids import PlayerId, ZoneId, card_id, player_id, zone_id
from here_to_slay.core.rng import DeterministicRng
from here_to_slay.core.state import CardInstance, GameState, PlayerState
from here_to_slay.core.zones import Zone

#: zone kinds setup deals from, if the rules declare them
LEADER_POOL = "leader_pool"
MAIN_DECK = "main_deck"
MONSTER_DECK = "monster_deck"
MONSTER_ROW = "monster_row"


def new_game(
    content: ContentRegistry,
    players: Sequence[str],
    *,
    seed: int | str = 0,
) -> GameState:
    """Deal a fresh game. Deterministic in ``(content, players, seed)``."""
    rules = content.rules
    if len(players) < rules.setup.min_players:
        raise SetupError(
            f"'{rules.id}' needs at least {rules.setup.min_players} players, got {len(players)}"
        )
    if len(players) > rules.setup.max_players:
        raise SetupError(
            f"'{rules.id}' allows at most {rules.setup.max_players} players, got {len(players)}"
        )
    if len(set(players)) != len(players):
        raise SetupError(f"player names must be unique, got {list(players)}")
    if not rules.zones:
        raise SetupError(f"rule set '{rules.id}' declares no zones")

    seats = [
        PlayerState(id=player_id(index), name=name, seat=index)
        for index, name in enumerate(players)
    ]
    state = GameState(
        content=content,
        players={seat.id: seat for seat in seats},
        turn_order=[seat.id for seat in seats],
        active_player=seats[0].id,
        zones=_build_zones(rules.zones, [seat.id for seat in seats]),
        cards={},
        rng=DeterministicRng(seed=seed),
        phase=rules.phases[0].id if rules.phases else "",
        turn_number=0,
    )

    _mint_cards(state, content)
    _shuffle_decks(state)
    _deal_leaders(state)
    _deal_hands(state)
    _fill_monster_row(state)
    return state


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _build_zones(definitions: Sequence[ZoneDef], seats: Sequence[PlayerId]) -> dict[ZoneId, Zone]:
    """One zone per shared declaration, one per player per player declaration."""
    zones: dict[ZoneId, Zone] = {}
    for definition in definitions:
        owners: Sequence[PlayerId | None] = list(seats) if definition.scope == "player" else [None]
        for owner in owners:
            zone = Zone.from_def(definition, owner)
            if zone.id in zones:
                raise SetupError(f"duplicate zone id '{zone.id}' in rule set")
            zones[zone.id] = zone
    return zones


def _mint_cards(state: GameState, content: ContentRegistry) -> None:
    """Instantiate ``copies`` physical cards per definition into its setup zone.

    Definitions are walked in sorted id order so the pre-shuffle deck is
    identical however the YAML files happened to be named or globbed.
    """
    for def_id in sorted(content.cards):
        definition = content.cards[def_id]
        kind = DECK_FOR_KIND.get(definition.kind, MAIN_DECK)
        target = zone_id(kind)
        if target not in state.zones:
            raise SetupError(
                f"card '{def_id}' is a {definition.kind}, which is dealt into '{kind}', "
                f"but the rule set declares no such shared zone"
            )
        for copy_number in range(1, definition.copies + 1):
            instance = CardInstance(id=card_id(def_id, copy_number), def_id=def_id, zone=target)
            state.register(instance)


def _shuffle_decks(state: GameState) -> None:
    """Shuffle every shared, ordered, hidden zone — data-driven, so a variant's
    own face-down deck is shuffled too, with no edit here."""
    for zone_key in sorted(state.zones):
        zone = state.zones[zone_key]
        if zone.owner is None and zone.ordered and zone.visibility == "hidden" and len(zone) > 1:
            state.rng.shuffle(zone.cards)


def _deal_leaders(state: GameState) -> None:
    """One Party Leader per player.

    An empty pool means this rule set simply has no leaders (a legitimate
    variant), so it is skipped. A *partially* stocked pool is a content bug and
    fails loudly.
    """
    if not state.has_zone(LEADER_POOL):
        return
    pool = state.zone_of(LEADER_POOL)
    if pool.is_empty:
        return
    if state.rules.setup.leader_selection != "random":
        raise SetupError(
            f"leader_selection '{state.rules.setup.leader_selection}' needs the decision "
            f"system (Phase 3+); only 'random' can be dealt at setup"
        )
    if len(pool) < len(state.turn_order):
        raise SetupError(
            f"not enough Party Leaders: {len(pool)} in the pool for {len(state.turn_order)} players"
        )
    for seat in state.turn_order:
        if not state.has_zone("leader", seat):
            raise SetupError("rule set has a leader_pool but no player-scoped 'leader' zone")
        state.move_card(pool.top()[0], zone_id("leader", seat))


def _deal_hands(state: GameState) -> None:
    """Starting hands, one player at a time in seat order."""
    size = state.rules.setup.starting_hand
    if size <= 0 or not state.has_zone(MAIN_DECK):
        return
    deck = state.zone_of(MAIN_DECK)
    needed = size * len(state.turn_order)
    if len(deck) < needed:
        raise SetupError(
            f"the main deck holds {len(deck)} cards, but dealing {size} to "
            f"{len(state.turn_order)} players needs {needed}"
        )
    for seat in state.turn_order:
        if not state.has_zone("hand", seat):
            raise SetupError("rule set declares a starting hand but no player-scoped 'hand' zone")
        # top() snapshots before the moves start shifting the deck underneath
        for card in deck.top(size):
            state.move_card(card, zone_id("hand", seat))


def _fill_monster_row(state: GameState) -> None:
    """Fill the row up to ``monster_row_size``, capped by the zone's capacity.

    Running short is *not* an error: a monster deck empties late in a normal
    game, and the row refills lazily from then on.
    """
    if not (state.has_zone(MONSTER_ROW) and state.has_zone(MONSTER_DECK)):
        return
    row, deck = state.zone_of(MONSTER_ROW), state.zone_of(MONSTER_DECK)
    wanted = state.rules.setup.monster_row_size
    if row.capacity is not None:
        wanted = min(wanted, row.capacity)
    for card in deck.top(max(0, wanted - len(row))):
        state.move_card(card, row.id)
