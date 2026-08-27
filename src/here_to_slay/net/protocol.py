"""The wire: newline-delimited JSON over TCP, and the messages that cross it.

Deliberately small. The whole networking design rests on one sentence from
``architecture_notes.md §7`` — *"Network play — send decisions, not state"* — so
the only game data that ever crosses the wire is a `Decision`, which already
serialises because the decision log has been round-tripping it since Phase 3.

No dependency, no framing library, no schema: a message is one JSON object on
one line. That is enough for a card game whose entire traffic is a few hundred
bytes per turn, and it stays readable in a packet capture, which matters more
than throughput when something goes wrong on somebody's LAN.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from dataclasses import dataclass, field
from typing import Any

#: Bump when a change would make an old client misread a new host. The handshake
#: refuses a mismatch outright rather than failing later in a way nobody can
#: diagnose from the symptom.
PROTOCOL_VERSION = 1

#: A line longer than this is a bug or an attack, not a game message.
MAX_LINE = 1 << 20

#: How long a blocking read waits before checking whether it should give up.
POLL_SECONDS = 0.25


class NetError(Exception):
    """Anything that went wrong on the wire, phrased for a player.

    Never a bare ``ConnectionResetError`` at the UI: a person who just lost a
    game to a flaky router needs a sentence, not a socket errno.
    """


class Disconnected(NetError):
    """The peer went away. Expected at the end of a game; fatal during one."""


# ---------------------------------------------------------------------------
# Message kinds
# ---------------------------------------------------------------------------

#: client -> host, first thing after connecting
HELLO = "hello"
#: host -> client, the seat you got and everything needed to deal the same game
WELCOME = "welcome"
#: host -> everyone, the lobby changed (someone joined, left, or renamed)
LOBBY = "lobby"
#: host -> everyone, deal now; the game is starting
START = "start"
#: client -> host, "here is my answer to the question I am being asked"
DECISION = "decision"
#: host -> everyone, "this is the answer, apply it" — the only ordering authority
APPLY = "apply"
#: either way, a courtesy note that shows up in the other side's toast
CHAT = "chat"
#: host -> client, you are being turned away, and why
REFUSED = "refused"
#: either way, "I am closing on purpose" — turns a reset into a clean end
BYE = "bye"


@dataclass(slots=True)
class Message:
    """One line on the wire."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        try:
            return self.data[key]
        except KeyError:
            raise NetError(f"message '{self.kind}' is missing '{key}'") from None

    def encode(self) -> bytes:
        return (json.dumps({"t": self.kind, **self.data}, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    @classmethod
    def decode(cls, line: bytes | str) -> Message:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise NetError(f"not a valid message: {exc.msg}") from None
        if not isinstance(payload, dict):
            raise NetError("a message must be a JSON object")
        kind = payload.pop("t", None)
        if not isinstance(kind, str):
            raise NetError("a message must carry a string 't'")
        return cls(kind, payload)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.kind}{self.data}"


def message(kind: str, /, **data: Any) -> Message:
    """``message(HELLO, name="Ana")`` — the terse constructor.

    ``kind`` is positional-only on purpose: a decision message carries the
    *decision's* kind in its payload, so ``message(APPLY, kind="confirmed")``
    has to mean what it reads like rather than colliding with this parameter.
    """
    return Message(kind, dict(data))


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


class Connection:
    """One socket, with a line buffer and a lock around writes.

    Two threads touch a connection in this design — the game thread sends, a
    reader thread receives — so writes are serialised. Reads are not locked
    because exactly one thread ever reads a given connection.
    """

    def __init__(self, sock: socket.socket, *, peer: str = "") -> None:
        self.sock = sock
        self.peer = peer or _peer_name(sock)
        self._buffer = b""
        self._closed = False
        self._write_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, say_goodbye: bool = False) -> None:
        """Shut the socket. ``say_goodbye`` turns a reset into a clean end."""
        if self._closed:
            return
        if say_goodbye:
            with contextlib.suppress(NetError):
                self.send(message(BYE))
        self._closed = True
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.sock.close()

    # -- io ----------------------------------------------------------------

    def send(self, msg: Message) -> None:
        if self._closed:
            raise Disconnected(f"{self.peer} is no longer connected")
        try:
            with self._write_lock:
                self.sock.sendall(msg.encode())
        except OSError as exc:
            self._closed = True
            raise Disconnected(f"could not reach {self.peer}: {_reason(exc)}") from None

    def receive(self, timeout: float | None = None) -> Message | None:
        """The next message, or ``None`` if ``timeout`` elapsed first.

        ``None`` is a timeout and nothing else. A peer that hangs up raises
        :class:`Disconnected`, because "no message yet" and "no message ever"
        are different problems and a caller that conflates them spins forever.
        """
        while True:
            line, self._buffer, found = _split_line(self._buffer)
            if found:
                return Message.decode(line)
            if len(self._buffer) > MAX_LINE:
                self.close()
                raise NetError(f"{self.peer} sent an implausibly long message")
            chunk = self._read_chunk(timeout)
            if chunk is None:
                return None
            if not chunk:
                self._closed = True
                raise Disconnected(f"{self.peer} closed the connection")
            self._buffer += chunk

    def _read_chunk(self, timeout: float | None) -> bytes | None:
        if self._closed:
            raise Disconnected(f"{self.peer} is no longer connected")
        try:
            self.sock.settimeout(timeout)
            return self.sock.recv(65536)
        except TimeoutError:
            return None
        except OSError as exc:
            self._closed = True
            raise Disconnected(f"lost {self.peer}: {_reason(exc)}") from None


