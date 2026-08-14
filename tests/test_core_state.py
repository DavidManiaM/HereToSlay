"""Phase 2: the state primitives — moving cards, cloning, snapshotting."""

from __future__ import annotations

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core.errors import EngineError, ZoneCapacityError, ZoneError
from here_to_slay.core.ids import PlayerId, zone_id
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import GameState, diff_snapshots

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")


@pytest.fixture
def state(table_content: ContentRegistry) -> GameState:
    return new_game(table_content, ["Ana", "Ben", "Cy"], seed=17)


# ---------------------------------------------------------------------------
# move_card: the one write path
# ---------------------------------------------------------------------------


def test_moving_updates_both_zones_and_the_card(state: GameState) -> None:
    card = state.zone_of("hand", P1).top()[0]
    state.move_card(card, zone_id("party", P1))

    assert card not in state.zone_of("hand", P1)
    assert card in state.zone_of("party", P1)
    assert state.card(card).zone == "party:p1"


def test_control_follows_location_but_ownership_does_not(state: GameState) -> None:
    """A stolen Hero is controlled by the thief and still owned by its player —
    which is what lets a variant say 'return stolen Heroes at end of turn'."""
    card = state.zone_of("hand", P1).top()[0]
    state.move_card(card, zone_id("party", P1))
    assert state.card(card).owner == P1

    state.move_card(card, zone_id("party", P2))
    assert state.card(card).controller == P2
    assert state.card(card).owner == P1

    state.move_card(card, "discard")
    assert state.card(card).controller is None
    assert state.card(card).owner == P1


def test_control_can_be_left_alone(state: GameState) -> None:
    card = state.zone_of("hand", P1).top()[0]
    state.move_card(card, "limbo", set_control=False)
    assert state.card(card).controller == P1


def test_a_rejected_move_does_not_lose_the_card(state: GameState) -> None:
    """The monster row is capacity 3. An overflowing move must roll back."""
    row = state.zone_of("monster_row")
    assert row.is_full
    spare = state.zone_of("monster_deck").top()[0]

    with pytest.raises(ZoneCapacityError):
        state.move_card(spare, row.id)

    assert spare in state.zone_of("monster_deck")
    assert state.card(spare).zone == "monster_deck"


def test_random_position_is_logged(state: GameState) -> None:
    card = state.zone_of("hand", P1).top()[0]
    before = state.rng.advances
    state.move_card(card, "main_deck", "random")
    assert state.rng.advances == before + 1
    assert card in state.zone_of("main_deck")


def test_unknown_zones_and_cards_raise_clearly(state: GameState) -> None:
    with pytest.raises(ZoneError):
        state.zone("vault")
    with pytest.raises(EngineError):
        state.card("table.hero.bard#99")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def test_attach_and_detach_keep_both_ends_true(state: GameState) -> None:
    hero, item = state.zone_of("hand", P1).top(2)
    state.attach(item, hero)
    assert state.card(hero).attachments == [item]
    assert state.card(item).attached_to == hero

    with pytest.raises(EngineError):
        state.attach(item, hero)

    assert state.detach(item) == hero
    assert state.card(hero).attachments == []
    assert state.card(item).attached_to is None


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------


def test_seat_order_starts_left_of_the_anchor(state: GameState) -> None:
    assert state.seat_order_from(P1) == (P2, P3)
    assert state.seat_order_from(P3) == (P1, P2)
    assert state.seat_order_from(P2, include_start=True) == (P2, P3, P1)


def test_seat_order_defaults_to_the_active_player(state: GameState) -> None:
    assert state.active_player == P1
    assert state.seat_order_from() == state.opponents_of(P1)
    assert state.next_player() == P2


def test_action_points_reads_the_active_seat(state: GameState) -> None:
    state.players[P1].action_points = 3
    assert state.action_points == 3


# ---------------------------------------------------------------------------
# Clone, snapshot, diff
# ---------------------------------------------------------------------------


def test_clone_is_fully_independent(state: GameState) -> None:
    twin = state.clone()
    card = twin.zone_of("hand", P1).top()[0]
    twin.move_card(card, "discard")
    twin.players[P1].action_points = 9
    twin.flags["corruption"] = 1
    twin.card(twin.zone_of("hand", P2).top()[0]).state["marked"] = True

    assert card in state.zone_of("hand", P1)
    assert state.players[P1].action_points == 0
    assert state.flags == {}
    assert state.snapshot() != twin.snapshot()


def test_clone_shares_immutable_content(state: GameState) -> None:
    assert state.clone().content is state.content


def test_snapshots_are_equal_for_equal_states(state: GameState) -> None:
    assert state.clone().snapshot() == state.snapshot()
    assert diff_snapshots(state.snapshot(), state.clone().snapshot()) == []


def test_diff_names_what_changed(state: GameState) -> None:
    before = state.snapshot()
    state.players[P2].action_points = 2
    differences = diff_snapshots(before, state.snapshot())
    assert differences == ["players.p2.action_points: 0 -> 2"]


def test_definitions_are_reachable_from_an_instance(state: GameState) -> None:
    card = state.zone_of("leader", P1).top()[0]
    assert state.definition(card).kind == "party_leader"
    assert state.definition(state.card(card)).id == state.card(card).def_id
