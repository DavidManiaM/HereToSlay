"""Reaction windows: who is asked, in what order, and what a Challenge does.

The window is the interrupt system, so these tests care about three things:
that nobody is asked a question they cannot answer, that the order is the same
every time, and that a Challenge cancels the play it landed on rather than
merely annotating it.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import Place, empty_hands
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    GameState,
    Interpreter,
    PlayerId,
    ReactionChosen,
    ScriptedSource,
    drive,
    new_game,
    zone_id,
)
from here_to_slay.core.context import EffectContext
from here_to_slay.core.events import Event, EventFrame
from here_to_slay.core.rolls import perform_roll
from here_to_slay.core.windows import open_window, playable_reactions, window_seats


def played_event(card: str, player: str = "p1") -> Event:
    return Event(
        name="card.played",
        payload={"card": card, "player": player, "kind": "hero", "challengeable": True},
        actor=PlayerId(player),
    )


def play_from_hand(state: GameState, card: str, answers: ScriptedSource, **params: Any) -> Any:
    ctx = EffectContext.root(state, player=PlayerId("p1"))
    node = {"op": "play_card_from_hand", "card": card, "kind": "hero", **params}
    return drive(Interpreter(state), ctx.run(node), answers)


class Picks(ScriptedSource):
    """Answers every reaction prompt with the first card it recognises."""

    def __init__(self, *wanted: str) -> None:
        super().__init__([])
        self.wanted = wanted
        self.windows: list[str] = []

    def answer(self, request: Any) -> Any:
        self.seen.append(request)
        self.windows.append(getattr(request, "window", ""))
        offered = [option.card for option in request.options]
        for card in self.wanted:
            if card in offered:
                return ReactionChosen(card)
        return ReactionChosen(None)


class TestWhoIsAsked:
    def test_a_card_for_another_window_is_not_offered(
        self, quiet_state: GameState, place: Place
    ) -> None:
        place(quiet_state, "play.modifier.plus_two", "hand", "p2")
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        assert playable_reactions(ctx, PlayerId("p2"), "card_played", played_event(hero)) == ()

    def test_the_cards_own_condition_decides(self, quiet_state: GameState, place: Place) -> None:
        """A Challenge says "played by somebody who isn't me", so nobody is ever
        offered a Challenge against their own card."""
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        mine = place(quiet_state, "play.challenge.veto", "hand", "p1")
        theirs = place(quiet_state, "play.challenge.veto", "hand", "p2")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        event = played_event(hero)

        assert playable_reactions(ctx, PlayerId("p1"), "card_played", event) == ()
        assert playable_reactions(ctx, PlayerId("p2"), "card_played", event) == (theirs,)
        assert mine in quiet_state.zone(zone_id("hand", PlayerId("p1"))).cards

    def test_an_empty_window_costs_no_prompts(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        script = ScriptedSource([])
        drive(Interpreter(quiet_state), open_window(ctx, "card_played", None), script)
        assert script.seen == []

    def test_a_window_the_rules_do_not_declare_is_a_no_op(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        script = ScriptedSource([])
        drive(Interpreter(quiet_state), open_window(ctx, "no_such_window", None), script)
        assert script.seen == []


class TestOrder:
    def test_seat_left_of_active_asks_the_active_player_last(
        self, table_content: ContentRegistry
    ) -> None:
        """Never a dict or a set: replay depends on this order exactly."""
        state = new_game(table_content, ["Ann", "Bob", "Cid", "Dee"], seed="order")
        state.active_player = PlayerId("p2")

        assert window_seats(state, state.rules.windows["card_played"], None) == (
            "p3",
            "p4",
            "p1",
            "p2",
        )

    def test_it_can_anchor_on_the_actor_instead(self, table_content: ContentRegistry) -> None:
        state = new_game(table_content, ["Ann", "Bob", "Cid", "Dee"], seed="order")
        window = state.rules.windows["card_played"].model_copy(
            update={"order": "seat_left_of_actor"}
        )

        assert window_seats(state, window, played_event("x", "p3")) == ("p4", "p1", "p2", "p3")


class TestChallenge:
    """What a Challenge is *for*: cancelling the event it landed on."""

    def test_it_stops_the_hero_entering_the_party(
        self, quiet_state: GameState, place: Place
    ) -> None:
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        veto = place(quiet_state, "play.challenge.veto", "hand", "p2")

        play_from_hand(quiet_state, hero, Picks(veto))

        assert quiet_state.card(hero).zone == "discard"
        assert quiet_state.card(veto).zone == "discard"
        assert quiet_state.zone(zone_id("party", PlayerId("p1"))).is_empty

    def test_an_unchallenged_hero_reaches_the_party(
        self, quiet_state: GameState, place: Place
    ) -> None:
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        place(quiet_state, "play.challenge.veto", "hand", "p2")

        play_from_hand(quiet_state, hero, Picks())  # everybody passes

        assert quiet_state.card(hero).zone == zone_id("party", PlayerId("p1"))

    def test_a_challenged_card_never_stays_in_limbo(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """Limbo exists so a card is *somewhere* mid-resolution; it must not be
        where a card ends up."""
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        veto = place(quiet_state, "play.challenge.veto", "hand", "p2")

        play_from_hand(quiet_state, hero, Picks(veto))

        assert quiet_state.zone("limbo").is_empty

    @pytest.mark.parametrize("challengeable", [True, False])
    def test_challengeable_false_keeps_the_window_shut(
        self, play_state: GameState, place: Place, challengeable: bool
    ) -> None:
        """The bus does not know the word "challengeable" — ``rules.yaml`` does,
        in the window's ``condition``."""
        empty_hands(play_state)
        hero = place(play_state, "play.hero.lump", "hand", "p1")
        place(play_state, "play.challenge.veto", "hand", "p2")
        script = Picks()

        play_from_hand(play_state, hero, script, challengeable=challengeable)

        assert bool(script.seen) is challengeable


class TestReopening:
    def test_the_window_reopens_after_somebody_acts(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """Two Modifiers in one hand both get played, because acting re-opens
        the poll from the top of the seat order."""
        first = place(quiet_state, "play.modifier.plus_two", "hand", "p2")
        second = place(quiet_state, "play.modifier.plus_two", "hand", "p2")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        rolls: list[Any] = []

        def flow() -> Any:
            rolls.append((yield from perform_roll(ctx, dice="2d6", kind="hero_ability")))

        drive(Interpreter(quiet_state), flow(), Picks(first, second))

        played = [
            modifier for modifier in rolls[0].modifiers if modifier.source in {first, second}
        ]
        assert [modifier.amount for modifier in played] == [2, 2]


class TestDepthCap:
    def test_a_window_declines_to_open_at_the_cap(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """The cap ends a chain gracefully: a game somebody is playing should
        stop being answerable, not crash."""
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        place(quiet_state, "play.challenge.veto", "hand", "p2")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        ctx.execution.stack.extend(
            EventFrame(event=played_event(hero))
            for _ in range(quiet_state.rules.max_reaction_depth)
        )
        script = ScriptedSource([])

        drive(Interpreter(quiet_state), open_window(ctx, "card_played", None), script)

        assert script.seen == []
