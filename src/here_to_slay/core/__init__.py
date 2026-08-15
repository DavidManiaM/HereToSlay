"""The engine: pure Python, deterministic, zero I/O.

``core/`` may import :mod:`here_to_slay.content` (inert data) and nothing above
it — no ``ui``, no ``ai``, no ``pygame``, no ``rich``, and notably no
:mod:`random`, whose ambient state would quietly break replay.
``tests/test_layering.py`` walks the import graph and asserts it.

Importing this package **registers the op catalogues**: the ``effects``,
``conditions``, ``selectors`` and ``mutators`` modules run their decorators on
import, so ``EFFECTS``, ``CONDITIONS`` and friends are populated by the time
anything can ask for an op. A content pack that ships a ``plugin.py`` adds to
the same tables (``docs/architecture_notes.md §5``).
"""

from here_to_slay.core import (  # noqa: F401  (imported for registration)
    conditions,
    effects,
    mutators,
    selectors,
)
from here_to_slay.core.actions import legal_intents, perform_action
from here_to_slay.core.bus import dispatch, subscriptions_for
from here_to_slay.core.context import EffectContext, Execution
from here_to_slay.core.engine import Engine
from here_to_slay.core.errors import (
    EffectError,
    EngineError,
    EngineInvariantError,
    IllegalDecisionError,
    ReplayError,
    SetupError,
    UnknownOpError,
    ZoneCapacityError,
    ZoneError,
)
from here_to_slay.core.events import Event, EventResult, Outcome, Phase, Verdict
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, card_id, player_id, zone_id
from here_to_slay.core.interpreter import (
    Awaiting,
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Decision,
    DecisionSource,
    GameOver,
    Intent,
    IntentChosen,
    Interpreter,
    Option,
    OptionChosen,
    PlayerChosen,
    Quiescent,
    ReactionChosen,
    ReactionPrompt,
    Request,
    ScriptedSource,
    Status,
    drive,
)
from here_to_slay.core.invariants import check_state, find_violations
from here_to_slay.core.log import DecisionLog, LoggedDecision, LogSource, replay
from here_to_slay.core.registry import (
    CONDITIONS,
    COSTS,
    EFFECTS,
    MUTATORS,
    SELECTORS,
    condition,
    cost,
    effect,
    mutator,
    registered_ops,
    selector,
)
from here_to_slay.core.rng import DeterministicRng
from here_to_slay.core.rolls import Modifier, Roll, perform_roll
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import CardInstance, GameState, PlayerState, diff_snapshots
from here_to_slay.core.turn_machine import TurnMachine
from here_to_slay.core.victory import Victory, check_victory, find_winner
from here_to_slay.core.view import GameView, build_view
from here_to_slay.core.windows import open_window, playable_reactions
from here_to_slay.core.zones import Zone

__all__ = [
    "CONDITIONS",
    "COSTS",
    "EFFECTS",
    "MUTATORS",
    "SELECTORS",
    "Awaiting",
    "CardId",
    "CardInstance",
    "CardsChosen",
    "ChooseCards",
    "ChooseIntent",
    "ChooseOption",
    "ChoosePlayer",
    "Confirm",
    "Confirmed",
    "Decision",
    "DecisionLog",
    "DecisionSource",
    "DeterministicRng",
    "EffectContext",
    "EffectError",
    "Engine",
    "EngineError",
    "EngineInvariantError",
    "Event",
    "EventResult",
    "Execution",
    "GameOver",
    "GameState",
    "GameView",
    "IllegalDecisionError",
    "Intent",
    "IntentChosen",
    "Interpreter",
    "LogSource",
    "LoggedDecision",
    "Modifier",
    "Option",
    "OptionChosen",
    "Outcome",
    "Phase",
    "PlayerChosen",
    "PlayerId",
    "PlayerState",
    "Quiescent",
    "ReactionChosen",
    "ReactionPrompt",
    "ReplayError",
    "Request",
    "Roll",
    "ScriptedSource",
    "SetupError",
    "Status",
    "TurnMachine",
    "UnknownOpError",
    "Verdict",
    "Victory",
    "Zone",
    "ZoneCapacityError",
    "ZoneError",
    "ZoneId",
    "build_view",
    "card_id",
    "check_state",
    "check_victory",
    "condition",
    "cost",
    "diff_snapshots",
    "dispatch",
    "drive",
    "effect",
    "find_violations",
    "find_winner",
    "legal_intents",
    "mutator",
    "new_game",
    "open_window",
    "perform_action",
    "perform_roll",
    "playable_reactions",
    "player_id",
    "registered_ops",
    "replay",
    "selector",
    "subscriptions_for",
    "zone_id",
]