def _split_line(buffer: bytes) -> tuple[bytes, bytes, bool]:
    index = buffer.find(b"\n")
    if index < 0:
        return b"", buffer, False
    return buffer[:index], buffer[index + 1 :], True


def _peer_name(sock: socket.socket) -> str:
    try:
        host, port, *_ = sock.getpeername()
        return f"{host}:{port}"
    except OSError:
        return "the other side"


def _reason(exc: OSError) -> str:
    """An OSError as a sentence rather than an errno."""
    return (exc.strerror or str(exc) or type(exc).__name__).lower()


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

DEFAULT_PORT = 57311


def parse_address(text: str, *, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """``"192.168.1.5:57311"`` / ``"192.168.1.5"`` / ``":9000"`` -> host, port.

    Written to be forgiving, because this is typed into a text field by a
    person reading an IP off someone else's screen.
    """
    text = text.strip()
    if not text:
        raise NetError("type an address, like 192.168.1.5")
    host, _, port_text = text.rpartition(":")
    if not host:
        # No colon at all: the whole string is the host.
        host, port_text = text, ""
    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            raise NetError(f"'{port_text}' is not a port number") from None
        if not 1 <= port <= 65535:
            raise NetError(f"port {port} is out of range")
    else:
        port = default_port
    return host.strip() or "127.0.0.1", port


def local_addresses(port: int = DEFAULT_PORT) -> list[str]:
    """Addresses other machines could plausibly use to reach this one.

    Shown on the host's lobby screen so somebody can read one out. The UDP
    connect is the standard trick for "which interface would leave this box" —
    no packet is sent, the kernel just picks a route.
    """
    found: list[str] = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        found.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass
    if not found:
        found.append("127.0.0.1")
    return [f"{address}:{port}" for address in found]


__all__ = [
    "APPLY",
    "BYE",
    "CHAT",
    "DECISION",
    "DEFAULT_PORT",
    "HELLO",
    "LOBBY",
    "MAX_LINE",
    "PROTOCOL_VERSION",
    "REFUSED",
    "START",
    "WELCOME",
    "Connection",
    "Disconnected",
    "Message",
    "NetError",
    "local_addresses",
    "message",
    "parse_address",
]
