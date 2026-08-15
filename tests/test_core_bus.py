"""Three-phase dispatch: who reacts, in what order, and what stops it."""

from __future__ import annotations

import pytest

from conftest import RunEffect
from here_to_slay.core import (
    CardsChosen,
    EngineInvariantError,
    GameState,
    Outcome,
    PlayerId,
    Quiescent,
    zone_id,
)
from here_to_slay.core.bus import clear_once_per_turn, subscriber_report, subscriptions_for
from here_to_slay.core.events import Event, Phase

DRAW = {"op": "draw", "target": "$self", "count": 1}


def put_in_party(state: GameState, def_id: str, player: str) -> str:
    """Move a fresh copy of a definition into a player's party, ability and all."""
    instance = next(
        card
        for card in state.cards.values()
        if card.def_id == def_id and not str(card.zone).startswith("party")
    )
    state.move_card(instance.id, zone_id("party", PlayerId(player)))
    return instance.id


def hand_card(state: GameState, player: str) -> str:
    return state.zone(zone_id("hand", PlayerId(player))).cards[0]


class TestSubscriptionsAreDerivedFromState:
    def test_a_card_in_the_right_zone_subscribes(self, trigger_state: GameState) -> None:
        card = put_in_party(trigger_state, "triggers.hero.scribe", "p1")
        found = subscriptions_for(trigger_state, Event("card.drawn"), Phase.POST)
        assert [entry.card for entry in found] == [card]

    def test_the_same_card_elsewhere_does_not(self, trigger_state: GameState) -> None:
        """``while_in`` is the whole subscription lifecycle: no bookkeeping to
        leak, because moving the card *is* unsubscribing it."""
        card = put_in_party(trigger_state, "triggers.hero.scribe", "p1")
        assert subscriptions_for(trigger_state, Event("card.drawn"), Phase.POST)
        trigger_state.move_card(card, zone_id("discard"))
        assert not subscriptions_for(trigger_state, Event("card.drawn"), Phase.POST)

    def test_timing_selects_the_phase(self, trigger_state: GameState) -> None:
        put_in_party(trigger_state, "triggers.hero.guardian_angel", "p1")
        assert subscriptions_for(trigger_state, Event("card.discarded"), Phase.PRE)
        assert not subscriptions_for(trigger_state, Event("card.discarded"), Phase.POST)

    def test_report_lists_who_would_react(self, trigger_state: GameState) -> None:
        put_in_party(trigger_state, "triggers.hero.scribe", "p1")
        report = subscriber_report(trigger_state, "card.drawn")
        assert any("triggers.hero.scribe" in line for line in report)


class TestOrdering:
    def test_priority_runs_high_first(self, trigger_state: GameState) -> None:
        late = put_in_party(trigger_state, "triggers.hero.latecomer", "p1")
        early = put_in_party(trigger_state, "triggers.hero.herald", "p1")
        order = [s.card for s in subscriptions_for(trigger_state, Event("card.drawn"), Phase.POST)]
        assert order == [early, late]

    def test_the_active_seat_reacts_before_the_others(self, trigger_state: GameState) -> None:
        theirs = put_in_party(trigger_state, "triggers.hero.scribe", "p2")
        mine = put_in_party(trigger_state, "triggers.hero.scribe", "p1")
        order = [s.card for s in subscriptions_for(trigger_state, Event("card.drawn"), Phase.POST)]
        assert order == [mine, theirs]

    def test_the_order_is_total(self, trigger_state: GameState) -> None:
        """Two identical cards in one party still have to be ordered, or replay
        is a coin flip."""
        for _ in range(2):
            put_in_party(trigger_state, "triggers.hero.scribe", "p1")
        order = [s.card for s in subscriptions_for(trigger_state, Event("card.drawn"), Phase.POST)]
        assert order == sorted(order)
        assert len(order) == 2


