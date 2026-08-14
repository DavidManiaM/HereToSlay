"""Phase 2 acceptance: setup deals the right game, and deals it the same way twice."""

from __future__ import annotations

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core.errors import SetupError
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.invariants import find_violations
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import diff_snapshots

P1, P2, P3, P4 = (PlayerId(f"p{n}") for n in range(1, 5))
SEATS = ["Ana", "Ben", "Cy", "Dee"]


@pytest.fixture
def table(table_content: ContentRegistry):
    return new_game(table_content, SEATS, seed=2024)


# ---------------------------------------------------------------------------
# Deck arithmetic, from the shipping rule set
# ---------------------------------------------------------------------------


def test_zones_exist_for_every_declaration(table) -> None:
    shared = {zone.kind for zone in table.zones.values() if zone.owner is None}
    assert shared == {"main_deck", "discard", "monster_deck", "monster_row", "leader_pool", "limbo"}
    for seat in table.turn_order:
        for kind in ("hand", "party", "leader", "slain"):
            assert table.has_zone(kind, seat)


def test_hands_and_row_match_the_rules(table) -> None:
    rules = table.rules.setup
    for seat in table.turn_order:
        assert len(table.zone_of("hand", seat)) == rules.starting_hand == 5
        assert len(table.zone_of("leader", seat)) == 1
        assert len(table.zone_of("party", seat)) == 0
    assert len(table.zone_of("monster_row")) == rules.monster_row_size == 3


def test_deck_sizes_add_up(table) -> None:
    #  32 main deck - 4 players x 5 cards; 6 monsters - 3 face up; 6 leaders - 4 dealt
    assert len(table.zone_of("main_deck")) == 12
    assert len(table.zone_of("monster_deck")) == 3
    assert len(table.zone_of("leader_pool")) == 2
    assert len(table.zone_of("discard")) == 0


def test_no_card_is_lost_in_the_deal(table) -> None:
    assert len(table.cards) == sum(len(zone) for zone in table.zones.values()) == 44


def test_leaders_are_party_leaders_and_owned(table) -> None:
    for seat in table.turn_order:
        leader = table.cards_in(table.zone_of("leader", seat))[0]
        assert table.definition(leader).kind == "party_leader"
        assert leader.owner == leader.controller == seat


def test_only_the_right_kinds_reach_each_deck(table) -> None:
    kinds = {table.definition(c).kind for c in table.cards_in("monster_deck")}
    assert kinds == {"monster"}
    kinds = {table.definition(c).kind for c in table.cards_in("main_deck")}
    assert kinds <= {"hero", "item", "magic", "modifier", "challenge"}


def test_the_state_starts_quiescent_and_hashed(table, table_content: ContentRegistry) -> None:
    assert table.turn_number == 0
    assert table.phase == "turn_start"  # the first phase in the base table
    assert table.active_player == P1
    assert table.winner is None
    assert all(seat.action_points == 0 for seat in table.players.values())
    assert table.content_hash == table_content.content_hash


def test_setup_leaves_no_invariant_broken(table) -> None:
    assert find_violations(table) == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_seed_deals_the_same_game(table_content: ContentRegistry) -> None:
    a = new_game(table_content, SEATS, seed=2024)
    b = new_game(table_content, SEATS, seed=2024)
    assert diff_snapshots(a.snapshot(), b.snapshot()) == []


def test_a_different_seed_deals_a_different_game(table_content: ContentRegistry) -> None:
    a = new_game(table_content, SEATS, seed=1)
    b = new_game(table_content, SEATS, seed=2)
    assert a.zone_of("main_deck").cards != b.zone_of("main_deck").cards


def test_string_seeds_work(table_content: ContentRegistry) -> None:
    a = new_game(table_content, SEATS, seed="dragons")
    b = new_game(table_content, SEATS, seed="dragons")
    assert diff_snapshots(a.snapshot(), b.snapshot()) == []


def test_the_deal_does_not_depend_on_yaml_ordering(table_content: ContentRegistry) -> None:
    """Cards are minted in sorted id order, so renaming a card file cannot
    reshuffle an existing seed's game."""
    ids = [card.id for card in new_game(table_content, SEATS, seed=5).cards.values()]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_player_count_is_checked_against_the_rules(table_content: ContentRegistry) -> None:
    with pytest.raises(SetupError, match="at least 2"):
        new_game(table_content, ["Solo"])
    with pytest.raises(SetupError, match="at most 6"):
        new_game(table_content, [f"P{n}" for n in range(7)])


def test_duplicate_player_names_are_refused(table_content: ContentRegistry) -> None:
    with pytest.raises(SetupError, match="unique"):
        new_game(table_content, ["Ana", "Ana"])


def test_a_deck_too_small_to_deal_says_so(base_content: ContentRegistry) -> None:
    """The base pack has no cards until Phase 6 — the failure should name the
    shortfall, not raise an IndexError somewhere in the deal."""
    with pytest.raises(SetupError, match="main deck holds 0 cards"):
        new_game(base_content, ["Ana", "Ben"])


# ---------------------------------------------------------------------------
# A second rule set, to prove nothing above is hardcoded
# ---------------------------------------------------------------------------


def test_a_variant_rule_set_deals_its_own_numbers(small_content: ContentRegistry) -> None:
    state = new_game(small_content, ["Ana", "Ben"], seed=3)
    assert state.rules.id == "fixture"
    assert len(state.zone_of("hand", P1)) == 2  # starting_hand: 2
    assert len(state.zone_of("monster_row")) == 1  # monster_row_size: 1
    assert len(state.zone_of("main_deck")) == 2  # 2 heroes + 4 modifiers - 4 dealt
    assert find_violations(state) == []
