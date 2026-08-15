"""The turn machine: it walks the ``phases`` table and knows nothing else.

``rules.yaml`` says what a turn is::

    phases:
      - {id: turn_start, auto_advance: true, on_enter: [...]}
      - {id: main, loop_while: {...}, allows: [draw, play_hero, ...]}
      - {id: turn_end, auto_advance: true, on_enter: [...]}

and this module walks it. Adding an upkeep phase, a two-action turn or a draft
phase is a YAML edit (``architecture_notes.md §6``); nothing below knows the
name of a single phase.

**One step at a time.** :meth:`TurnMachine.step` performs exactly one atomic
piece of a turn — begin the turn, enter a phase, take one action, leave a
phase — and returns. It is a generator, so a step that has to ask something
suspends the way every other effect does, and between steps the game is
*quiescent*, which is the only point at which a save is legal
(``rules_engine.md §6``). The alternative, one long generator for the whole
game, would never be quiescent and could never be saved.

Two loose ends the table cannot express, handled here and documented:

* ``end_turn`` and ``extra_turn`` leave a flag rather than transitioning
  immediately, because unwinding the generator stack out of the middle of a
  card's effect would skip the rest of that card. The flags are read here, at
  the next safe point.
* **A phase loop with no legal intent ends**, and so does one whose actions keep
  being *refused*. Otherwise a card that says "you may not draw" would leave a
  seat choosing the only thing it can choose, having it cancelled before it cost
  anything, and choosing it again forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from here_to_slay.content.schema import PhaseDef
from here_to_slay.core.actions import legal_intents, perform_action
from here_to_slay.core.bus import clear_once_per_turn
from here_to_slay.core.context import EffectContext
from here_to_slay.core.effects.meta import FLAG_END_TURN, FLAG_EXTRA_TURN
from here_to_slay.core.errors import EngineError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.interpreter import ChooseIntent, Flow
from here_to_slay.core.state import GameState
from here_to_slay.core.victory import check_victory

#: how many actions in a row may be refused before their cost is even paid
#: before the phase gives up on this seat
MAX_REFUSED_ACTIONS = 3


@dataclass(slots=True)
class TurnMachine:
    """A cursor into the phase table, plus the four things a step can be."""

    state: GameState
    #: 0 = play until somebody wins. A cap is what keeps a fuzz run finite.
    max_turns: int = 0
    phase_index: int = 0
    #: has the current turn's ``turn.started`` been announced?
    turn_open: bool = False
    #: has the current phase's ``on_enter`` run?
    phase_open: bool = False
    finished: bool = False
    #: turns actually played, for the cap and for tests
    turns_played: int = 0
    #: actions declared in this phase that were cancelled before paying anything
    refused: int = 0
    trace: list[str] = field(default_factory=list)

    # -- reading the table -------------------------------------------------

    @property
    def phases(self) -> list[PhaseDef]:
        return list(self.state.rules.phases)

    @property
    def phase(self) -> PhaseDef | None:
        phases = self.phases
        if not phases or self.phase_index >= len(phases):
            return None
        return phases[self.phase_index]

    @property
    def done(self) -> bool:
        return self.finished or self.state.winner is not None

    # -- the one entry point -----------------------------------------------

    def step(self, ctx: EffectContext) -> Flow:
        """Perform the next atomic piece of the game. Always makes progress."""
        if self.done:
            self.finished = True
            return Outcome.DONE
        if not self.phases:
            raise EngineError(
                f"rule set '{self.state.rules.id}' declares no phases, so there is no turn to take"
            )

        if not self.turn_open:
            yield from self._begin_turn(ctx)
            return Outcome.DONE

        phase = self.phase
        if phase is None:  # walked off the end of the table
            yield from self._end_turn(ctx)
            return Outcome.DONE

        if not self.phase_open:
            yield from self._enter_phase(ctx, phase)
            return Outcome.DONE

        if self._loop_continues(ctx, phase):
            yield from self._take_action(ctx, phase)
            return Outcome.DONE

        yield from self._leave_phase(ctx, phase)
        return Outcome.DONE

    # -- turns -------------------------------------------------------------

    def _begin_turn(self, ctx: EffectContext) -> Flow:
        state = self.state
        state.turn_number += 1
        self.turns_played += 1
        self.turn_open = True
        self.phase_index = 0
        self.phase_open = False
        state.flags.pop(FLAG_END_TURN, None)
        # Per-turn limits are cleared here rather than at turn end so that a
        # trigger firing during the last phase of a turn still counts as used.
        clear_once_per_turn(state)
        for instance in state.cards.values():
            instance.tapped = False
        self.trace.append(f"turn {state.turn_number} -> {state.active_player}")
        yield from ctx.emit(
            "turn.started",
            {"player": state.active_player, "turn": state.turn_number},
            actor=state.active_player,
        )

    def _end_turn(self, ctx: EffectContext) -> Flow:
        state = self.state
        yield from ctx.emit(
            "turn.ended",
            {"player": state.active_player, "turn": state.turn_number},
            actor=state.active_player,
        )
        state.flags.pop(FLAG_END_TURN, None)
        state.active_player = self._next_seat()
        self.turn_open = False
        self.phase_index = 0
        self.phase_open = False
        if self.max_turns and self.turns_played >= self.max_turns:
            self.finished = True

    def _next_seat(self) -> PlayerId:
        """Whoever plays next — usually the seat to the left."""
        state = self.state
        claimed = state.flags.pop(FLAG_EXTRA_TURN, None)
        if claimed is not None and claimed in state.players:
            return PlayerId(str(claimed))
        return state.next_player()

    # -- phases ------------------------------------------------------------

    def _enter_phase(self, ctx: EffectContext, phase: PhaseDef) -> Flow:
        state = self.state
        state.phase = phase.id
        self.phase_open = True
        self.refused = 0
        yield from ctx.emit(
            "phase.changed", {"phase": phase.id, "player": state.active_player}
        )
        for step in phase.on_enter:
            yield from ctx.run(step)
        yield from check_victory(ctx)

    def _leave_phase(self, ctx: EffectContext, phase: PhaseDef) -> Flow:
        for step in phase.on_exit:
            yield from ctx.run(step)
        self.phase_open = False
        self.phase_index += 1
        yield from check_victory(ctx)
        if self.phase is None:
            yield from self._end_turn(ctx)

    def _loop_continues(self, ctx: EffectContext, phase: PhaseDef) -> bool:
        """Whether this phase asks for another action.

        ``auto_advance`` and "no ``loop_while``" both mean "run ``on_enter``,
        then move on" — a phase only lingers if the table says when to stop.
        """
        if phase.loop_while is None or self.done:
            return False
        if self.state.flags.get(FLAG_END_TURN):
            return False
        return ctx.test(phase.loop_while)

    # -- actions -----------------------------------------------------------

    def _take_action(self, ctx: EffectContext, phase: PhaseDef) -> Flow:
        state = self.state
        seat = state.active_player
        intents = legal_intents(state, seat)
        if not intents:
            # Nothing legal: the phase is over, however much AP is left. A menu
            # with no entries is a deadlock, not a turn.
            state.flags[FLAG_END_TURN] = True
            return Outcome.DONE

        chosen = yield ChooseIntent(
            requester=seat,
            prompt=f"{state.player(seat).name}: {phase.id} ({state.player(seat).action_points} AP)",
            intents=intents,
        )
        self.trace.append(f"  {seat} {chosen.action}")
        yield from perform_action(ctx, chosen, player=seat)

        # An action cancelled before it paid for anything left the game exactly
        # as it was, so choosing it again would ask the same question forever.
        # "You may not draw" is a legitimate card; an endless turn is not.
        if any(event.name == "action.paid" for event in ctx.execution.history):
            self.refused = 0
        else:
            self.refused += 1
            if self.refused >= MAX_REFUSED_ACTIONS:
                self.trace.append(f"  {seat} refused {self.refused}x, ending the phase")
                state.flags[FLAG_END_TURN] = True
                return Outcome.DONE

        # "The Monster row refills" lives in rules.yaml, because *when* a new
        # Monster turns up is policy (Phase 3, decision 9).
        for step in state.rules.turn.after_action:
            yield from ctx.run(step)
        yield from check_victory(ctx)
        return Outcome.DONE

    # -- reporting ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The cursor, for a save file or a test failure message."""
        return {
            "phase_index": self.phase_index,
            "turn_open": self.turn_open,
            "phase_open": self.phase_open,
            "turns_played": self.turns_played,
            "refused": self.refused,
            "finished": self.finished,
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.phase_index = int(data["phase_index"])
        self.turn_open = bool(data["turn_open"])
        self.phase_open = bool(data["phase_open"])
        self.turns_played = int(data["turns_played"])
        self.refused = int(data.get("refused", 0))
        self.finished = bool(data["finished"])


__all__ = ["TurnMachine"]
