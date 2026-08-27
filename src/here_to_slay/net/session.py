"""Lockstep multiplayer: every machine runs the same game, decisions cross.

The engine's central theorem is ``Game = f(content_hash, seed, max_turns,
[decisions])``. Network play is that theorem used in anger: give every machine
the same content, the same seed and the same list of players, and the *only*
thing that has to travel is the stream of decisions. Each engine then asks the
same question at the same moment and reaches the same state.

That is why there is no view serialisation anywhere in this package. A client
does not receive a board; it computes one, out of its own engine, redacted for
its own seat by the same ``build_view`` the host uses.

::

    host                                   client
    ────                                   ──────
    Engine (authoritative *ordering*)      Engine (same content + seed)
      │                                      │
      │ asks seat p1 (local)                 │ asks seat p1 (not mine → wait)
      ├─ local UI answers                    │
      ├─ broadcast APPLY ────────────────────┤
      └─ apply                               └─ apply

**The host is the ordering authority, not the state authority.** Every decision,
including the host's own, is broadcast and then applied, so all engines consume
answers in exactly one order. Nothing else is needed to keep them in step, and
a desync would have to be an engine bug — which is worth knowing, so the
sequence number is checked and a mismatch fails loudly instead of drifting.

**Trust model, stated plainly.** Lockstep means every machine holds the whole
state, including other players' hands. The UI never shows them — each client
renders `engine.view(my_seat)`, which is redacted in the core — but a modified
client could look. This is a game for people who can see each other, on a LAN
or through a port they opened for a friend; it is not hardened against a
cheating peer, and pretending otherwise would be the dishonest option. Making it
so would mean host-authoritative play with the full view on the wire, which is a
different design and a much larger one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from here_to_slay.core.interpreter import Decision, DecisionSource, Request
from here_to_slay.net.protocol import (
    APPLY,
    Connection,
    Disconnected,
    Message,
    NetError,
    message,
)

#: How long a seat waits for the network before it checks whether the game is
#: still alive. Not a timeout on the player — a person can think for an hour.
HEARTBEAT_SECONDS = 0.5


class SessionClosed(NetError):
    """The game ended, or somebody left mid-hand. Either way, stop asking."""


@dataclass(slots=True)
class Seat:
    """One place at the table, and who is answering for it."""

    seat_id: str
    name: str
    #: ``None`` means this machine answers for it — a human here, or an agent
    connection: Connection | None = None
    is_ai: bool = False

    @property
    def is_local(self) -> bool:
        return self.connection is None


@dataclass(slots=True)
class Applied:
    """One decision, in the order the host settled on."""

    index: int
    seat: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def decision(self) -> Decision:
        return Decision.from_data(self.kind, self.data)

    def as_message(self) -> Message:
        return message(APPLY, n=self.index, seat=self.seat, kind=self.kind, data=self.data)

    @classmethod
    def from_message(cls, msg: Message) -> Applied:
        return cls(
            index=int(msg["n"]),
            seat=str(msg["seat"]),
            kind=str(msg["kind"]),
            data=dict(msg.get("data") or {}),
        )


class DecisionRelay:
    """The shared half of both ends: a queue of decisions in settled order.

    The engine thread waits on :meth:`next_after`; a reader thread calls
    :meth:`accept` as messages land. Keeping the two apart is what lets the
    pygame window stay at 60 fps while the engine blocks on somebody in another
    room deciding what to play.
    """

    def __init__(self) -> None:
        self._applied: list[Applied] = []
        self._condition = threading.Condition()
        self._closed = False
        self._reason = ""

    # -- producer ----------------------------------------------------------

    def accept(self, applied: Applied) -> None:
        """Record a settled decision and wake anyone waiting for it."""
        with self._condition:
            expected = len(self._applied)
            if applied.index != expected:
                # Out of order means a message was lost or an engine diverged.
                # Both are unrecoverable in lockstep, and both are far easier to
                # diagnose here than three turns later as a wrong board.
                self._closed = True
                self._reason = (
                    f"decision {applied.index} arrived when {expected} was expected — "
                    f"the games are out of step"
                )
                self._condition.notify_all()
                raise SessionClosed(self._reason)
            self._applied.append(applied)
            self._condition.notify_all()

    def close(self, reason: str = "") -> None:
        with self._condition:
            self._closed = True
            self._reason = reason or self._reason
            self._condition.notify_all()

    # -- consumer ----------------------------------------------------------

    def next_after(self, index: int, *, timeout: float | None = None) -> Applied | None:
        """The decision at ``index``, waiting for it if it has not arrived.

        Returns ``None`` on timeout so a caller can check whether the window is
        still open. Raises once the session is closed and the answer will never
        come.
        """
        with self._condition:
            while True:
                if index < len(self._applied):
                    return self._applied[index]
                if self._closed:
                    raise SessionClosed(self._reason or "the game session has ended")
                if not self._condition.wait(timeout):
                    return None

    @property
    def count(self) -> int:
        with self._condition:
            return len(self._applied)

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def reason(self) -> str:
        with self._condition:
            return self._reason


class NetworkSource(DecisionSource):
    """The `DecisionSource` every networked engine runs on.

    One method, as everywhere else in this project. What changes is *who* is
    asked:

    * a seat this machine owns → the local source (the pygame presenter, the
      CLI, or an agent), and the answer is published to the table;
    * anybody else's seat → wait for the host to settle it.

    Either way the decision the engine finally receives is the one that came
    back through :class:`DecisionRelay`, so all engines consume the same answers
    in the same order — including the machine that produced one.
    """

    def __init__(
        self,
        relay: DecisionRelay,
        local_seats: Iterable[str],
        local_source: DecisionSource,
        *,
        publish: Callable[[str, Decision], None],
        on_wait: Callable[[Request], None] | None = None,
    ) -> None:
        self.relay = relay
        self.local_seats = set(local_seats)
        self.local_source = local_source
        self.publish = publish
        #: called once per question this machine is *not* answering, so a UI can
        #: say whose turn it is instead of freezing with no explanation
        self.on_wait = on_wait
        self._consumed = 0

    def answer(self, request: Request) -> Decision:
        seat = str(request.requester)
        if seat in self.local_seats:
            decision = self.local_source.answer(request)
            self.publish(seat, decision)
        elif self.on_wait is not None:
            self.on_wait(request)
        return self._await_settled(seat)

    def _await_settled(self, seat: str) -> Decision:
        while True:
            applied = self.relay.next_after(self._consumed, timeout=HEARTBEAT_SECONDS)
            if applied is None:
                continue
            if applied.seat != seat:
                raise SessionClosed(
                    f"the table settled a decision for '{applied.seat}' while '{seat}' "
                    f"was being asked — the games are out of step"
                )
            self._consumed += 1
            return applied.decision()

    @property
    def consumed(self) -> int:
        """How many settled decisions this engine has taken. The lockstep clock."""
        return self._consumed


def pump(
    connection: Connection,
    handle: Callable[[Message], None],
    *,
    stop: threading.Event,
    on_error: Callable[[NetError], None] | None = None,
) -> threading.Thread:
    """Read a connection on its own thread until it dies or is told to stop."""

    def loop() -> None:
        try:
            while not stop.is_set():
                msg = connection.receive(timeout=HEARTBEAT_SECONDS)
                if msg is None:
                    continue
                handle(msg)
        except Disconnected as exc:
            if not stop.is_set() and on_error is not None:
                on_error(exc)
        except NetError as exc:  # pragma: no cover - defensive
            if on_error is not None:
                on_error(exc)

    thread = threading.Thread(target=loop, name=f"hts-net-{connection.peer}", daemon=True)
    thread.start()
    return thread


def seat_ids(count: int) -> list[str]:
    """``p1..pN`` — the ids ``core/setup.py`` mints, spelled once here."""
    return [f"p{index + 1}" for index in range(count)]


def describe_table(seats: Sequence[Seat]) -> str:
    """A one-line lobby summary for a toast or a log line."""
    return ", ".join(
        f"{seat.name}"
        + (" (you)" if seat.is_local and not seat.is_ai else " (AI)" if seat.is_ai else "")
        for seat in seats
    )


__all__ = [
    "HEARTBEAT_SECONDS",
    "Applied",
    "DecisionRelay",
    "NetworkSource",
    "Seat",
    "SessionClosed",
    "describe_table",
    "pump",
    "seat_ids",
]
