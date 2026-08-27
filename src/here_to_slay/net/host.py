"""The host: opens a port, seats whoever turns up, settles every decision.

The host is the **ordering** authority, not the state authority (see
``session.py``). Its two jobs:

1. run a lobby until the table is full or the host says go, handing out seats in
   the order people arrived — which is also the turn order, so "who joined
   first goes first" needs no extra rule;
2. during the game, settle each decision — take it from whoever owns the seat,
   stamp it with the next sequence number, and broadcast it to everybody
   including itself.

Content is *not* sent. Both ends load the same pack from disk and the handshake
compares `content_hash`, exactly as ``replay`` does before re-running a log. A
mismatch is refused with the two hashes in the message, because "your Dragon
says 11 and mine says 10" is the kind of thing that otherwise surfaces as an
inexplicable desync forty minutes later.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from here_to_slay.core.interpreter import Decision
from here_to_slay.net.protocol import (
    DECISION,
    DEFAULT_PORT,
    HELLO,
    LOBBY,
    PROTOCOL_VERSION,
    REFUSED,
    START,
    WELCOME,
    Connection,
    Disconnected,
    Message,
    NetError,
    message,
)
from here_to_slay.net.session import (
    Applied,
    DecisionRelay,
    Seat,
    SessionClosed,
    pump,
    seat_ids,
)


@dataclass(slots=True)
class HostConfig:
    """Everything the host decides before anybody connects."""

    host_name: str = "Host"
    port: int = DEFAULT_PORT
    #: seats in total, humans plus AI plus everyone dialling in
    players: int = 4
    #: how many of the trailing seats an agent plays
    ai_seats: int = 0
    seed: str = ""
    max_turns: int = 0
    content_hash: str = ""
    packs: Sequence[str] = ()
    bind: str = "0.0.0.0"

    @property
    def remote_slots(self) -> int:
        """Seats that must be filled from the network: everyone but us and the AI."""
        return max(0, self.players - 1 - self.ai_seats)


class GameHost:
    """Accepts players, then settles decisions for the table.

    Threading: one acceptor thread while the lobby is open, one reader thread
    per client for the whole session, and the caller's thread runs the engine.
    Everything shared goes through :class:`DecisionRelay` or the lobby lock.
    """

    def __init__(
        self,
        config: HostConfig,
        *,
        on_lobby: Callable[[list[Seat]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.relay = DecisionRelay()
        self.seats: list[Seat] = [Seat(seat_ids(config.players)[0], config.host_name)]
        self.on_lobby = on_lobby
        self.on_error = on_error
        self._server: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._accepting = threading.Event()
        self._started = False
        self._threads: list[threading.Thread] = []

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> int:
        """Bind and start accepting. Returns the port actually bound."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((self.config.bind, self.config.port))
        except OSError as exc:
            server.close()
            raise NetError(
                f"could not open port {self.config.port}: "
                f"{(exc.strerror or str(exc)).lower()}"
            ) from None
        server.listen(8)
        server.settimeout(0.25)
        self._server = server
        port = server.getsockname()[1]
        self.config.port = port
        self._accepting.set()
        thread = threading.Thread(target=self._accept_loop, name="hts-accept", daemon=True)
        thread.start()
        self._threads.append(thread)
        self._announce_lobby()
        return port

    def close(self, reason: str = "") -> None:
        self._stop.set()
        self._accepting.clear()
        self.relay.close(reason)
        if self._server is not None:
            with contextlib.suppress(OSError):
                self._server.close()
            self._server = None
        with self._lock:
            connections = [seat.connection for seat in self.seats if seat.connection]
        for connection in connections:
            connection.close(say_goodbye=True)

    def __enter__(self) -> GameHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- lobby -------------------------------------------------------------

    @property
    def waiting_for(self) -> int:
        """How many more people must connect before the table is full."""
        with self._lock:
            joined = sum(1 for seat in self.seats if seat.connection is not None)
        return max(0, self.config.remote_slots - joined)

    @property
    def ready(self) -> bool:
        return self.waiting_for == 0

    def roster(self) -> list[Seat]:
        """The table as it stands, in seat order. AI seats are appended last."""
        with self._lock:
            filled = list(self.seats)
        ids = seat_ids(self.config.players)
        for index in range(len(filled), self.config.players):
            filled.append(Seat(ids[index], f"AI {index + 1}", is_ai=True))
        return filled

    def _accept_loop(self) -> None:
        while self._accepting.is_set() and not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                sock, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._greet, args=(sock,), name="hts-greet", daemon=True
            ).start()

    def _greet(self, sock: socket.socket) -> None:
        connection = Connection(sock)
        try:
            hello = connection.receive(timeout=10.0)
            if hello is None or hello.kind != HELLO:
                self._refuse(connection, "expected a hello")
                return
            problem = self._vet(hello)
            if problem:
                self._refuse(connection, problem)
                return
            seat = self._seat(connection, str(hello.get("name") or "Player"))
            if seat is None:
                self._refuse(connection, "the table is full")
                return
            connection.send(
                message(
                    WELCOME,
                    seat=seat.seat_id,
                    version=PROTOCOL_VERSION,
                    players=self.config.players,
                    seed=self.config.seed,
                    max_turns=self.config.max_turns,
                    content_hash=self.config.content_hash,
                    packs=list(self.config.packs),
                    names=[s.name for s in self.roster()],
                )
            )
            self._threads.append(
                pump(
                    connection,
                    lambda msg, s=seat: self._on_client_message(s, msg),
                    stop=self._stop,
                    on_error=lambda exc, s=seat: self._on_client_gone(s, exc),
                )
            )
            self._announce_lobby()
        except NetError as exc:
            self._report(str(exc))
            connection.close()

    def _vet(self, hello: Message) -> str:
        """Why this client may not join, or an empty string."""
        version = hello.get("version")
        if version != PROTOCOL_VERSION:
            return (
                f"this game speaks protocol {PROTOCOL_VERSION}, "
                f"the joining client speaks {version}"
            )
        theirs = str(hello.get("content_hash") or "")
        ours = self.config.content_hash
        if ours and theirs and theirs != ours:
            return (
                "you are running different content: "
                f"host {ours[:12]}, you {theirs[:12]}. Same pack, same edits, "
                "including plugin.py."
            )
        if self._started:
            return "that game has already started"
        return ""

    def _seat(self, connection: Connection, name: str) -> Seat | None:
        ids = seat_ids(self.config.players)
        with self._lock:
            index = len(self.seats)
            if index >= self.config.players - self.config.ai_seats:
                return None
            seat = Seat(ids[index], name or f"Player {index + 1}", connection=connection)
            self.seats.append(seat)
            return seat

    def _refuse(self, connection: Connection, why: str) -> None:
        with contextlib.suppress(NetError):
            connection.send(message(REFUSED, reason=why))
        connection.close()

    def _announce_lobby(self) -> None:
        roster = self.roster()
        payload = message(
            LOBBY,
            names=[seat.name for seat in roster],
            seats=[seat.seat_id for seat in roster],
            ai=[seat.is_ai for seat in roster],
            waiting=self.waiting_for,
        )
        self._broadcast(payload)
        if self.on_lobby is not None:
            self.on_lobby(roster)

    # -- the game ----------------------------------------------------------

    def start(self) -> list[Seat]:
        """Close the lobby and tell everyone to deal. Returns the final table."""
        self._started = True
        self._accepting.clear()
        if self._server is not None:
            with contextlib.suppress(OSError):
                self._server.close()
            self._server = None
        roster = self.roster()
        self._broadcast(
            message(
                START,
                names=[seat.name for seat in roster],
                seats=[seat.seat_id for seat in roster],
                ai=[seat.is_ai for seat in roster],
                seed=self.config.seed,
                max_turns=self.config.max_turns,
            )
        )
        return roster

    def settle(self, seat: str, decision: Decision) -> None:
        """Stamp a decision with the next index and give it to the whole table.

        Called for the host's own answers too. The host applying its decision
        without broadcasting it would be the one way this design can drift.
        """
        applied = Applied(
            index=self.relay.count, seat=seat, kind=decision.kind, data=decision.as_data()
        )
        self._broadcast(applied.as_message())
        self.relay.accept(applied)

    def _on_client_message(self, seat: Seat, msg: Message) -> None:
        if msg.kind == DECISION:
            try:
                decision = Decision.from_data(str(msg["kind"]), dict(msg.get("data") or {}))
            except Exception as exc:
                self._report(f"{seat.name} sent an answer we could not read: {exc}")
                return
            try:
                self.settle(seat.seat_id, decision)
            except SessionClosed as exc:
                self._report(str(exc))
        elif msg.kind == HELLO:
            with self._lock:
                seat.name = str(msg.get("name") or seat.name)
            self._announce_lobby()

    def _on_client_gone(self, seat: Seat, exc: Exception) -> None:
        if self._stop.is_set():
            return
        # Mid-game there is no recovery: lockstep has no way to answer for a
        # seat nobody owns. Saying so and ending is better than hanging on a
        # question that will never be answered.
        if self._started:
            self.relay.close(f"{seat.name} left the game")
        self._report(f"{seat.name} disconnected: {exc}")
        self._announce_lobby()

    def _broadcast(self, msg: Message, *, skip: Connection | None = None) -> None:
        with self._lock:
            targets = [
                seat.connection
                for seat in self.seats
                if seat.connection is not None and seat.connection is not skip
            ]
        for connection in targets:
            # A send that fails here is not this method's problem: the reader
            # thread on that connection will notice and report it once.
            with contextlib.suppress(Disconnected):
                connection.send(msg)

    def _report(self, text: str) -> None:
        if self.on_error is not None:
            self.on_error(text)


__all__ = ["GameHost", "HostConfig"]
