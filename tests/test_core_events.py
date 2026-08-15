"""Events are immutable records; frames carry the mutable resolution state."""

from __future__ import annotations

import pytest

from here_to_slay.core.events import (
    Event,
    EventFrame,
    EventResult,
    Outcome,
    Phase,
    Verdict,
    VerdictKind,
)
from here_to_slay.core.ids import CardId, PlayerId


class TestEvent:
    def test_payload_is_read_only(self) -> None:
        event = Event("card.drawn", {"player": "p1"})
        with pytest.raises(TypeError):
            event.payload["player"] = "p2"  # type: ignore[index]

    def test_well_known_accessors_fall_back_to_actor_and_source(self) -> None:
        event = Event("card.played", actor=PlayerId("p1"), source=CardId("x#1"))
        assert event.player == "p1"
        assert event.card == "x#1"

    def test_payload_wins_over_the_fallback(self) -> None:
        event = Event("card.drawn", {"player": "p2"}, actor=PlayerId("p1"))
        assert event.player == "p2"

    def test_noun_and_verb(self) -> None:
        event = Event("monster.slain")
        assert (event.noun, event.verb) == ("monster", "slain")

    def test_replace_copies_rather_than_mutates(self) -> None:
        """A modified event must not rewrite history: the original still says
        what was announced."""
        original = Event("card.drawn", {"player": "p1", "card": "a#1"})
        modified = original.replace(player="p2")
        assert original.payload["player"] == "p1"
        assert modified.payload["player"] == "p2"
        assert modified.payload["card"] == "a#1"

    def test_replace_can_change_the_envelope(self) -> None:
        event = Event("card.drawn").replace(name="card.stolen", actor=PlayerId("p3"))
        assert (event.name, event.actor) == ("card.stolen", "p3")


class TestVerdict:
    def test_continue_is_neither_modified_nor_cancelled(self) -> None:
        verdict = Verdict.proceed()
        assert verdict.kind is VerdictKind.CONTINUE
        assert not verdict.is_cancelled and not verdict.is_modified

    def test_cancelled_carries_a_reason(self) -> None:
        verdict = Verdict.cancelled("challenged")
        assert verdict.is_cancelled and verdict.reason == "challenged"

    def test_modified_carries_the_new_event(self) -> None:
        verdict = Verdict.modified(Event("card.drawn"))
        assert verdict.is_modified and verdict.event is not None


class TestFrame:
    def test_cancelling_records_the_reason_and_the_discard_flag(self) -> None:
        frame = EventFrame(event=Event("card.played"))
        frame.cancel("challenged", and_discard=True)
        assert frame.cancelled and frame.reason == "challenged" and frame.discard_source

    def test_a_second_cancel_does_not_clear_and_discard(self) -> None:
        frame = EventFrame(event=Event("card.played"))
        frame.cancel("first", and_discard=True)
        frame.cancel("second")
        assert frame.discard_source

    def test_modify_swaps_the_event_in_flight(self) -> None:
        frame = EventFrame(event=Event("card.drawn", {"player": "p1"}))
        frame.modify(player="p2")
        assert frame.event.payload["player"] == "p2"

    def test_str_shows_depth_and_cancellation(self) -> None:
        frame = EventFrame(event=Event("card.played"), depth=2)
        frame.cancel()
        assert "CANCELLED" in str(frame)


class TestResults:
    def test_event_result_is_truthy_when_it_happened(self) -> None:
        assert EventResult(Event("x.y"))
        assert not EventResult(Event("x.y"), cancelled=True)

    def test_outcome_reports_cancellation(self) -> None:
        assert Outcome.CANCELLED.is_cancelled
        assert not Outcome.DONE.is_cancelled

    def test_phases_match_the_trigger_timings_content_may_declare(self) -> None:
        assert {Phase.PRE.value, Phase.RESOLVE_AFTER.value, Phase.POST.value} == {
            "pre",
            "resolve_after",
            "post",
        }