class TestPostReactions:
    def test_a_subscriber_reacts_after_the_event(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.scribe", "p1")
        run_effect(trigger_state, DRAW, player="p1")
        assert trigger_state.player(PlayerId("p1")).action_points == 1

    def test_the_condition_gates_it(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.scribe", "p2")
        run_effect(trigger_state, DRAW, player="p1")
        assert trigger_state.player(PlayerId("p2")).action_points == 0

    def test_once_per_turn_fires_once(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.hoarder", "p1")
        run_effect(trigger_state, DRAW, player="p1")
        run_effect(trigger_state, DRAW, player="p1")
        assert trigger_state.player(PlayerId("p1")).action_points == 10

    def test_once_per_turn_resets_with_the_turn(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.hoarder", "p1")
        run_effect(trigger_state, DRAW, player="p1")
        trigger_state.turn_number += 1
        clear_once_per_turn(trigger_state)
        run_effect(trigger_state, DRAW, player="p1")
        assert trigger_state.player(PlayerId("p1")).action_points == 20

    def test_the_per_turn_marker_lives_on_the_card(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        """It must clone and snapshot with the state, not hide in the bus."""
        card = put_in_party(trigger_state, "triggers.hero.hoarder", "p1")
        run_effect(trigger_state, DRAW, player="p1")
        assert "fired_on_turn" in trigger_state.card(card).state
        assert "fired_on_turn" in trigger_state.snapshot()["cards"][card]["state"]


class TestPreCancellation:
    def test_a_pre_subscriber_stops_the_event(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.guardian_angel", "p1")
        card = hand_card(trigger_state, "p1")
        before = len(trigger_state.zone(zone_id("hand", PlayerId("p1"))))

        run = run_effect(
            trigger_state,
            {"op": "discard", "target": "$self", "count": 1},
            player="p1",
            decisions=[CardsChosen((card,))],
        )

        assert trigger_state.card(card).zone == zone_id("hand", PlayerId("p1"))
        assert len(trigger_state.zone(zone_id("hand", PlayerId("p1")))) == before
        assert run.status == Quiescent(Outcome.CANCELLED)

    def test_cancelling_stops_the_rest_of_the_sequence(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        """"``seq``: abort if one is cancelled" — otherwise a countered card
        still pays out the rest of its text."""
        put_in_party(trigger_state, "triggers.hero.guardian_angel", "p1")
        run_effect(
            trigger_state,
            {
                "op": "seq",
                "steps": [
                    {"op": "discard", "target": "$self", "count": 1},
                    {"op": "gain_action_points", "target": "$self", "amount": 5},
                ],
            },
            player="p1",
            decisions=[CardsChosen((hand_card(trigger_state, "p1"),))],
        )
        assert trigger_state.player(PlayerId("p1")).action_points == 0

    def test_an_unrelated_players_discard_still_resolves(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.guardian_angel", "p1")
        before = len(trigger_state.zone(zone_id("hand", PlayerId("p2"))))
        run_effect(
            trigger_state,
            {"op": "discard", "target": "$self", "count": 1},
            player="p2",
            decisions=[CardsChosen((hand_card(trigger_state, "p2"),))],
        )
        assert len(trigger_state.zone(zone_id("hand", PlayerId("p2")))) == before - 1


class TestDepthCap:
    def test_runaway_recursion_raises_instead_of_hanging(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        """A card that reacts to its own event is a mod bug; the engine has to
        name it rather than spin."""
        put_in_party(trigger_state, "triggers.hero.echo", "p1")
        with pytest.raises(EngineInvariantError, match="max_reaction_depth"):
            run_effect(trigger_state, {"op": "emit", "event": "triggers.echoed"}, player="p1")

    def test_the_report_shows_the_stack(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        put_in_party(trigger_state, "triggers.hero.echo", "p1")
        with pytest.raises(EngineInvariantError) as error:
            run_effect(trigger_state, {"op": "emit", "event": "triggers.echoed"}, player="p1")
        assert len(error.value.recent) == trigger_state.rules.max_reaction_depth
