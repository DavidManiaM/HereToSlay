"""``presenter.py`` — the PyGame ``DecisionSource`` bridging engine and GUI threads.

The engine runs on a background worker thread, calling :meth:`answer` whenever
a decision is required. :meth:`answer` publishes the :class:`~core.interpreter.Request`
to the GUI thread and waits on a synchronization primitive. When the player
interacts with the GUI, the GUI thread submits the chosen :class:`~core.interpreter.Decision`,
unblocking the engine.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from here_to_slay.core.interpreter import Decision, DecisionSource, Request

if TYPE_CHECKING:
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine


class PygamePresenter(DecisionSource):
    """Bridges the Engine's Request/Decision flow to the Pygame event loop."""

    def __init__(
        self,
        engine: Engine,
        registry: ContentRegistry | None = None,
    ) -> None:
        self.engine = engine
        self.registry = registry

        self._pending_request: Request | None = None
        self._current_decision: Decision | None = None
        self._decision_event = threading.Event()
        self._lock = threading.Lock()
        self._closed = False

        self._last_seat: str | None = None
        self._transition_target: str | None = None
        self._transition_event = threading.Event()
        self._transition_event.set()  # Start unblocked

    # -----------------------------------------------------------------------
    # DecisionSource Interface (called from Engine background thread)
    # -----------------------------------------------------------------------

    def answer(self, request: Request) -> Decision:
        """Called by Engine when a decision is required."""
        if self._closed:
            raise InterruptedError("PygamePresenter was closed")

        with self._lock:
            # Check for seat transition
            if self._last_seat is not None and request.requester != self._last_seat:
                self._transition_target = request.requester
                self._transition_event.clear()
            else:
                self._transition_target = None
                self._transition_event.set()

            self._last_seat = request.requester
            self._pending_request = request
            self._current_decision = None
            self._decision_event.clear()

        # Wait for seat transition if needed
        self._transition_event.wait()
        if self._closed:
            raise InterruptedError("PygamePresenter was closed")

        # Wait for GUI thread to submit decision
        self._decision_event.wait()
        if self._closed or self._current_decision is None:
            raise InterruptedError("PygamePresenter was closed")

        with self._lock:
            decision = self._current_decision
            self._pending_request = None
            self._current_decision = None
            return decision

    # -----------------------------------------------------------------------
    # GUI Thread Interface
    # -----------------------------------------------------------------------

    @property
    def pending_request(self) -> Request | None:
        """The currently active request awaiting user input, or None."""
        with self._lock:
            return self._pending_request

    @property
    def transition_seat(self) -> str | None:
        """Seat ID if currently waiting on an interstitial seat transition."""
        with self._lock:
            return self._transition_target

    def acknowledge_transition(self) -> None:
        """Called by GUI when player clicks through the seat transition screen."""
        with self._lock:
            self._transition_target = None
            self._transition_event.set()

    def submit_decision(self, decision: Decision) -> None:
        """Called by GUI when the player selects a legal decision."""
        with self._lock:
            self._current_decision = decision
            self._decision_event.set()

    def close(self) -> None:
        """Signals shutdown to unblock any waiting thread."""
        with self._lock:
            self._closed = True
            self._transition_event.set()
            self._decision_event.set()


__all__ = ["PygamePresenter"]
