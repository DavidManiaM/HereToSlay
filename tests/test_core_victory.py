"""Victory is a loop over ``rules.victory``, never a Python comparison to 3."""

from __future__ import annotations

from conftest import Place
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    GameState,
    Interpreter,
    PlayerId,
    ScriptedSource,
    drive,
    new_game,
    zone_id,
)
from here_to_slay.core.context import EffectContext
from here_to_slay.core.victory import check_order, check_victory, find_winner, satisfied_by


def slay(state: GameState, player: str, count: int) -> None:
    """Move ``count`` Monsters into a seat's slain pile, however they got there."""
    monsters = [
        instance.id
        for instance in state.cards.values()
        if state.definition(instance).kind == "monster"
    ]
    for card in monsters[:count]:
        state.move_card(card, zone_id("slain", PlayerId(player)))


class TestConditions:
    def test_the_shipping_condition_is_data(self, play_state: GameState) -> None:
        """"Slay 3 Monsters" is a `slain_count` node in rules.yaml."""
        assert satisfied_by(play_state, PlayerId("p1")) == ()

        slay(play_state, "p1", 3)

        met = satisfied_by(play_state, PlayerId("p1"))
        assert [victory.id for victory in met] == ["slay_three"]

    def test_two_short_is_not_a_win(self, play_state: GameState) -> None:
        slay(play_state, "p1", 2)
        assert find_winner(play_state) is None

    def test_a_full_party_wins_by_the_other_condition(
        self, table_state: GameState, place: Place
    ) -> None:
        """``party_covers_all_classes`` reads ``rules.classes``, so a variant
        with a seventh class needs no code — only one more Hero to win."""
        classes = list(table_state.rules.classes)
        for card_class in classes[:-1]:
            place(table_state, f"table.hero.{card_class}", "party", "p1")
        assert find_winner(table_state) is None

        place(table_state, f"table.hero.{classes[-1]}", "party", "p1")

        victory = find_winner(table_state)
        assert victory is not None and victory.condition.id == "full_party"

    def test_the_winner_carries_the_condition_it_won_by(self, play_state: GameState) -> None:
        slay(play_state, "p2", 3)
        victory = find_winner(play_state)
        assert victory is not None
        assert victory.player == "p2" and victory.text == "Slay 3 Monsters"


class TestTiebreak:
    def test_the_active_player_is_examined_first(self, play_state: GameState) -> None:
        """``tiebreak: active_player`` means simultaneous wins go to whoever's
        card caused them."""
        slay(play_state, "p1", 3)
        slay(play_state, "p2", 3)
        play_state.active_player = PlayerId("p2")

        assert check_order(play_state)[0] == "p2"
        victory = find_winner(play_state)
        assert victory is not None and victory.player == "p2"


class TestEndingTheGame:
    def test_it_ends_through_the_same_events_a_card_would_use(
        self, play_state: GameState
    ) -> None:
        slay(play_state, "p1", 3)
        ctx = EffectContext.root(play_state, player=PlayerId("p1"))

        drive(Interpreter(play_state), check_victory(ctx), ScriptedSource([]))

        assert play_state.winner == "p1"
        assert [event.name for event in ctx.execution.history] == ["player.won", "game.ended"]

    def test_a_game_already_won_is_not_won_twice(self, play_state: GameState) -> None:
        play_state.winner = PlayerId("p2")
        slay(play_state, "p1", 3)
        ctx = EffectContext.root(play_state, player=PlayerId("p1"))

        drive(Interpreter(play_state), check_victory(ctx), ScriptedSource([]))

        assert play_state.winner == "p2"
        assert ctx.execution.history == []

    def test_nothing_happens_when_nobody_has_won(self, play_state: GameState) -> None:
        ctx = EffectContext.root(play_state, player=PlayerId("p1"))
        drive(Interpreter(play_state), check_victory(ctx), ScriptedSource([]))
        assert play_state.winner is None


def test_a_rule_set_can_have_no_victory_conditions(small_content: ContentRegistry) -> None:
    """A sandbox variant is legal; it simply never ends by itself."""
    state = new_game(small_content, ["Ann", "Bob"], seed="sandbox")
    object.__setattr__(state.content.rules, "victory", [])
    try:
        assert find_winner(state) is None
    finally:
        object.__setattr__(state.content.rules, "victory", list(small_content.rules.victory))
