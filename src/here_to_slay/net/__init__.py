"""Lockstep network play: the determinism theorem, used in anger.

``Game = f(content_hash, seed, max_turns, [decisions])`` is the engine's central
claim. This package is what happens when you take it literally: give every
machine the same content and the same seed, send only the decisions, and every
engine reaches the same state on its own.

No board is ever serialised. A client computes its own view, redacted for its
own seat by the same ``build_view`` the host uses.

* :mod:`~here_to_slay.net.protocol` — newline-delimited JSON over TCP
* :mod:`~here_to_slay.net.session` — the decision relay and the `DecisionSource`
  every networked engine runs on
* :mod:`~here_to_slay.net.host` — opens a port, seats arrivals, settles order
* :mod:`~here_to_slay.net.client` — dials in, deals the same game

Layering: `net/` may see `core/` (it speaks in `Decision`s) and nothing above it
— no `ui`, no `pygame`. ``tests/test_layering.py`` asserts it.

The trust model is stated plainly in :mod:`~here_to_slay.net.session`: lockstep
means every machine holds the whole state. Play with people you can see.
"""

from here_to_slay.net.client import GameClient, Invitation
from here_to_slay.net.host import GameHost, HostConfig
from here_to_slay.net.protocol import (
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    Connection,
    Disconnected,
    Message,
    NetError,
    local_addresses,
    parse_address,
)
from here_to_slay.net.session import (
    Applied,
    DecisionRelay,
    NetworkSource,
    Seat,
    SessionClosed,
    describe_table,
    seat_ids,
)

__all__ = [
    "DEFAULT_PORT",
    "PROTOCOL_VERSION",
    "Applied",
    "Connection",
    "DecisionRelay",
    "Disconnected",
    "GameClient",
    "GameHost",
    "HostConfig",
    "Invitation",
    "Message",
    "NetError",
    "NetworkSource",
    "Seat",
    "SessionClosed",
    "describe_table",
    "local_addresses",
    "parse_address",
    "seat_ids",
]
