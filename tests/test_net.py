"""Lockstep network play, proven headlessly on real sockets.

The claim under test is the engine's own theorem: give two machines the same
content and the same seed, send only the decisions, and both reach the same
state. So the acceptance test here is not "a message arrived" — it is *two
engines that played a whole game and agree about every card in every zone*.

Everything binds to 127.0.0.1 on port 0, so the suite needs no fixed port and
two runs can overlap.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from here_to_slay.ai.random_agent import RandomAgent
from here_to_slay.content import ContentRegistry
from here_to_slay.core import Engine
from here_to_slay.core.interpreter import Confirmed, Decision, DecisionSource, Request
from here_to_slay.net import (
    GameClient,
    GameHost,
    HostConfig,
    NetError,
    NetworkSource,
    SessionClosed,
)
from here_to_slay.net.protocol import (
    DEFAULT_PORT,
    Connection,
    Message,
    local_addresses,
    message,
    parse_address,
)
from here_to_slay.net.session import Applied, DecisionRelay, seat_ids

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


class TestMessages:
    def test_a_message_round_trips_through_the_wire_format(self) -> None:
        original = message("hello", name="Ana", version=1)
        again = Message.decode(original.encode().rstrip(b"\n"))
        assert again.kind == "hello"
        assert again["name"] == "Ana"
        assert again["version"] == 1

    def test_every_message_is_exactly_one_line(self) -> None:
        """The framing *is* the newline, so a payload may not contain one."""
        encoded = message("chat", text="two\nlines").encode()
        assert encoded.count(b"\n") == 1
        assert Message.decode(encoded.rstrip(b"\n"))["text"] == "two\nlines"

    def test_a_missing_field_names_itself(self) -> None:
        with pytest.raises(NetError, match="missing 'seat'"):
            _ = message("welcome")["seat"]

    @pytest.mark.parametrize("junk", ["not json", "[1,2,3]", '{"no":"kind"}'])
    def test_junk_is_a_net_error_not_a_crash(self, junk: str) -> None:
        with pytest.raises(NetError):
            Message.decode(junk)


class TestAddresses:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("192.168.1.5:57311", ("192.168.1.5", 57311)),
            ("192.168.1.5", ("192.168.1.5", DEFAULT_PORT)),
            ("  10.0.0.2:9000  ", ("10.0.0.2", 9000)),
            ("localhost", ("localhost", DEFAULT_PORT)),
        ],
    )
    def test_what_a_person_actually_types(self, text: str, expected: tuple[str, int]) -> None:
        assert parse_address(text) == expected

    @pytest.mark.parametrize("bad", ["", "   ", "host:nope", "host:70000"])
    def test_a_bad_address_says_why(self, bad: str) -> None:
        with pytest.raises(NetError):
            parse_address(bad)

    def test_the_host_can_offer_an_address_to_read_out(self) -> None:
        found = local_addresses(1234)
        assert found and all(a.endswith(":1234") for a in found)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestTheRelay:
    def test_a_waiter_is_woken_when_the_decision_lands(self) -> None:
        relay = DecisionRelay()
        got: list[Any] = []

        def wait() -> None:
            got.append(relay.next_after(0, timeout=5.0))

        thread = threading.Thread(target=wait)
        thread.start()
        relay.accept(Applied(0, "p1", "confirmed", {"ok": True}))
        thread.join(timeout=5.0)
        assert got and got[0].seat == "p1"

    def test_a_gap_in_the_sequence_fails_loudly(self) -> None:
        """Lockstep cannot recover from a lost decision. Drifting quietly into a
        wrong board is the one outcome worse than stopping."""
        relay = DecisionRelay()
        relay.accept(Applied(0, "p1", "confirmed", {"ok": True}))
        with pytest.raises(SessionClosed, match="out of step"):
            relay.accept(Applied(5, "p1", "confirmed", {"ok": True}))

    def test_waiting_on_a_closed_session_raises_rather_than_hanging(self) -> None:
        relay = DecisionRelay()
        relay.close("everyone left")
        with pytest.raises(SessionClosed, match="everyone left"):
            relay.next_after(0, timeout=0.1)

    def test_a_timeout_is_none_not_an_error(self) -> None:
        assert DecisionRelay().next_after(0, timeout=0.01) is None


class TestTheNetworkSource:
    """The source answers locally or waits, but always consumes what the table
    settled — including its own answer, which is what keeps the order single."""

    def _source(self, relay: DecisionRelay, seats: list[str]) -> tuple[NetworkSource, list]:
        published: list[tuple[str, Decision]] = []

        class Always(DecisionSource):
            def answer(self, request: Request) -> Decision:
                return Confirmed(True)

        source = NetworkSource(
            relay, seats, Always(), publish=lambda s, d: published.append((s, d))
        )
        return source, published

    def test_a_local_seat_is_asked_here_and_published(self) -> None:
        relay = DecisionRelay()
        source, published = self._source(relay, ["p1"])

        def settle() -> None:
            relay.next_after(0, timeout=5.0)

        # Publishing is what the host/client turns into a broadcast; here we
        # short-circuit it so the answer comes straight back.
        original = source.publish

        def publish(seat: str, decision: Decision) -> None:
            original(seat, decision)
            relay.accept(Applied(relay.count, seat, decision.kind, decision.as_data()))

        source.publish = publish  # type: ignore[assignment]
        answer = source.answer(_confirm("p1"))
        assert isinstance(answer, Confirmed)
        assert published == [("p1", Confirmed(True))]
        assert source.consumed == 1

    def test_a_remote_seat_waits_and_is_never_asked_locally(self) -> None:
        relay = DecisionRelay()
        asked: list[Request] = []

        class Never(DecisionSource):
            def answer(self, request: Request) -> Decision:
                asked.append(request)
                raise AssertionError("a remote seat must not be answered locally")

        waited: list[Request] = []
        source = NetworkSource(
            relay, ["p1"], Never(), publish=lambda *_: None, on_wait=waited.append
        )
        relay.accept(Applied(0, "p2", "confirmed", {"ok": True}))
        answer = source.answer(_confirm("p2"))
        assert isinstance(answer, Confirmed)
        assert not asked
        assert len(waited) == 1  # the UI gets told whose turn it is

    def test_a_decision_for_the_wrong_seat_is_a_desync(self) -> None:
        relay = DecisionRelay()
        source, _ = self._source(relay, ["p1"])
        relay.accept(Applied(0, "p3", "confirmed", {"ok": True}))
        with pytest.raises(SessionClosed, match="out of step"):
            source.answer(_confirm("p2"))


def _confirm(seat: str) -> Request:
    from here_to_slay.core.interpreter import Confirm

    return Confirm(requester=seat, prompt="?")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End to end, on real sockets
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def content() -> ContentRegistry:
    from here_to_slay.content import load_pack

    return load_pack(PROJECT_ROOT / "data" / "base")


@pytest.fixture
def host() -> Iterator[GameHost]:
    errors: list[str] = []
    config = HostConfig(
        host_name="Ana", port=0, players=2, seed="net-test", bind="127.0.0.1"
    )
    game_host = GameHost(config, on_error=errors.append)
    game_host.errors = errors  # type: ignore[attr-defined]
    try:
        yield game_host
    finally:
        game_host.close()


class TestTheHandshake:
    def test_a_client_gets_a_seat_and_the_deal(self, host: GameHost) -> None:
        port = host.open()
        with GameClient(f"127.0.0.1:{port}", "Bob") as client:
            invitation = client.connect()
            assert invitation.seat == "p2"  # seats go in arrival order
            assert invitation.players == 2
            assert invitation.seed == "net-test"

    def test_the_lobby_reaches_the_host(self, host: GameHost) -> None:
        port = host.open()
        with GameClient(f"127.0.0.1:{port}", "Bob") as client:
            client.connect()
            _settle(lambda: host.waiting_for == 0)
            assert [seat.name for seat in host.roster()] == ["Ana", "Bob"]
            assert host.ready

    def test_different_content_is_refused_with_both_hashes(self, host: GameHost) -> None:
        host.config.content_hash = "a" * 64
        port = host.open()
        client = GameClient(f"127.0.0.1:{port}", "Bob", content_hash="b" * 64)
        with pytest.raises(NetError, match="different content"):
            client.connect()
        client.close()

    def test_a_wrong_protocol_version_is_refused(self, host: GameHost) -> None:
        port = host.open()
        import socket as _socket

        sock = _socket.create_connection(("127.0.0.1", port), timeout=5)
        connection = Connection(sock)
        connection.send(message("hello", name="Old", version=0))
        reply = connection.receive(timeout=5)
        assert reply is not None and reply.kind == "refused"
        assert "protocol" in reply["reason"]
        connection.close()

    def test_a_full_table_turns_the_next_player_away(self, host: GameHost) -> None:
        port = host.open()
        with GameClient(f"127.0.0.1:{port}", "Bob") as first:
            first.connect()
            _settle(lambda: host.ready)
            late = GameClient(f"127.0.0.1:{port}", "Cid")
            with pytest.raises(NetError, match="full"):
                late.connect()
            late.close()

    def test_a_host_that_is_not_there_says_so_plainly(self) -> None:
        client = GameClient("127.0.0.1:1", "Bob")
        with pytest.raises(NetError, match="could not reach"):
            client.connect()
        client.close()


class TestTwoEnginesPlayOneGame:
    """The acceptance test. Two engines, one socket, identical final state."""

    def test_a_whole_game_stays_in_lockstep(
        self, content: ContentRegistry, host: GameHost
    ) -> None:
        port = host.open()
        client = GameClient(f"127.0.0.1:{port}", "Bob")
        client.connect()
        _settle(lambda: host.ready)
        host.start()
        assert client.wait_for_start(timeout=5.0)

        names = [seat.name for seat in host.roster()]
        seats = seat_ids(2)
        outcome: dict[str, Any] = {}

        def run_host() -> None:
            engine = Engine.new(content, names, seed="net-test", max_turns=25)
            source = NetworkSource(
                host.relay,
                [seats[0]],
                RandomAgent(seed=1),
                publish=host.settle,
            )
            with contextlib.suppress(SessionClosed):
                engine.run(source)
            outcome["host"] = engine

        def run_client() -> None:
            engine = Engine.new(content, names, seed="net-test", max_turns=25)
            source = NetworkSource(
                client.relay,
                [seats[1]],
                RandomAgent(seed=2),
                publish=lambda _seat, decision: client.send_decision(decision),
            )
            with contextlib.suppress(SessionClosed):
                engine.run(source)
            outcome["client"] = engine

        threads = [
            threading.Thread(target=run_host, name="host-engine"),
            threading.Thread(target=run_client, name="client-engine"),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            assert not thread.is_alive(), "an engine thread hung"

        left, right = outcome["host"].state, outcome["client"].state
        assert left.turn_number == right.turn_number
        assert left.winner == right.winner
        assert {k: list(v.cards) for k, v in left.zones.items()} == {
            k: list(v.cards) for k, v in right.zones.items()
        }
        # And the decision stream both consumed is the same stream.
        assert host.relay.count == client.relay.count > 0

    def test_a_client_that_leaves_ends_the_game_rather_than_hanging(
        self, host: GameHost
    ) -> None:
        """Lockstep has no way to answer for a seat nobody owns, so the honest
        move is to stop and say who left."""
        port = host.open()
        client = GameClient(f"127.0.0.1:{port}", "Bob")
        client.connect()
        _settle(lambda: host.ready)
        host.start()
        client.close()
        _settle(lambda: host.relay.closed, timeout=10.0)
        assert "Bob" in host.relay.reason


def _settle(predicate: Any, timeout: float = 5.0) -> None:
    """Spin briefly until a threaded side effect has landed."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")
