"""Phase 4's acceptance test lives here.

    4 players, ``draw`` only, 20 turns, no invariant violations; and forcing a
    victory condition ends the game at the right moment.

Plus the property everything downstream leans on: a game driven through the
``Engine`` replays from its decision log to a byte-identical state.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import Place, empty_hands
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    Awaiting,
    CardsChosen,
    Confirmed,
    Decision,
    DecisionSource,
    Engine,
    EngineError,
    GameOver,
    Intent,
    IntentChosen,
    OptionChosen,
    PlayerChosen,
    PlayerId,
    ReactionChosen,
    Request,
    diff_snapshots,
    find_violations,
    new_game,
    zone_id,
)


class FirstChoice(DecisionSource):
    """Always takes the first legal answer. The dullest possible player, and the
    ancestor of Phase 8's random agent."""

    def __init__(self, prefer: str | None = None) -> None:
        self.prefer = prefer
        self.asked: list[Request] = []

    def answer(self, request: Request) -> Decision:
        self.asked.append(request)
        match request.kind:
            case "choose_intent":
                intents = list(request.intents)  # type: ignore[attr-defined]
                if self.prefer:
                    preferred = [i for i in intents if i.action == self.prefer]
                    intents = preferred or intents
                return IntentChosen(intents[0])
            case "choose_cards":
                minimum = request.minimum  # type: ignore[attr-defined]
                return CardsChosen(tuple(request.candidates[:minimum]))  # type: ignore[attr-defined]
            case "choose_player":
                return PlayerChosen(request.candidates[0])  # type: ignore[attr-defined]
            case "choose_option":
                return OptionChosen(request.options[0].key)  # type: ignore[attr-defined]
            case "reaction":
                return ReactionChosen(None)
            case "confirm":
                return Confirmed(False)
        raise AssertionError(f"unhandled request kind {request.kind}")

    @property
    def intents_seen(self) -> int:
        return sum(1 for request in self.asked if request.kind == "choose_intent")


class TestTheAcceptanceTest:
    def test_four_players_draw_for_twenty_turns_without_breaking_anything(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = Engine.new(
            cardless_content, ["Ann", "Bob", "Cid", "Dee"], seed="acceptance", max_turns=20
        )
        source = FirstChoice()

        status = engine.run(source)

        assert isinstance(status, GameOver)  # the turn cap, not a winner
        assert engine.winner is None
        assert engine.machine.turns_played == 20
        assert find_violations(engine.state) == []
        # 20 turns x 3 action points, and every one of them was a real decision
        assert source.intents_seen == 60

    def test_the_seats_take_turns_in_order(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(
            cardless_content, ["Ann", "Bob", "Cid", "Dee"], seed="order", max_turns=8
        )
        seen: list[PlayerId] = []

        class Watcher(FirstChoice):
            def answer(self, request: Request) -> Decision:
                if request.kind == "choose_intent":
                    seen.append(request.requester)
                return super().answer(request)

        engine.run(Watcher())

        assert [seat for seat, _ in zip(seen[::3], range(8), strict=False)] == [
            "p1",
            "p2",
            "p3",
            "p4",
        ] * 2

    def test_a_forced_victory_ends_the_game_at_the_right_moment(
        self, play_content: ContentRegistry
    ) -> None:
        """Two Monsters already slain, a third in the row that always dies: the
        game must end on the attack that slays it, not at end of turn."""
        engine = Engine.new(play_content, ["Ann", "Bob"], seed="victory", max_turns=6)
        state = engine.state
        empty_hands(state)
        for card in list(state.zone(zone_id("monster_row")).cards):
            state.move_card(card, zone_id("monster_deck"))
        pushovers = [
            instance.id
            for instance in state.cards.values()
            if instance.def_id == "play.monster.pushover"
        ]
        for card in pushovers[:2]:
            state.move_card(card, zone_id("slain", PlayerId("p1")))
        state.move_card(pushovers[2], zone_id("monster_row"))

        status = engine.run(FirstChoice(prefer="attack_monster"))

        assert isinstance(status, GameOver) and status.winner == "p1"
        assert engine.state.winner == "p1"
        assert len(engine.state.zone(zone_id("slain", PlayerId("p1")))) == 3
        assert engine.state.turn_number == 1  # it ended mid-turn, not at turn end


class TestReplay:
    def test_a_game_replays_from_its_log_to_the_same_state(
        self, cardless_content: ContentRegistry
    ) -> None:
        """``Game = f(content_hash, seed, decisions)`` — now for a whole game."""
        players = ["Ann", "Bob", "Cid"]
        engine = Engine.new(cardless_content, players, seed="replay", max_turns=6)
        engine.run(FirstChoice())

        rerun, source = Engine.replaying(cardless_content, engine.log, max_turns=6)
        rerun.run(source)

        assert diff_snapshots(engine.state.snapshot(), rerun.state.snapshot()) == []
        assert source.exhausted

    def test_a_log_recorded_against_other_content_is_refused(
        self, cardless_content: ContentRegistry, play_content: ContentRegistry
    ) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="mismatch", max_turns=2)
        engine.run(FirstChoice())

        with pytest.raises(Exception, match="different content"):
            Engine.replaying(play_content, engine.log)

    def test_every_decision_is_in_the_log(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="log", max_turns=4)
        source = FirstChoice()
        engine.run(source)
        assert len(engine.log) == len(source.asked)


class TestTheDoor:
    """Four methods, and nothing else gets in."""

    def test_it_reports_what_it_is_waiting_for(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="door")
        status = engine.start()

        assert isinstance(status, Awaiting)
        assert engine.pending is status.request
        assert not engine.quiescent

    def test_legal_intents_match_the_open_menu_exactly(
        self, cardless_content: ContentRegistry
    ) -> None:
        """The engine must never offer the UI a set it will not then validate."""
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="door")
        status = engine.start()
        assert isinstance(status, Awaiting)

        assert engine.legal_intents(PlayerId("p1")) == status.request.intents  # type: ignore[attr-defined]
        assert engine.legal_intents(PlayerId("p2")) == ()

    def test_an_illegal_decision_is_refused_and_the_question_stays_open(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="door")
        engine.start()

        with pytest.raises(Exception, match="not a legal intent"):
            engine.submit(IntentChosen(Intent(action="win_immediately")))

        assert engine.pending is not None

    def test_a_view_is_redacted_per_seat(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="door")
        engine.start()

        view = engine.view(PlayerId("p2"))

        assert view.seat == "p2" and not view.is_your_turn
        assert view.zone("main_deck") is not None
        assert view.zone("main_deck").revealed is False  # type: ignore[union-attr]
        assert view.zone("hand", PlayerId("p1")).revealed is False  # type: ignore[union-attr]

    def test_starting_twice_is_refused(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="door")
        engine.start()
        with pytest.raises(EngineError, match="already started"):
            engine.start()

    def test_submitting_before_starting_is_refused(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="door")
        with pytest.raises(EngineError, match="call start"):
            engine.submit(Confirmed(True))


