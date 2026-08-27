"""Glue between the start screen and :mod:`here_to_slay.net`.

The window should not have to know what a socket is, and ``net/`` must not know
what a pygame surface is. This module is the one place that knows both: it turns
a :class:`~here_to_slay.ui.pygame.menu.MenuChoice` into a live lobby, and a live
lobby into the three things ``app.py`` needs to deal a networked game — the seat
names, which seats this machine answers for, and the `DecisionSource` to run the
engine on.

The seat-ownership rule is the whole correctness story, so it is stated once
here and enforced in :meth:`NetSession.local_seats`:

* every seat is answered by **exactly one** machine;
* the host answers its own seat *and* every AI seat, because two machines both
  running an agent would both publish an answer to the same question;
* a client answers its own seat and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from here_to_slay.core.interpreter import Decision, DecisionSource, Request
from here_to_slay.net import (
    GameClient,
    GameHost,
    HostConfig,
    NetError,
    NetworkSource,
    local_addresses,
    seat_ids,
)
from here_to_slay.ui.pygame.menu import MODE_HOST, MODE_JOIN, MenuChoice


@dataclass(slots=True)
class NetSession:
    """A hosted or joined game, from lobby to teardown.

    Exactly one of :attr:`host` / :attr:`client` is set. Everything else here is
    what the window needs to show a lobby and then deal.
    """

    choice: MenuChoice
    host: GameHost | None = None
    client: GameClient | None = None
    #: filled once the table is settled — the deal both ends must agree on
    names: tuple[str, ...] = ()
    seat: str = "p1"
    seed: str = ""
    max_turns: int = 0
    ai_seats: int = 0
    addresses: tuple[str, ...] = ()
    error: str = ""

    @property
    def hosting(self) -> bool:
        return self.host is not None

    @property
    def players(self) -> int:
        return len(self.names) or self.choice.players

    def local_seats(self) -> tuple[str, ...]:
        """The seats this machine answers for. See the module docstring.

        The host's AI seats are the *trailing* ones, matching ``GameSetup``, so
        the same convention holds whether a game is local or networked.
        """
        ids = seat_ids(self.players)
        if not self.hosting:
            return (self.seat,)
        mine = [ids[0]]
        if self.ai_seats:
            mine.extend(ids[len(ids) - self.ai_seats :])
        return tuple(dict.fromkeys(mine))

    def source(
        self,
        local: DecisionSource,
        *,
        on_wait: Callable[[Request], None] | None = None,
    ) -> NetworkSource:
        """The `DecisionSource` this machine's engine runs on."""
        return NetworkSource(
            self.relay,
            self.local_seats(),
            local,
            publish=self._publish,
            on_wait=on_wait,
        )

    @property
    def relay(self):  # type: ignore[no-untyped-def]
        session = self.host or self.client
        if session is None:
            raise NetError("this session is not connected")
        return session.relay

    def _publish(self, seat: str, decision: Decision) -> None:
        """Hand an answer to the table.

        The host stamps and broadcasts it; a client sends it to the host and
        waits for it to come back stamped. Neither applies it locally here —
        one ordering authority, no shortcut for the machine that produced it.
        """
        if self.host is not None:
            self.host.settle(seat, decision)
        elif self.client is not None:
            self.client.send_decision(decision)
        else:  # pragma: no cover - defensive
            raise NetError("this session is not connected")

    def close(self, reason: str = "") -> None:
        if self.host is not None:
            self.host.close(reason)
            self.host = None
        if self.client is not None:
            self.client.close(reason)
            self.client = None


def open_session(
    choice: MenuChoice,
    *,
    content_hash: str = "",
    packs: Sequence[str] = (),
    seed: str = "",
    max_turns: int = 0,
    on_lobby: Callable[[list[str], int], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> NetSession:
    """Start hosting, or join. Raises :class:`NetError` with a readable reason.

    The caller is expected to put the message straight on screen, which is why
    everything ``net/`` raises is already phrased for a person.
    """
    if choice.mode == MODE_HOST:
        return _host(
            choice,
            content_hash=content_hash,
            packs=packs,
            seed=seed,
            max_turns=max_turns,
            on_lobby=on_lobby,
            on_error=on_error,
        )
    if choice.mode == MODE_JOIN:
        return _join(
            choice, content_hash=content_hash, on_lobby=on_lobby, on_error=on_error
        )
    raise NetError(f"'{choice.mode}' is not a network mode")


def _host(
    choice: MenuChoice,
    *,
    content_hash: str,
    packs: Sequence[str],
    seed: str,
    max_turns: int,
    on_lobby: Callable[[list[str], int], None] | None,
    on_error: Callable[[str], None] | None,
) -> NetSession:
    config = HostConfig(
        host_name=choice.name,
        port=choice.port,
        players=choice.players,
        ai_seats=choice.ai_seats,
        seed=seed,
        max_turns=max_turns,
        content_hash=content_hash,
        packs=tuple(packs),
    )
    if config.remote_slots <= 0:
        raise NetError(
            "nobody could join this table — raise the player count, or lower the bots"
        )
    session = NetSession(choice=choice, seed=seed, max_turns=max_turns, ai_seats=choice.ai_seats)

    def lobby_changed(seats: list) -> None:
        session.names = tuple(seat.name for seat in seats)
        if on_lobby is not None:
            on_lobby(list(session.names), host.waiting_for)

    host = GameHost(config, on_lobby=lobby_changed, on_error=on_error or (lambda _t: None))
    port = host.open()
    session.host = host
    session.seat = "p1"
    session.addresses = tuple(local_addresses(port))
    session.names = tuple(seat.name for seat in host.roster())
    return session


def _join(
    choice: MenuChoice,
    *,
    content_hash: str,
    on_lobby: Callable[[list[str], int], None] | None,
    on_error: Callable[[str], None] | None,
) -> NetSession:
    session = NetSession(choice=choice)

    def lobby_changed(names: list[str], waiting: int) -> None:
        session.names = tuple(names)
        if on_lobby is not None:
            on_lobby(list(names), waiting)

    client = GameClient(
        choice.address,
        choice.name,
        content_hash=content_hash,
        on_lobby=lobby_changed,
        on_error=on_error or (lambda _t: None),
    )
    invitation = client.connect()
    session.client = client
    session.seat = invitation.seat
    session.seed = invitation.seed
    session.max_turns = invitation.max_turns
    session.names = invitation.names or tuple(
        f"Jucător {i + 1}" for i in range(invitation.players)
    )
    # A client never runs an agent: the host owns every AI seat, or two machines
    # would answer the same question.
    session.ai_seats = 0
    return session


__all__ = ["NetSession", "open_session"]
