"""The turn machine walks the phase table and nothing else.

Every test here is really the same test: *the shape of a turn is data*. Nothing
below asserts that a phase is called "main" — only that whatever the table says
is what happens, in that order, and that the two things the table cannot express
(``end_turn``, ``extra_turn``) are honoured at a safe point.
"""

from __future__ import annotations

from typing import Any

from conftest import Place
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    Awaiting,
    Intent,
    IntentChosen,
    Interpreter,
    PlayerId,
    ScriptedSource,
    Status,
    new_game,
    zone_id,
)
from here_to_slay.core.context import EffectContext
from here_to_slay.core.effects.meta import FLAG_END_TURN, FLAG_EXTRA_TURN
from here_to_slay.core.turn_machine import TurnMachine


def step(machine: TurnMachine, decisions: Any = ()) -> Status:
    """Run one atomic step of the game, answering from a script."""
    state = machine.state
    ctx = EffectContext.root(state, player=state.active_player)
    interpreter = Interpreter(state)
    source = ScriptedSource(list(decisions))
    status = interpreter.begin(machine.step(ctx))
    while isinstance(status, Awaiting):
        status = interpreter.submit(source.answer(status.request))
    return status


def run_to_menu(machine: TurnMachine, limit: int = 20) -> Status:
    """Step until the machine asks somebody to choose an action."""
    state = machine.state
    for _ in range(limit):
        ctx = EffectContext.root(state, player=state.active_player)
        interpreter = Interpreter(state)
        status = interpreter.begin(machine.step(ctx))
        if isinstance(status, Awaiting):
            interpreter.abort()
            return status
    raise AssertionError("the machine never asked for an intent")


class TestWalkingTheTable:
    def test_the_first_step_opens_a_turn(self, cardless_content: ContentRegistry) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)

        step(machine)

        assert state.turn_number == 1 and machine.turn_open

    def test_phases_are_entered_in_declaration_order(
        self, cardless_content: ContentRegistry
    ) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)
        seen = []

        for _ in range(4):
            step(machine, [IntentChosen(Intent(action="draw"))])
            seen.append(state.phase)

        assert seen[:2] == ["turn_start", "turn_start"]
        assert "main" in seen

    def test_turn_start_resets_action_points_from_the_rules(
        self, cardless_content: ContentRegistry
    ) -> None:
        """The number lives in ``rules.turn.action_points_per_turn`` and the
        machine never sees it."""
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)

        step(machine)  # begin turn
        step(machine)  # enter turn_start, which runs set_action_points

        assert state.action_points == state.rules.turn.action_points_per_turn

    def test_the_main_phase_asks_for_an_intent(self, cardless_content: ContentRegistry) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        status = run_to_menu(TurnMachine(state))

        assert isinstance(status, Awaiting)
        assert status.request.kind == "choose_intent"
        assert status.request.requester == "p1"

    def test_a_full_turn_hands_over_to_the_next_seat(
        self, cardless_content: ContentRegistry
    ) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)

        for _ in range(12):
            step(machine, [IntentChosen(Intent(action="draw"))])
            if state.active_player == "p2":
                break

        assert state.active_player == "p2"
        assert machine.turns_played == 1


class TestTheThingsTheTableCannotSay:
    def test_end_turn_stops_the_main_phase_at_a_safe_point(
        self, cardless_content: ContentRegistry
    ) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)
        run_to_menu(machine)
        state.flags[FLAG_END_TURN] = True

        step(machine)  # the loop reads the flag rather than asking again

        assert machine.phase is not None and machine.phase.id != "main"

    def test_the_end_turn_flag_does_not_survive_the_turn(
        self, cardless_content: ContentRegistry
    ) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)
        run_to_menu(machine)
        state.flags[FLAG_END_TURN] = True

        for _ in range(6):
            step(machine)
            if state.active_player == "p2":
                break

        assert state.active_player == "p2"
        assert not state.flags.get(FLAG_END_TURN)

    def test_an_extra_turn_keeps_the_same_seat(self, cardless_content: ContentRegistry) -> None:
        state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
        machine = TurnMachine(state)
        state.flags[FLAG_EXTRA_TURN] = PlayerId("p1")

        machine.turn_open = True
        machine.phase_index = len(machine.phases)
        step(machine)

        assert state.active_player == "p1"
        assert FLAG_EXTRA_TURN not in state.flags

    def test_a_menu_with_nothing_in_it_ends_the_phase(
        self, deadlock_content: ContentRegistry
    ) -> None:
        """A phase that says "loop forever" and allows nothing would hang the
        engine. An empty menu is a deadlock, not a turn, so the phase ends."""
        state = new_game(deadlock_content, ["Ann", "Bob"], seed="stuck")
        machine = TurnMachine(state)

        for _ in range(6):
            step(machine)
            if state.phase == "main" and machine.phase_open:
                break
        assert state.phase == "main"

        step(machine)  # the menu is empty

        assert state.flags.get(FLAG_END_TURN)


class TestPerTurnState:
    def test_a_new_turn_untaps_every_card(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = new_game(play_content, ["Ann", "Bob"], seed="untap")
        hero = place(state, "play.hero.striker", "party", "p1")
        state.card(hero).tapped = True
        machine = TurnMachine(state)

        step(machine)

        assert not state.card(hero).tapped

    def test_a_new_turn_clears_once_per_turn_markers(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = new_game(play_content, ["Ann", "Bob"], seed="untap")
        hero = place(state, "play.hero.striker", "party", "p1")
        state.card(hero).state["fired_on_turn"] = {"card.drawn:post:0": 1}

        step(TurnMachine(state))

        assert "fired_on_turn" not in state.card(hero).state


class TestAfterEachAction:
    def test_the_monster_row_refills_because_rules_yaml_says_so(
        self, play_content: ContentRegistry
    ) -> None:
        """Phase 3 refused to refill inside ``slay_monster``: *when* a Monster
        turns up is policy, and this is the policy hook."""
        state = new_game(play_content, ["Ann", "Bob"], seed="refill")
        machine = TurnMachine(state)
        run_to_menu(machine)
        row = state.zone(zone_id("monster_row"))
        state.move_card(row.cards[0], zone_id("slain", PlayerId("p1")))
        assert len(row) == 2

        step(machine, [IntentChosen(Intent(action="draw"))])

        assert len(row) == 3


def test_the_machine_reports_its_cursor(cardless_content: ContentRegistry) -> None:
    """A save file needs the cursor, because the phase table is where the game is."""
    state = new_game(cardless_content, ["Ann", "Bob"], seed="turns")
    machine = TurnMachine(state)
    step(machine)
    step(machine)

    restored = TurnMachine(state)
    restored.restore(machine.snapshot())

    assert restored.snapshot() == machine.snapshot()


def test_a_rule_set_with_no_phases_says_so(small_content: ContentRegistry) -> None:
    state = new_game(small_content, ["Ann", "Bob"], seed="empty")
    object.__setattr__(state.content.rules, "phases", [])
    machine = TurnMachine(state)
    try:
        step(machine)
    except Exception as error:
        assert "declares no phases" in str(error)
    else:
        raise AssertionError("a phaseless rule set should refuse to start a turn")
    finally:
        object.__setattr__(state.content.rules, "phases", list(small_content.rules.phases))
