"""The client: dial an address, take a seat, deal the same game.

A client is not a thin terminal. It runs a whole `Engine` of its own, on the
same content and the same seed, and stays in step by consuming the decision
stream the host settles. Its board is `engine.view(my_seat)` — computed here,
redacted in the core, never sent over a wire.

What crosses the wire from this side is one message per question this seat is
asked, and nothing else.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass

from here_to_slay.core.interpreter import Decision
from here_to_slay.net.protocol import (
    APPLY,
    BYE,
    DECISION,
    DEFAULT_PORT,
    HELLO,
    LOBBY,
    PROTOCOL_VERSION,
    REFUSED,
    START,
    WELCOME,
    Connection,
    Message,
    NetError,
    message,
    parse_address,
)
from here_to_slay.net.session import Applied, DecisionRelay, SessionClosed, pump

#: How long we wait for a host to answer a fresh connection before giving up.
CONNECT_TIMEOUT = 8.0


@dataclass(slots=True)
class Invitation:
    """What the host told us when it let us in — everything needed to deal."""

    seat: str
    players: int
    seed: str
    max_turns: int = 0
    content_hash: str = ""
    packs: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    @property
    def seat_index(self) -> int:
        return max(0, self.players and int(self.seat.lstrip("p")) - 1)


class GameClient:
    """One connection to a host, plus the relay its engine will run on."""

    def __init__(
        self,
        address: str,
        name: str,
        *,
        content_hash: str = "",
        on_lobby: Callable[[list[str], int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.host, self.port = parse_address(address, default_port=DEFAULT_PORT)
        self.name = name or "Player"
        self.content_hash = content_hash
        self.on_lobby = on_lobby
        self.on_error = on_error

        self.relay = DecisionRelay()
        self.invitation: Invitation | None = None
        self.lobby_names: list[str] = []
        self.waiting_for = 0

        self._connection: Connection | None = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._reader: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> Invitation:
        """Dial, shake hands, and come back with our seat.

        Raises :class:`NetError` with something a person can act on: a refusal
        carries the host's reason, and a content mismatch names both hashes.
        """
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            raise NetError(
                f"could not reach {self.host}:{self.port} — "
                f"{(exc.strerror or str(exc)).lower()}"
            ) from None
        connection = Connection(sock, peer=f"{self.host}:{self.port}")
        connection.send(
            message(
                HELLO,
                name=self.name,
                version=PROTOCOL_VERSION,
                content_hash=self.content_hash,
            )
        )
        reply = connection.receive(timeout=CONNECT_TIMEOUT)
        if reply is None:
            connection.close()
            raise NetError(f"{self.host}:{self.port} did not answer in time")
        if reply.kind == REFUSED:
            reason = str(reply.get("reason") or "the host turned us away")
            connection.close()
            raise NetError(reason)
        if reply.kind != WELCOME:
            connection.close()
            raise NetError(f"unexpected reply '{reply.kind}' from the host")

        invitation = Invitation(
            seat=str(reply["seat"]),
            players=int(reply["players"]),
            seed=str(reply.get("seed") or ""),
            max_turns=int(reply.get("max_turns") or 0),
            content_hash=str(reply.get("content_hash") or ""),
            packs=tuple(reply.get("packs") or ()),
            names=tuple(reply.get("names") or ()),
        )
        self.invitation = invitation
        self.lobby_names = list(invitation.names)
        self._connection = connection
        self._reader = pump(
            connection, self._handle, stop=self._stop, on_error=self._on_reader_error
        )
        return invitation

    def close(self, reason: str = "") -> None:
        self._stop.set()
        self.relay.close(reason)
        if self._connection is not None:
            self._connection.close(say_goodbye=True)
            self._connection = None

    def __enter__(self) -> GameClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- lobby -------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started.is_set()

    def wait_for_start(self, timeout: float | None = None) -> bool:
        """Block until the host deals. ``False`` means the timeout won."""
        return self._started.wait(timeout)

    # -- during the game ---------------------------------------------------

    def send_decision(self, decision: Decision) -> None:
        """Answer the question this seat was asked.

        The answer is *not* applied locally here. It goes to the host, which
        stamps it and broadcasts it back; this engine applies it when it arrives
        like everyone else's. One ordering authority, no special case for the
        machine that happened to produce the answer.
        """
        if self._connection is None:
            raise SessionClosed("not connected to a game")
        self._connection.send(
            message(DECISION, kind=decision.kind, data=decision.as_data())
        )

    # -- plumbing ----------------------------------------------------------

    def _handle(self, msg: Message) -> None:
        match msg.kind:
            case _ if msg.kind == APPLY:
                try:
                    self.relay.accept(Applied.from_message(msg))
                except SessionClosed as exc:
                    self._report(str(exc))
            case _ if msg.kind == LOBBY:
                self.lobby_names = [str(n) for n in (msg.get("names") or ())]
                self.waiting_for = int(msg.get("waiting") or 0)
                if self.on_lobby is not None:
                    self.on_lobby(list(self.lobby_names), self.waiting_for)
            case _ if msg.kind == START:
                self.lobby_names = [str(n) for n in (msg.get("names") or ())]
                self._started.set()
                if self.on_lobby is not None:
                    self.on_lobby(list(self.lobby_names), 0)
            case _ if msg.kind == BYE:
                self.relay.close("the host ended the game")
                self._stop.set()
            case _ if msg.kind == REFUSED:
                self._report(str(msg.get("reason") or "the host turned us away"))

    def _on_reader_error(self, exc: Exception) -> None:
        self.relay.close(f"lost the host: {exc}")
        self._started.set()  # unblock anybody waiting in the lobby
        self._report(f"lost the host: {exc}")

    def _report(self, text: str) -> None:
        if self.on_error is not None:
            self.on_error(text)


__all__ = ["CONNECT_TIMEOUT", "GameClient", "Invitation"]
