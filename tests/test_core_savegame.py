"""Phase 11 — save and load.

The claim under test is the one ``core/savegame.py`` is built on: a save is the
*inputs* to a game, so loading reproduces the game exactly or refuses. Every
test here either proves that equality with ``GameState.snapshot()`` — the same
oracle the determinism tests use — or proves a refusal.

The other half is :attr:`Engine.savepoint`, which had to become true rather than
approximate before any of this was safe. ``pending is None`` was the old test
for "between actions", and it is *also* true in the middle of a step, which is
exactly when a save must not be taken.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    CardsChosen,
    Confirmed,
    DecisionSource,
    Engine,
    IntentChosen,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    Request,
)
from here_to_slay.core.errors import ReplayError
from here_to_slay.core.interpreter import Decision
from here_to_slay.core.savegame import (
    SAVE_SUFFIX,
    SaveError,
    SaveGame,
    SaveSummary,
    autosave_name,
    list_saves,
    save_path,
)


class FirstChoice(DecisionSource):
    """Deterministic, and stops after ``limit`` answers so a game can be caught
    mid-flight."""

    class Stop(Exception):
        pass

    def __init__(self, limit: int = 10_000) -> None:
        self.limit = limit
        self.answered = 0

    def answer(self, request: Request) -> Decision:
        if self.answered >= self.limit:
            raise FirstChoice.Stop
        self.answered += 1
        match request.kind:
            case "choose_intent":
                return IntentChosen(next(iter(request.intents)))  # type: ignore[attr-defined]
            case "choose_cards":
                low = request.minimum  # type: ignore[attr-defined]
                return CardsChosen(tuple(request.candidates[:low]))  # type: ignore[attr-defined]
            case "choose_player":
                return PlayerChosen(request.candidates[0])  # type: ignore[attr-defined]
            case "choose_option":
                return OptionChosen(request.options[0].key)  # type: ignore[attr-defined]
            case "reaction":
                return ReactionChosen(None)
            case "confirm":
                return Confirmed(False)
        raise AssertionError(f"unhandled request kind {request.kind}")


def _played(content: ContentRegistry, *, decisions: int, seed: str = "save") -> Engine:
    """A game stopped mid-flight, waiting on a request."""
    engine = Engine.new(content, ["Ann", "Bob"], seed=seed, max_turns=20)
    source = FirstChoice(limit=decisions)
    with pytest.raises(FirstChoice.Stop):
        engine.run(source)
    return engine


# ---------------------------------------------------------------------------
# Savepoints
# ---------------------------------------------------------------------------


class TestWhenASaveIsLegal:
    """`quiescent` used to be true mid-step, which is the one time it must not be."""

    def test_a_fresh_engine_is_quiescent_and_saveable(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="fresh")
        assert engine.quiescent and engine.savepoint and not engine.running

    def test_an_open_question_is_a_savepoint_but_not_quiescent(
        self, cardless_content: ContentRegistry
    ) -> None:
        """The only point a UI ever gets to save at, so it had better be legal.

        The engine is blocked waiting for an answer: nothing is mutating and the
        log holds every decision made so far.
        """
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="open")
        engine.start()
        assert engine.pending is not None
        assert engine.savepoint
        assert not engine.quiescent

    def test_a_finished_game_is_a_savepoint(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="done", max_turns=1)
        engine.run(FirstChoice())
        assert engine.over and engine.savepoint and not engine.quiescent

    def test_mid_step_is_neither(self, cardless_content: ContentRegistry) -> None:
        """The regression this property exists for.

        Answering *is* the middle of a step from anywhere but the answering
        thread, and the old `pending is None` test said "quiescent, go ahead and
        save" — a save of a position that has already half-changed.
        """
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="midstep")
        seen: list[tuple[bool, bool, bool, bool]] = []

        # Wrapping the interpreter's `begin` is how we get to look at the engine
        # from *inside* `_pump` — which is where the pygame client's other
        # thread can legitimately find it while it reads a view or asks to save.
        original = engine.interpreter.begin

        def watching(flow: Any) -> Any:
            old_formula = engine.pending is None and not engine.over
            seen.append((engine.running, engine.quiescent, engine.savepoint, old_formula))
            return original(flow)

        engine.interpreter.begin = watching  # type: ignore[method-assign]
        with pytest.raises(FirstChoice.Stop):
            engine.run(FirstChoice(limit=3))

        assert seen, "the interpreter never began a step"
        for running, quiescent, savepoint, _old in seen:
            assert running is True
            assert quiescent is False
            assert savepoint is False, "a save mid-step would describe a half-applied action"
        assert any(old for *_rest, old in seen), (
            "the old `pending is None and not over` test must have said 'quiescent' "
            "at least once here, or this is not the regression it claims to be"
        )

    def test_capture_refuses_mid_step(self, cardless_content: ContentRegistry) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="refuse")
        engine.start()
        engine._depth = 1  # what being inside submit() looks like
        with pytest.raises(SaveError, match="between decisions"):
            SaveGame.capture(engine)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


class TestASaveIsTheGame:
    def test_restoring_reproduces_the_state_exactly(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = _played(cardless_content, decisions=9)
        before = engine.state.snapshot()

        restored = SaveGame.capture(engine).restore(cardless_content)

        assert restored.state.snapshot() == before

    def test_it_survives_a_file(self, cardless_content: ContentRegistry, tmp_path: Path) -> None:
        engine = _played(cardless_content, decisions=7)
        before = engine.state.snapshot()

        path = SaveGame.capture(engine, label="halfway").save(tmp_path / "g.hts.json")
        restored = SaveGame.load(path).restore(cardless_content)

        assert restored.state.snapshot() == before

    def test_it_lands_on_the_same_open_question(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = _played(cardless_content, decisions=6)
        pending = engine.pending
        assert pending is not None

        restored = SaveGame.capture(engine).restore(cardless_content)

        assert restored.pending is not None
        assert restored.pending.kind == pending.kind
        assert restored.pending.requester == pending.requester

    def test_play_continues_from_the_same_place(
        self, cardless_content: ContentRegistry
    ) -> None:
        """The point of a save: carry on, and get the game you would have had."""
        engine = _played(cardless_content, decisions=6)
        restored = SaveGame.capture(engine).restore(cardless_content)

        for side in (engine, restored):
            with pytest.raises(FirstChoice.Stop):
                side.run(FirstChoice(limit=8))

        assert restored.state.snapshot() == engine.state.snapshot()

    def test_the_restored_log_keeps_recording(
        self, cardless_content: ContentRegistry
    ) -> None:
        """So the *next* save continues the same history rather than starting one."""
        engine = _played(cardless_content, decisions=6)
        restored = SaveGame.capture(engine).restore(cardless_content)
        assert len(restored.log) == 6

        with pytest.raises(FirstChoice.Stop):
            restored.run(FirstChoice(limit=4))
        assert len(restored.log) == 10

        again = SaveGame.capture(restored).restore(cardless_content)
        assert again.state.snapshot() == restored.state.snapshot()

    def test_a_finished_game_saves_and_restores(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="won", max_turns=2)
        engine.run(FirstChoice())
        game = SaveGame.capture(engine)
        assert game.summary.turn_number >= 1

        restored = game.restore(cardless_content)
        assert restored.over
        assert restored.state.snapshot() == engine.state.snapshot()

    def test_the_turn_cap_travels_with_the_save(
        self, cardless_content: ContentRegistry
    ) -> None:
        """`max_turns` is an input to the game, and Phase 10 put it in the log.

        A save that forgot it would restore a game that keeps playing past the
        turn it was supposed to stop on.
        """
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="capped", max_turns=2)
        engine.run(FirstChoice())
        restored = SaveGame.capture(engine).restore(cardless_content)
        assert restored.machine.max_turns == 2
        assert restored.over

    def test_a_save_taken_before_the_first_decision_still_loads(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = Engine.new(cardless_content, ["Ann", "Bob"], seed="brandnew")
        restored = SaveGame.capture(engine).restore(cardless_content)
        assert restored.pending is not None
        assert len(restored.log) == 0

    def test_capturing_does_not_alias_the_running_log(
        self, cardless_content: ContentRegistry
    ) -> None:
        """The save must describe the position the player chose to keep."""
        engine = _played(cardless_content, decisions=5)
        game = SaveGame.capture(engine)
        with pytest.raises(FirstChoice.Stop):
            engine.run(FirstChoice(limit=3))

        assert len(game.log) == 5
        assert len(engine.log) == 8


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestItRefusesRatherThanLies:
    def test_edited_content_is_refused(
        self, cardless_content: ContentRegistry, table_content: ContentRegistry
    ) -> None:
        engine = _played(cardless_content, decisions=5)
        game = SaveGame.capture(engine)
        with pytest.raises(ReplayError, match="different content"):
            game.restore(table_content)

    def test_a_truncated_file_is_a_save_error_not_a_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.hts.json"
        path.write_text('{"format": 1, "log": ', encoding="utf-8")
        with pytest.raises(SaveError):
            SaveGame.load(path)

    def test_a_json_file_that_is_not_a_save_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "other.hts.json"
        path.write_text('{"hello": "world"}', encoding="utf-8")
        with pytest.raises(SaveError, match="no decision log"):
            SaveGame.load(path)

    def test_a_newer_format_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "future.hts.json"
        path.write_text(json.dumps({"format": 99, "log": {}}), encoding="utf-8")
        with pytest.raises(SaveError, match="newer than this build"):
            SaveGame.load(path)

    def test_a_missing_file_is_a_save_error(self, tmp_path: Path) -> None:
        with pytest.raises(SaveError, match="could not read"):
            SaveGame.load(tmp_path / "nope.hts.json")


# ---------------------------------------------------------------------------
# The directory
# ---------------------------------------------------------------------------


class TestTheSaveFolder:
    def test_it_lists_newest_first_and_skips_rubbish(
        self, cardless_content: ContentRegistry, tmp_path: Path
    ) -> None:
        engine = _played(cardless_content, decisions=4)
        for index, name in enumerate(("older", "newer")):
            path = SaveGame.capture(engine, label=name).save(save_path(tmp_path, name))
            # Explicit mtimes: two saves written in the same millisecond would
            # otherwise make this test's ordering a coin toss.
            import os

            os.utime(path, (1_700_000_000 + index * 60,) * 2)
        (tmp_path / f"corrupt{SAVE_SUFFIX}").write_text("{{{", encoding="utf-8")
        (tmp_path / "notasave.txt").write_text("hello", encoding="utf-8")

        found = list_saves(tmp_path)

        assert [game.label for game in found] == ["newer", "older"]

    def test_an_absent_directory_lists_nothing(self, tmp_path: Path) -> None:
        assert list_saves(tmp_path / "never_created") == ()

    def test_save_path_applies_the_suffix_exactly_once(self, tmp_path: Path) -> None:
        assert save_path(tmp_path, "game").name == f"game{SAVE_SUFFIX}"
        assert save_path(tmp_path, f"game{SAVE_SUFFIX}").name == f"game{SAVE_SUFFIX}"

    def test_save_path_strips_characters_a_filesystem_refuses(self, tmp_path: Path) -> None:
        assert "?" not in save_path(tmp_path, "who? me:").name

    def test_autosave_names_are_sortable_and_say_who(self) -> None:
        name = autosave_name(("Ann", "Bob"), 7)
        assert name.endswith("_AB_t7")


# ---------------------------------------------------------------------------
# The summary a load screen reads
# ---------------------------------------------------------------------------


class TestTheSummary:
    def test_it_describes_the_game_without_replaying_it(
        self, cardless_content: ContentRegistry
    ) -> None:
        engine = _played(cardless_content, decisions=8)
        game = SaveGame.capture(engine)

        assert game.summary.players == ("Ann", "Bob")
        assert game.summary.decisions == 8
        assert game.summary.turn_number == engine.state.turn_number
        assert game.summary.pending == engine.pending.kind  # type: ignore[union-attr]
        assert not game.summary.finished

    def test_it_survives_the_file(
        self, cardless_content: ContentRegistry, tmp_path: Path
    ) -> None:
        engine = _played(cardless_content, decisions=8)
        path = SaveGame.capture(engine).save(tmp_path / "s.hts.json")
        assert SaveGame.load(path).summary == SaveGame.capture(engine).summary

    def test_a_missing_summary_degrades_to_an_empty_one(self, tmp_path: Path) -> None:
        """Hand-written and machine-written files both have to load."""
        path = tmp_path / "bare.hts.json"
        path.write_text(
            json.dumps({"format": 1, "log": {"format": 1, "entries": []}}), encoding="utf-8"
        )
        assert SaveGame.load(path).summary == SaveSummary()

    def test_the_description_is_ascii(self, cardless_content: ContentRegistry) -> None:
        """`core/` must not emit a glyph a legacy Windows console cannot encode
        — the Phase 5 lesson, applied to the newest thing that prints."""
        engine = _played(cardless_content, decisions=3)
        text = str(SaveGame.capture(engine, label="x"))
        text.encode("cp1252")  # raises if a bullet or dash sneaked in
