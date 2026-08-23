"""The bridge between the engine thread and the frame loop.

The engine is a generator driven by ``DecisionSource.answer(request)``, which
*blocks* until an answer exists. A 60 Hz render loop cannot block. So the engine
runs on its own thread and this class is the airlock: :meth:`answer` publishes
the request and waits on an ``Event``; the GUI thread reads
:attr:`pending_request`, draws whatever it implies, and calls
:meth:`submit_decision` when the player clicks.

Three things it does beyond the plumbing:

* **AI seats.** Any seat not in ``human_seats`` is answered by an agent, after a
  short deliberate pause so the player can watch what happened. That is what
  makes a six-player game playable by one person, and it needed no engine
  change — an agent is just another ``DecisionSource``.
* **Hot-seat privacy.** When control passes between two *humans* it raises a
  transition flag and waits for acknowledgement, so nobody sees the next
  player's hand. Passing to an AI never interrupts.
* **Pause / step.** The dev console can freeze the engine between decisions
  without touching engine state, because "frozen" here just means "do not
  answer yet".

Everything the GUI thread reads is behind a lock and returns immutable values,
so a torn read cannot produce a half-drawn frame.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from here_to_slay.core.interpreter import Decision, DecisionSource, Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine

#: How long an AI seat "thinks", so its move is legible rather than instant.
DEFAULT_AI_DELAY = 0.55
#: How often a waiting thread re-checks for shutdown, in seconds.
WAKE_INTERVAL = 0.05


class PygamePresenter(DecisionSource):
    """Answers engine questions from the GUI thread, an agent, or both."""

    def __init__(
        self,
        engine: Engine,
        registry: ContentRegistry | None = None,
        *,
        human_seats: Iterable[str] | None = None,
        agent: DecisionSource | None = None,
        ai_delay: float = DEFAULT_AI_DELAY,
        on_request: Callable[[Request], None] | None = None,
        on_decision: Callable[[Request, Decision], None] | None = None,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.agent = agent
        self.ai_delay = max(0.0, ai_delay)
        self.on_request = on_request
        self.on_decision = on_decision
        #: ``None`` means "every seat is human" — the original hot-seat game.
        self.human_seats: set[str] | None = set(human_seats) if human_seats is not None else None

        self._lock = threading.RLock()
        self._pending_request: Request | None = None
        self._current_decision: Decision | None = None
        self._decision_event = threading.Event()
        self._closed = False

        self._last_seat: str | None = None
        self._transition_target: str | None = None
        self._transition_event = threading.Event()
        self._transition_event.set()

        self._paused = False
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._step_once = False

        #: Set while an AI seat is deliberating, so the UI can say "Bob is
        #: thinking" instead of showing an empty action menu.
        self._thinking_seat: str | None = None
        self.decisions_made = 0
        self.error: BaseException | None = None

    # ------------------------------------------------------------------
    # Engine thread
    # ------------------------------------------------------------------

    def answer(self, request: Request) -> Decision:
        """Called by the engine, on the engine thread, and blocks."""
        if self._closed:
            raise InterruptedError("presenter closed")

        if self.on_request is not None:
            self.on_request(request)

        if self._is_ai(request.requester):
            return self._answer_with_agent(request)

        with self._lock:
            previous = self._last_seat
            needs_transition = (
                previous is not None
                and request.requester != previous
                and not self._is_ai(previous)
            )
            if needs_transition:
                self._transition_target = request.requester
                self._transition_event.clear()
            else:
                self._transition_target = None
                self._transition_event.set()
            self._last_seat = request.requester
            self._thinking_seat = None
            self._pending_request = request
            self._current_decision = None
            self._decision_event.clear()

        self._wait(self._transition_event)
        self._wait(self._resume_event)
        self._wait(self._decision_event)

        with self._lock:
            decision = self._current_decision
            self._pending_request = None
            self._current_decision = None
        if decision is None:
            raise InterruptedError("presenter closed while waiting for a decision")
        self.decisions_made += 1
        if self.on_decision is not None:
            self.on_decision(request, decision)
        return decision

    def _answer_with_agent(self, request: Request) -> Decision:
        agent = self.agent
        if agent is None:
            raise InterruptedError(
                f"seat '{request.requester}' is not human but no agent was supplied"
            )
        with self._lock:
            self._last_seat = request.requester
            self._thinking_seat = request.requester
            # Publishing the request even for an AI seat lets the board render
            # what is being asked ("Bob is choosing a Monster") rather than
            # freezing for half a second with no explanation.
            self._pending_request = request
        self._wait(self._resume_event)
        decision = agent.answer(request)
        self._sleep(self.ai_delay)
        with self._lock:
            self._pending_request = None
            self._thinking_seat = None
        if self._closed:
            raise InterruptedError("presenter closed")
        self.decisions_made += 1
        if self.on_decision is not None:
            self.on_decision(request, decision)
        return decision

    def _wait(self, event: threading.Event) -> None:
        """Wait, but wake often enough to notice a shutdown."""
        while not event.wait(WAKE_INTERVAL):
            if self._closed:
                raise InterruptedError("presenter closed")
        if self._closed:
            raise InterruptedError("presenter closed")

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._closed:
                raise InterruptedError("presenter closed")
            time.sleep(min(WAKE_INTERVAL, max(0.0, deadline - time.monotonic())))

    def _is_ai(self, seat: str | None) -> bool:
        if seat is None or self.human_seats is None or self.agent is None:
            return False
        return seat not in self.human_seats

    # ------------------------------------------------------------------
    # GUI thread
    # ------------------------------------------------------------------

    @property
    def pending_request(self) -> Request | None:
        """The open question, or ``None``. Includes questions posed to an AI."""
        with self._lock:
            return self._pending_request

    @property
    def awaiting_human(self) -> Request | None:
        """The open question *this* client must answer."""
        with self._lock:
            if self._thinking_seat is not None:
                return None
            return self._pending_request

    @property
    def thinking_seat(self) -> str | None:
        with self._lock:
            return self._thinking_seat

    @property
    def transition_seat(self) -> str | None:
        with self._lock:
            return self._transition_target

    def acknowledge_transition(self) -> None:
        with self._lock:
            self._transition_target = None
            self._transition_event.set()

    def submit_decision(self, decision: Decision, *, answering: Request | None = None) -> bool:
        """Answer the open question, reporting whether it was accepted.

        ``answering`` is the request the caller *believes* it is answering. The
        engine thread can swap the pending request between the frame that drew
        a menu and the click that presses it, and the engine validates a
        decision against whatever is open now — so an answer aimed at a
        superseded question is dropped here rather than raising over there.
        """
        with self._lock:
            if self._pending_request is None or self._thinking_seat is not None:
                return False
            if answering is not None and answering is not self._pending_request:
                return False
            self._current_decision = decision
            self._decision_event.set()
            return True

    def is_human(self, seat: str) -> bool:
        return not self._is_ai(seat)

    def set_human_seats(self, seats: Iterable[str] | None) -> None:
        with self._lock:
            self.human_seats = set(seats) if seats is not None else None

    # -- pause / step ------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._resume_event.clear()

    def resume(self) -> None:
        self._paused = False
        self._resume_event.set()

    def toggle_pause(self) -> bool:
        self.resume() if self._paused else self.pause()
        return self._paused

    def step(self) -> None:
        """Let exactly one decision through while paused."""
        if not self._paused:
            return
        self._step_once = True
        self._resume_event.set()
        # Re-arm on the next frame; the engine only needs the gate open long
        # enough to take one decision.
        threading.Timer(0.12, self._rearm_pause).start()

    def _rearm_pause(self) -> None:
        if self._paused:
            self._resume_event.clear()
        self._step_once = False

    # -- shutdown ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._transition_event.set()
        self._decision_event.set()
        self._resume_event.set()

    @property
    def closed(self) -> bool:
        return self._closed


__all__ = ["DEFAULT_AI_DELAY", "PygamePresenter"]
