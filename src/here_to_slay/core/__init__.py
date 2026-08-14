"""The engine: pure Python, deterministic, zero I/O.

``core/`` may import :mod:`here_to_slay.content` (inert data) and nothing above
it — no ``ui``, no ``ai``, no ``pygame``, no ``rich``, and notably no
:mod:`random`, whose ambient state would quietly break replay.
``tests/test_layering.py`` walks the import graph and asserts it.
"""

from here_to_slay.core.errors import (
    EngineError,
    EngineInvariantError,
    SetupError,
    ZoneCapacityError,
    ZoneError,
)
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, card_id, player_id, zone_id
from here_to_slay.core.invariants import check_state, find_violations
from here_to_slay.core.rng import DeterministicRng
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import CardInstance, GameState, PlayerState, diff_snapshots
from here_to_slay.core.view import GameView, build_view
from here_to_slay.core.zones import Zone

__all__ = [
    "CardId",
    "CardInstance",
    "DeterministicRng",
    "EngineError",
    "EngineInvariantError",
    "GameState",
    "GameView",
    "PlayerId",
    "PlayerState",
    "SetupError",
    "Zone",
    "ZoneCapacityError",
    "ZoneError",
    "ZoneId",
    "build_view",
    "card_id",
    "check_state",
    "diff_snapshots",
    "find_violations",
    "new_game",
    "player_id",
    "zone_id",
]
