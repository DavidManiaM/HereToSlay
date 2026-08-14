"""Phase 2: the checker that makes a bad card point at itself."""

from __future__ import annotations

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core.errors import EngineInvariantError
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.invariants import (
    STRICT_ENV,
    check_if_strict,
    check_state,
    find_violations,
    strict_mode,
)
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import GameState

P1, P2 = PlayerId("p1"), PlayerId("p2")


@pytest.fixture
def state(table_content: ContentRegistry) -> GameState:
    return new_game(table_content, ["Ana", "Ben"], seed=4)


def test_a_freshly_dealt_game_is_clean(state: GameState) -> None:
    assert find_violations(state) == []
    check_state(state)  # does not raise


def test_a_card_in_two_zones_is_caught(state: GameState) -> None:
    card = state.zone_of("hand", P1).top()[0]
    state.zone_of("discard").cards.append(card)  # bypassing move_card on purpose
    assert any("2 zones at once" in violation for violation in find_violations(state))


def test_a_card_that_vanished_is_caught(state: GameState) -> None:
    card = state.zone_of("hand", P1).top()[0]
    state.zone_of("hand", P1).cards.remove(card)
    assert any("is in no zone" in violation for violation in find_violations(state))


def test_a_card_that_disagrees_with_its_zone_is_caught(state: GameState) -> None:
    card = state.zone_of("hand", P1).top()[0]
    state.card(card).zone = "discard"  # type: ignore[assignment]
    assert any("claims zone" in violation for violation in find_violations(state))


def test_an_overfull_zone_is_caught(state: GameState) -> None:
    row = state.zone_of("monster_row")
    row.cards.append(state.zone_of("monster_deck").top()[0])
    assert any("over its capacity" in violation for violation in find_violations(state))


def test_negative_action_points_are_caught(state: GameState) -> None:
    state.players[P2].action_points = -1
    assert find_violations(state) == ["player 'p2' has -1 action points"]


def test_a_one_sided_attachment_is_caught(state: GameState) -> None:
    hero, item = state.zone_of("hand", P1).top(2)
    state.card(hero).attachments.append(item)  # no back-link
    assert any("not attached to it" in violation for violation in find_violations(state))


def test_a_broken_seat_table_is_caught(state: GameState) -> None:
    state.active_player = PlayerId("p9")
    assert any("active player" in violation for violation in find_violations(state))


def test_check_state_reports_everything_at_once(state: GameState) -> None:
    state.players[P1].action_points = -2
    state.players[P2].action_points = -3
    with pytest.raises(EngineInvariantError) as exc:
        check_state(state, recent=["turn.started", "card.drawn"])

    assert len(exc.value.violations) == 2
    assert "card.drawn" in str(exc.value)


def test_strict_mode_follows_the_environment(
    state: GameState, monkeypatch: pytest.MonkeyPatch
) -> None:
    state.players[P1].action_points = -1

    monkeypatch.delenv(STRICT_ENV, raising=False)
    assert strict_mode() is False
    check_if_strict(state)  # silent in release builds

    monkeypatch.setenv(STRICT_ENV, "1")
    assert strict_mode() is True
    with pytest.raises(EngineInvariantError):
        check_if_strict(state)
