"""The decision log: recording, round-tripping, and refusing a bad replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    CardsChosen,
    ChooseCards,
    ChoosePlayer,
    Confirmed,
    DecisionLog,
    GameState,
    Intent,
    IntentChosen,
    Interpreter,
    LoggedDecision,
    LogSource,
    OptionChosen,
    PlayerChosen,
    PlayerId,
    ReactionChosen,
    ReplayError,
    diff_snapshots,
    new_game,
    replay,
    zone_id,
)
from here_to_slay.core.context import EffectContext

P1, P3 = PlayerId("p1"), PlayerId("p3")

DRAW_TWICE = {
    "op": "seq",
    "steps": [
        {"op": "choose", "bind": "victim", "from": {"selector": "opponents"}},
        {"op": "draw", "target": "$victim", "count": 2},
    ],
}


class TestRecording:
    def test_a_log_identifies_the_game_it_belongs_to(self, table_state: GameState) -> None:
        log = DecisionLog.for_game(table_state, ["Ann", "Bob", "Cid", "Dee"])
        assert log.content_hash == table_state.content_hash
        assert log.seed == table_state.rng.seed
        assert log.players == ("Ann", "Bob", "Cid", "Dee")

    def test_recording_keeps_the_request_as_well_as_the_answer(
        self, table_state: GameState
    ) -> None:
        """Answers alone would replay right up until the content changed, then
        silently apply "option 2" to a different menu."""
        log = DecisionLog.for_game(table_state)
        request = ChoosePlayer(requester=P1, prompt="Who?", candidates=(P3,))
        entry = log.record(request, PlayerChosen(P3))
        assert entry.request == "choose_player"
        assert entry.requester == P1
        assert entry.prompt == "Who?"
        assert entry.decision() == PlayerChosen(P3)

    def test_truncating_gives_an_undo_point(self, table_state: GameState) -> None:
        log = DecisionLog.for_game(table_state)
        for _ in range(3):
            log.record(ChoosePlayer(requester=P1, candidates=(P3,)), PlayerChosen(P3))
        shorter = log.truncated(2)
        assert len(shorter) == 2 and len(log) == 3
        assert shorter.content_hash == log.content_hash


class TestSerialisation:
    @pytest.mark.parametrize(
        "decision",
        [
            CardsChosen(("a#1", "b#2")),
            PlayerChosen(P3),
            OptionChosen("second"),
            Confirmed(True),
            ReactionChosen("challenge#1"),
            ReactionChosen(None),
            IntentChosen(Intent(action="play_hero", card="x#1", params={"n": 2})),
        ],
    )
    def test_every_decision_kind_round_trips(self, decision) -> None:  # type: ignore[no-untyped-def]
        entry = LoggedDecision(
            index=0,
            request="whatever",
            requester=P1,
            kind=decision.kind,
            data=decision.as_data(),
        )
        assert LoggedDecision.from_data(entry.as_data()).decision() == decision

    def test_a_log_round_trips_through_json(self, table_state: GameState) -> None:
        log = DecisionLog.for_game(table_state)
        log.record(ChoosePlayer(requester=P1, candidates=(P3,)), PlayerChosen(P3))
        restored = DecisionLog.from_json(log.to_json())
        assert restored.as_data() == log.as_data()

    def test_a_log_round_trips_through_a_file(
        self, table_state: GameState, tmp_path: Path
    ) -> None:
        log = DecisionLog.for_game(table_state)
        log.record(ChoosePlayer(requester=P1, candidates=(P3,)), PlayerChosen(P3))
        assert DecisionLog.load(log.save(tmp_path / "game.json")).as_data() == log.as_data()

    def test_a_future_log_format_is_refused(self) -> None:
        with pytest.raises(ReplayError, match="newer than this engine"):
            DecisionLog.from_data({"format": 99, "entries": []})


class TestReplay:
    def game(self, content: ContentRegistry, seed: str = "log") -> GameState:
        return new_game(content, ["Ann", "Bob", "Cid", "Dee"], seed=seed)

    def play(self, state: GameState) -> DecisionLog:
        log = DecisionLog.for_game(state)
        interpreter = Interpreter(state, log=log)
        ctx = EffectContext.root(state, player=P1)
        interpreter.begin(ctx.run(DRAW_TWICE))
        interpreter.submit(PlayerChosen(P3))
        return log

    def test_a_replay_reproduces_the_state_exactly(
        self, table_content: ContentRegistry
    ) -> None:
        original = self.game(table_content)
        log = self.play(original)

        rerun = self.game(table_content)
        replay(log, rerun, EffectContext.root(rerun, player=P1).run(DRAW_TWICE))

        assert diff_snapshots(original.snapshot(), rerun.snapshot()) == []
        assert len(rerun.zone(zone_id("hand", P3))) == 7

    def test_a_log_recorded_against_other_content_is_refused(
        self, table_content: ContentRegistry, small_content: ContentRegistry
    ) -> None:
        """A replay that quietly runs against different cards produces a
        plausible, wrong game — worse than no replay at all."""
        log = DecisionLog.for_game(self.game(table_content))
        other = new_game(small_content, ["Ann", "Bob"], seed="log")
        with pytest.raises(ReplayError, match="different content"):
            replay(log, other, EffectContext.root(other).run({"op": "noop"}))

    def test_a_log_that_runs_out_of_decisions_says_so(
        self, table_content: ContentRegistry
    ) -> None:
        state = self.game(table_content)
        log = DecisionLog.for_game(state)
        with pytest.raises(ReplayError, match="asked for another"):
            replay(log, state, EffectContext.root(state, player=P1).run(DRAW_TWICE))

    def test_a_decision_recorded_for_a_different_request_is_refused(
        self, table_content: ContentRegistry
    ) -> None:
        state = self.game(table_content)
        log = DecisionLog.for_game(state)
        log.record(
            ChooseCards(requester=P1, candidates=("x#1",)), CardsChosen(("x#1",))
        )
        with pytest.raises(ReplayError, match="the content or the engine has changed"):
            replay(log, state, EffectContext.root(state, player=P1).run(DRAW_TWICE))

    def test_a_decision_recorded_for_a_different_seat_is_refused(
        self, table_content: ContentRegistry
    ) -> None:
        state = self.game(table_content)
        log = DecisionLog.for_game(state)
        log.record(ChoosePlayer(requester=PlayerId("p2"), candidates=(P3,)), PlayerChosen(P3))
        with pytest.raises(ReplayError, match="but replay is asking"):
            replay(log, state, EffectContext.root(state, player=P1).run(DRAW_TWICE))

    def test_verification_can_be_skipped_deliberately(
        self, table_content: ContentRegistry
    ) -> None:
        """A test or a tool may want to run a log against a rebuilt game whose
        hash it already knows differs; it has to say so explicitly."""
        state = self.game(table_content)
        log = DecisionLog(content_hash="not-this-one", seed=state.rng.seed)
        source = LogSource(log)
        assert source.exhausted
        replay(log, state, EffectContext.root(state).run({"op": "noop"}), verify=False)
