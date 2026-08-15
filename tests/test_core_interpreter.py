"""The generator driver — Phase 3's acceptance test lives here.

A nested effect suspends twice for decisions and resumes correctly, and
replaying the log reproduces the state byte for byte.
"""

from __future__ import annotations

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    Awaiting,
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChoosePlayer,
    Confirmed,
    DecisionLog,
    EngineError,
    GameState,
    IllegalDecisionError,
    Intent,
    IntentChosen,
    Interpreter,
    Outcome,
    PlayerChosen,
    PlayerId,
    Quiescent,
    ScriptedSource,
    diff_snapshots,
    drive,
    new_game,
    replay,
    zone_id,
)
from here_to_slay.core.context import EffectContext

#: seq -> choose -> if -> discard/draw: two suspensions on the way through
NESTED = {
    "op": "seq",
    "steps": [
        {
            "op": "choose",
            "bind": "victim",
            "chooser": "$self",
            "from": {"selector": "opponents"},
            "prompt": "Steal from whom?",
        },
        {
            "op": "if",
            "condition": {"op": "hand_size", "player": "$victim", "cmp": ">", "value": 0},
            "then": {"op": "discard", "target": "$victim", "count": 1, "chooser": "$self"},
            "else": {"op": "draw", "target": "$self", "count": 1},
        },
    ],
}


def run_nested(state: GameState, log: DecisionLog | None = None) -> Interpreter:
    ctx = EffectContext.root(state, player=PlayerId("p1"))
    interpreter = Interpreter(state, log=log)
    interpreter.begin(ctx.run(NESTED))
    return interpreter


