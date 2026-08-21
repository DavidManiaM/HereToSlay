"""``Engine`` — the entire surface the outside world talks to.

::

    class Engine:
        def start(self) -> Status
        def submit(self, decision: Decision) -> Status
        def view(self, seat: PlayerId) -> GameView
        def legal_intents(self, seat: PlayerId) -> tuple[Intent, ...]

    Status = Awaiting(request) | Quiescent | GameOver(winner)

Four methods, and the CLI, pygame, the AI and the test harness differ only in
what they do between them. Everything else in ``core/`` is reachable only
through this door, which is what keeps ``ui/`` unable to mutate the game
(``architecture_notes.md §1``).

The loop this hides is :meth:`Engine._pump`: run turn-machine steps until one of
them asks a question. A step that needs nobody — resetting action points,
refilling the Monster row, ending a turn — runs and the next one starts, so the
outside world only ever sees the points where a human has something to decide.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from here_to_slay.content.registry import ContentRegistry
from here_to_slay.core.actions import legal_intents
from here_to_slay.core.context import EffectContext
from here_to_slay.core.errors import EngineError
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.interpreter import (
    Awaiting,
    ChooseIntent,
    Decision,
    DecisionSource,
    GameOver,
    Intent,
    Interpreter,
    Quiescent,
    Request,
    Status,
)
from here_to_slay.core.log import DecisionLog, LogSource, check_content
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import GameState
from here_to_slay.core.turn_machine import TurnMachine
from here_to_slay.core.view import GameView, build_view

#: a step that neither asks nor finishes the game this many times in a row means
#: a rule set that cannot make progress — better to say so than to hang
MAX_QUIET_STEPS = 10_000

#: how many rolls :attr:`Engine.recent_rolls` keeps. A UI shows the last few; a
#: whole game's dice belong in the decision log, not in memory.
MAX_ROLL_HISTORY = 64


class Engine:
    """One game, driven by decisions."""

    def __init__(
        self,
        state: GameState,
        *,
        log: DecisionLog | None = None,
        max_turns: int = 0,
    ) -> None:
        self.state = state
        self.log = log if log is not None else DecisionLog.for_game(state)
        self.interpreter = Interpreter(state, log=self.log)
        self.machine = TurnMachine(state, max_turns=max_turns)
        self._started = False
        # Rolls live on the `Execution` of whichever step is running, and an
        # `Execution` dies with its step. The engine holds the current context
        # so it can harvest them before that happens — which is what keeps the
        # UI off the event bus (Phase 5 gap 1).
        self._ctx: EffectContext | None = None
        self._rolls: list[Any] = []
        self._harvested = 0

    # -- construction ------------------------------------------------------

    @classmethod
    def new(
        cls,
        content: ContentRegistry,
        players: Sequence[str],
        *,
        seed: int | str = 0,
        max_turns: int = 0,
    ) -> Engine:
        """Deal a game and wrap it. The one-liner every caller wants."""
        state = new_game(content, players, seed=seed)
        return cls(state, log=DecisionLog.for_game(state, players), max_turns=max_turns)

    @classmethod
    def replaying(
        cls, content: ContentRegistry, log: DecisionLog, *, max_turns: int = 0
    ) -> tuple[Engine, LogSource]:
        """Rebuild the game a log describes, and the source that answers it.

        Refuses a log recorded against different content or a different seed —
        a replay that quietly runs against edited cards produces a plausible,
        wrong game and a bug report nobody can reproduce.
        """
        state = new_game(content, log.players, seed=log.seed)
        check_content(log, state)
        engine = cls(state, log=DecisionLog.for_game(state, log.players), max_turns=max_turns)
        return engine, LogSource(log)

    # -- status ------------------------------------------------------------

    @property
    def pending(self) -> Request | None:
        """The unanswered question, if the game is waiting on one."""
        return self.interpreter.pending

    @property
    def quiescent(self) -> bool:
        """Between actions — the only point at which a save is legal."""
        return self.pending is None and not self.over

    @property
    def over(self) -> bool:
        return self.state.winner is not None or self.machine.finished

    @property
    def winner(self) -> PlayerId | None:
        return self.state.winner

    def status(self) -> Status:
        if self.over:
            return GameOver(self.state.winner)
        request = self.pending
        return Awaiting(request) if request is not None else Quiescent()

    # -- driving -----------------------------------------------------------

    def start(self) -> Status:
        """Begin the first turn and run until somebody has to answer something."""
        if self._started:
            raise EngineError("this game has already started")
        self._started = True
        return self._pump()

    def submit(self, decision: Decision) -> Status:
        """Answer the pending question and carry on.

        The decision is re-validated against the request that asked for it (the
        UI is never trusted) and appended to the log before anything moves.
        """
        if not self._started:
            raise EngineError("call start() before submitting a decision")
        status = self.interpreter.submit(decision)
        if isinstance(status, Awaiting):
            return status
        return self._pump()

    def run(self, source: DecisionSource) -> Status:
        """Play the whole game, taking every answer from ``source``.

        The CLI, the AI, the replayer and the fuzz harness are all this call
        with a different ``source``.
        """
        status = self.start() if not self._started else self.status()
        while isinstance(status, Awaiting):
            status = self.submit(source.answer(status.request))
        return status

    def _pump(self) -> Status:
        """Run turn-machine steps until one asks a question or the game ends."""
        for _ in range(MAX_QUIET_STEPS):
            if self.over:
                return GameOver(self.state.winner)
            ctx = EffectContext.root(self.state, player=self.state.active_player)
            self._adopt(ctx)
            status = self.interpreter.begin(self.machine.step(ctx))
            if isinstance(status, Awaiting):
                return status
            if isinstance(status, GameOver):
                return status
        raise EngineError(
            f"the turn machine ran {MAX_QUIET_STEPS} steps without asking anybody anything; "
            f"rule set '{self.state.rules.id}' cannot make progress from phase "
            f"'{self.state.phase}'"
        )

    # -- reading -----------------------------------------------------------

    @property
    def recent_rolls(self) -> tuple[Any, ...]:
        """Every :class:`~here_to_slay.core.rolls.Roll` made lately, oldest first.

        A read-only window onto the dice, so a UI can show *how* a number was
        reached without subscribing to the bus or reaching into a context. Rolls
        are appended as they are created, so one read mid-Challenge sees the
        sides that have landed so far — which is exactly what the player being
        asked "modify which roll?" wants on screen.
        """
        self._harvest_rolls()
        return tuple(self._rolls)

    def _adopt(self, ctx: EffectContext) -> None:
        """Take over a fresh execution, banking the outgoing one's rolls first."""
        self._harvest_rolls()
        self._ctx = ctx
        self._harvested = 0

    def _harvest_rolls(self) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        rolls = ctx.execution.rolls
        if self._harvested >= len(rolls):
            return
        self._rolls.extend(rolls[self._harvested :])
        self._harvested = len(rolls)
        if len(self._rolls) > MAX_ROLL_HISTORY:
            del self._rolls[: len(self._rolls) - MAX_ROLL_HISTORY]

    def view(self, seat: PlayerId) -> GameView:
        """The board through one seat's eyes. Redacted in the core, never the UI."""
        return build_view(self.state, seat)

    def legal_intents(self, seat: PlayerId | None = None) -> tuple[Intent, ...]:
        """What ``seat`` may legally do.

        While a menu is already open this returns exactly what was offered —
        the engine must not present the UI with a different set from the one it
        will validate against.
        """
        seat = seat if seat is not None else self.state.active_player
        request = self.pending
        if isinstance(request, ChooseIntent) and request.requester == seat:
            return tuple(request.intents)
        return legal_intents(self.state, seat)

    def __repr__(self) -> str:
        return (
            f"<Engine turn {self.state.turn_number} phase {self.state.phase!r} "
            f"active {self.state.active_player} {self.status()}>"
        )


__all__ = ["Engine"]
