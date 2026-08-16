"""Phase 2 acceptance: a view never leaks a card the seat may not see."""

from __future__ import annotations

import json

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import GameState
from here_to_slay.core.view import build_view, hidden_card_ids

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")


@pytest.fixture
def state(table_content: ContentRegistry) -> GameState:
    return new_game(table_content, ["Ana", "Ben", "Cy"], seed=8)


# ---------------------------------------------------------------------------
# The property that matters
# ---------------------------------------------------------------------------


def test_no_seat_can_name_a_card_it_cannot_see(state: GameState) -> None:
    """Serialise the whole view and search it for every hidden id. This is the
    check that has to keep passing when the view grows new fields."""
    for seat in state.turn_order:
        blob = json.dumps(build_view(state, seat).as_data())
        leaked = [card for card in hidden_card_ids(state, seat) if card in blob]
        assert not leaked, f"{seat} can see {leaked}"


def test_hidden_zones_report_a_size_and_nothing_else(state: GameState) -> None:
    deck = build_view(state, P1).zone("main_deck")
    assert deck is not None
    assert deck.size == len(state.zone_of("main_deck"))
    assert deck.cards == ()
    assert deck.revealed is False
    assert deck.hidden_count == deck.size


def test_you_see_your_own_hand_but_not_theirs(state: GameState) -> None:
    view = build_view(state, P1)
    mine = view.zone("hand", P1)
    theirs = view.zone("hand", P2)
    assert mine is not None and theirs is not None

    assert len(mine.cards) == mine.size == 5
    assert {card.def_id for card in mine.cards}
    assert theirs.cards == ()
    assert theirs.size == 5  # how many cards they hold is public; which ones is not


def test_public_zones_are_visible_to_everyone(state: GameState) -> None:
    for seat in state.turn_order:
        view = build_view(state, seat)
        row = view.zone("monster_row")
        leader = view.zone("leader", P2)
        assert row is not None and leader is not None
        assert len(row.cards) == 3
        assert len(leader.cards) == 1


# ---------------------------------------------------------------------------
# Shape, for the renderers
# ---------------------------------------------------------------------------


def test_the_view_carries_the_board_state(state: GameState) -> None:
    view = build_view(state, P2)
    assert view.seat == P2
    assert view.you.is_you and view.you.name == "Ben"
    assert view.active_player == P1
    assert view.is_your_turn is False
    assert view.players[P1].is_active
    assert view.content_hash == state.content_hash
    assert view.zone("monster_row") is not None and view.zone("monster_row").capacity == 3


def test_opponents_are_listed_in_seat_order(state: GameState) -> None:
    assert [player.id for player in build_view(state, P2).opponents()] == [P3, P1]
    assert [player.id for player in build_view(state, P3).opponents()] == [P1, P2]


def test_card_views_expose_only_rendering_facts(state: GameState) -> None:
    card = build_view(state, P1).zone("leader", P1).cards[0]
    # Which pack the leader came from is not the point (the fixture stacks on
    # base, so it may be either); that a view carries the def id is.
    assert state.content.cards[card.def_id].kind == "party_leader"
    assert card.owner == P1
    assert card.tapped is False


def test_an_unknown_seat_is_an_error(state: GameState) -> None:
    with pytest.raises(KeyError):
        build_view(state, PlayerId("p9"))


def test_the_view_is_a_projection_not_a_handle(state: GameState) -> None:
    """Mutating a view must never reach the state — the UI has exactly one
    write path, and it is engine.submit()."""
    view = build_view(state, P1)
    view.flags["cheat"] = True
    view.you.zones["hand"].cards[0].state["cheat"] = True

    assert state.flags == {}
    assert all(not card.state for card in state.cards.values())