class TestSuspendAndResume:
    def test_it_suspends_for_the_first_decision(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        request = interpreter.pending
        assert isinstance(request, ChoosePlayer)
        assert request.requester == "p1"
        assert set(request.candidates) == {"p2", "p3", "p4"}

    def test_it_suspends_a_second_time_inside_the_branch(self, table_state: GameState) -> None:
        """The ``if`` was chosen using a value bound by the *first* suspension —
        which only works because the whole call stack survived it."""
        interpreter = run_nested(table_state)
        status = interpreter.submit(PlayerChosen(PlayerId("p3")))
        assert isinstance(status, Awaiting)
        request = status.request
        assert isinstance(request, ChooseCards)
        assert request.requester == "p1"  # the chooser, not the victim
        assert request.from_zone == zone_id("hand", PlayerId("p3"))

    def test_it_resumes_to_quiescent_and_applies_the_effect(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        interpreter.submit(PlayerChosen(PlayerId("p3")))
        victim_hand = table_state.zone(zone_id("hand", PlayerId("p3")))
        card = victim_hand.cards[0]
        before = len(victim_hand)

        status = interpreter.submit(CardsChosen((card,)))

        assert status == Quiescent(Outcome.DONE)
        assert len(victim_hand) == before - 1
        assert table_state.card(card).zone == "discard"
        assert interpreter.pending is None

    def test_two_decisions_are_logged_in_order(self, table_state: GameState) -> None:
        log = DecisionLog.for_game(table_state)
        interpreter = run_nested(table_state, log)
        interpreter.submit(PlayerChosen(PlayerId("p3")))
        card = table_state.zone(zone_id("hand", PlayerId("p3"))).cards[0]
        interpreter.submit(CardsChosen((card,)))

        assert [entry.request for entry in log.entries] == ["choose_player", "choose_cards"]
        assert log.entries[0].requester == "p1"


class TestReplay:
    def test_replaying_the_log_reproduces_the_state_byte_for_byte(
        self, table_content: ContentRegistry
    ) -> None:
        """``Game = f(content_hash, seed, decisions)``, made assertable."""
        players = ["Ann", "Bob", "Cid", "Dee"]
        original = new_game(table_content, players, seed="replay-me")
        log = DecisionLog.for_game(original, players)
        interpreter = run_nested(original, log)
        interpreter.submit(PlayerChosen(PlayerId("p3")))
        interpreter.submit(
            CardsChosen((original.zone(zone_id("hand", PlayerId("p3"))).cards[0],))
        )

        rerun = new_game(table_content, players, seed="replay-me")
        status = replay(log, rerun, EffectContext.root(rerun, player=PlayerId("p1")).run(NESTED))

        assert status == Quiescent(Outcome.DONE)
        assert diff_snapshots(original.snapshot(), rerun.snapshot()) == []

    def test_a_log_from_a_different_seed_is_refused(
        self, table_content: ContentRegistry
    ) -> None:
        log = DecisionLog.for_game(new_game(table_content, ["Ann", "Bob"], seed="one"))
        other = new_game(table_content, ["Ann", "Bob"], seed="two")
        with pytest.raises(Exception, match="seed"):
            replay(log, other, EffectContext.root(other).run({"op": "noop"}))


class TestValidation:
    """The UI is never trusted: every decision re-validates against its request."""

    def test_the_wrong_kind_of_decision_is_rejected(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        with pytest.raises(IllegalDecisionError, match="expected a PlayerChosen"):
            interpreter.submit(Confirmed(True))

    def test_an_unoffered_player_is_rejected(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        with pytest.raises(IllegalDecisionError, match="not among"):
            interpreter.submit(PlayerChosen(PlayerId("p1")))

    def test_a_card_from_the_wrong_zone_is_rejected(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        interpreter.submit(PlayerChosen(PlayerId("p3")))
        mine = table_state.zone(zone_id("hand", PlayerId("p1"))).cards[0]
        with pytest.raises(IllegalDecisionError, match="not among the candidates"):
            interpreter.submit(CardsChosen((mine,)))

    def test_the_wrong_number_of_cards_is_rejected(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        interpreter.submit(PlayerChosen(PlayerId("p3")))
        hand = table_state.zone(zone_id("hand", PlayerId("p3"))).cards
        with pytest.raises(IllegalDecisionError, match="asks for"):
            interpreter.submit(CardsChosen(tuple(hand[:2])))

    def test_a_rejected_decision_leaves_the_request_pending(self, table_state: GameState) -> None:
        """A bad submit must not consume the question, or the game deadlocks."""
        interpreter = run_nested(table_state)
        with pytest.raises(IllegalDecisionError):
            interpreter.submit(Confirmed(True))
        assert isinstance(interpreter.pending, ChoosePlayer)

    def test_a_rejected_decision_is_not_logged(self, table_state: GameState) -> None:
        log = DecisionLog.for_game(table_state)
        interpreter = run_nested(table_state, log)
        with pytest.raises(IllegalDecisionError):
            interpreter.submit(Confirmed(True))
        assert len(log) == 0

    def test_choose_intent_returns_the_engines_own_copy(self) -> None:
        """The UI's object is untrusted data; only its identity is used."""
        mine = Intent(action="draw", label="Draw a card")
        request = ChooseIntent(requester=PlayerId("p1"), intents=(mine,))
        theirs = Intent(action="draw", label="anything they like")
        assert request.validate(IntentChosen(theirs)) is mine

    def test_an_illegal_intent_is_rejected(self) -> None:
        request = ChooseIntent(requester=PlayerId("p1"), intents=(Intent(action="draw"),))
        with pytest.raises(IllegalDecisionError, match="not a legal intent"):
            request.validate(IntentChosen(Intent(action="win_immediately")))


class TestDriverProtocol:
    def test_submitting_with_nothing_pending_raises(self, table_state: GameState) -> None:
        interpreter = Interpreter(table_state)
        with pytest.raises(EngineError, match="nothing is pending"):
            interpreter.submit(Confirmed(True))

    def test_starting_a_second_flow_while_suspended_raises(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        ctx = EffectContext.root(table_state, player=PlayerId("p1"))
        with pytest.raises(EngineError, match="already in progress"):
            interpreter.begin(ctx.run({"op": "noop"}))

    def test_yielding_something_that_is_not_a_request_raises(
        self, table_state: GameState
    ) -> None:
        def rogue():  # type: ignore[no-untyped-def]
            yield "just tell me the answer"

        interpreter = Interpreter(table_state)
        with pytest.raises(EngineError, match="not a Request"):
            interpreter.begin(rogue())

    def test_abort_discards_a_suspended_flow(self, table_state: GameState) -> None:
        interpreter = run_nested(table_state)
        interpreter.abort()
        assert interpreter.pending is None and not interpreter.running

    def test_drive_runs_a_flow_to_completion(self, table_state: GameState) -> None:
        ctx = EffectContext.root(table_state, player=PlayerId("p1"))
        script = ScriptedSource(
            [
                PlayerChosen(PlayerId("p2")),
                CardsChosen((table_state.zone(zone_id("hand", PlayerId("p2"))).cards[0],)),
            ]
        )
        status = drive(Interpreter(table_state), ctx.run(NESTED), script)
        assert status == Quiescent(Outcome.DONE)
        assert script.exhausted

    def test_a_script_that_runs_out_says_which_request_it_died_on(
        self, table_state: GameState
    ) -> None:
        ctx = EffectContext.root(table_state, player=PlayerId("p1"))
        with pytest.raises(EngineError, match="ran out of decisions"):
            drive(Interpreter(table_state), ctx.run(NESTED), ScriptedSource([]))


class TestForcedChoices:
    """A question with one legal answer is not a question."""

    def test_a_single_candidate_is_not_asked_about(self, small_content: ContentRegistry) -> None:
        state = new_game(small_content, ["Ann", "Bob"], seed="forced")
        ctx = EffectContext.root(state, player=PlayerId("p1"))
        script = ScriptedSource([])
        status = drive(
            Interpreter(state),
            ctx.run(
                {
                    "op": "choose",
                    "bind": "victim",
                    "from": {"selector": "opponents"},
                }
            ),
            script,
        )
        assert status == Quiescent(Outcome.DONE)
        assert script.seen == []

    def test_no_candidates_at_all_cancels_rather_than_asking(
        self, table_state: GameState
    ) -> None:
        ctx = EffectContext.root(table_state, player=PlayerId("p1"))
        status = drive(
            Interpreter(table_state),
            ctx.run(
                {
                    "op": "choose",
                    "bind": "hero",
                    "from": {"selector": "heroes", "of": "$self"},
                }
            ),
            ScriptedSource([]),
        )
        assert status == Quiescent(Outcome.CANCELLED)