class TestPathologicalRuleSets:
    def test_a_phase_that_never_ends_and_allows_nothing_still_terminates(
        self, deadlock_content: ContentRegistry
    ) -> None:
        """The engine's job is to notice, not to hang."""
        engine = Engine.new(deadlock_content, ["Ann", "Bob"], seed="stuck", max_turns=3)

        status = engine.run(FirstChoice())

        assert isinstance(status, GameOver)
        assert engine.machine.turns_played == 3


class TestPlayingRealCards:
    def test_a_hero_played_through_the_engine_reaches_the_party(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        engine = Engine.new(play_content, ["Ann", "Bob"], seed="hero", max_turns=1)
        empty_hands(engine.state)
        hero = place(engine.state, "play.hero.lump", "hand", "p1")

        engine.run(FirstChoice(prefer="play_hero"))

        assert engine.state.card(hero).zone == zone_id("party", PlayerId("p1"))

    def test_the_monster_row_is_topped_up_after_an_attack(
        self, play_content: ContentRegistry
    ) -> None:
        engine = Engine.new(play_content, ["Ann", "Bob"], seed="rowfill", max_turns=2)
        empty_hands(engine.state)

        engine.run(FirstChoice(prefer="attack_monster"))

        row = engine.state.zone(zone_id("monster_row"))
        assert len(row) == engine.state.rules.setup.monster_row_size

    def test_a_whole_game_of_real_cards_breaks_no_invariant(
        self, play_content: ContentRegistry
    ) -> None:
        """Reaction windows, rolls, challenges and equipment, all at once."""
        engine = Engine.new(
            play_content, ["Ann", "Bob", "Cid"], seed="everything", max_turns=12
        )

        class Eager(FirstChoice):
            """Does anything rather than draw, and plays every reaction offered,
            so the interrupt paths actually run."""

            def answer(self, request: Request) -> Decision:
                if request.kind == "reaction":
                    self.asked.append(request)
                    return ReactionChosen(request.options[0].card)  # type: ignore[attr-defined]
                if request.kind == "choose_intent":
                    self.asked.append(request)
                    intents = list(request.intents)  # type: ignore[attr-defined]
                    busy = [intent for intent in intents if intent.action != "draw"]
                    return IntentChosen((busy or intents)[0])
                return super().answer(request)

        source: Any = Eager()
        engine.run(source)

        assert find_violations(engine.state) == []
        assert any(request.kind == "reaction" for request in source.asked)


def test_a_game_is_quiescent_between_actions(cardless_content: ContentRegistry) -> None:
    """Save/load is only legal here, so "here" has to be observable."""
    engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="quiet", max_turns=1)
    engine.run(FirstChoice())
    assert engine.over and not engine.quiescent

    fresh = Engine.new(cardless_content, ["Ann", "Bob"], seed="quiet", max_turns=1)
    assert fresh.quiescent  # before start(), nothing is pending
    assert new_game(cardless_content, ["Ann", "Bob"], seed="quiet").turn_number == 0
