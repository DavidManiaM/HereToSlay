"""Who has won, and when we look.

Victory is a loop over ``rules.victory`` for every seat — never a Python
comparison against 3 — so "slay 3 Monsters", "have a Hero of every class" and a
variant's "hold 10 cards at end of turn" are the same mechanism.

**When** the check runs matters as much as what it checks. It runs after every
resolved action and at every phase boundary, not only at end of turn, because a
variant can slay a Monster on somebody else's turn (``rules_engine.md §2``).
Inside an action — mid-Challenge, mid-roll — the board is provisional: a
Modifier still to be played can undo the slay that would have won. So the check
lands at the first point where the board is settled again.

The first satisfied condition wins. Ties are broken by ``rules.tiebreak``, which
means the *order seats are examined in* is the tiebreak: ``active_player`` looks
at whoever is playing first, so simultaneous wins go to the player whose card
caused them.
"""

from __future__ import annotations

from dataclasses import dataclass

from here_to_slay.content.schema import VictoryDef
from here_to_slay.core.context import EffectContext
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import PlayerId
from here_to_slay.core.interpreter import Flow
from here_to_slay.core.state import GameState


@dataclass(frozen=True, slots=True)
class Victory:
    """Who won, and which condition they won by (the UI wants to say so)."""

    player: PlayerId
    condition: VictoryDef

    @property
    def text(self) -> str:
        return self.condition.text or self.condition.id


def check_order(state: GameState) -> tuple[PlayerId, ...]:
    """The order seats are examined in — that is, the tiebreak."""
    match state.rules.tiebreak:
        case "active_player":
            return state.seat_order_from(state.active_player, include_start=True)
        case _:
            return tuple(state.turn_order)


def satisfied_by(state: GameState, player: PlayerId) -> tuple[VictoryDef, ...]:
    """Every victory condition ``player`` currently meets.

    ``$player`` is bound (that is the ref ``rules.yaml`` uses) and ``$self`` is
    set to the same seat, so a condition may be written either way round.
    """
    ctx = EffectContext.root(state, player=player, bindings={"player": player})
    return tuple(
        victory for victory in state.rules.victory if ctx.test(victory.condition)
    )


def find_winner(state: GameState) -> Victory | None:
    """The first seat meeting any condition, in tiebreak order."""
    for player in check_order(state):
        met = satisfied_by(state, player)
        if met:
            return Victory(player=player, condition=met[0])
    return None


def check_victory(ctx: EffectContext) -> Flow:
    """Look for a winner and, if there is one, end the game.

    Announced through ``player.won`` rather than by writing ``state.winner``, so
    a card that says "you cannot lose while this is in play" has a PRE window to
    land in, and so a win by a rule and a win by ``{op: win_game}`` end the game
    through exactly the same events.
    """
    state = ctx.state
    if state.winner is not None:
        return Outcome.DONE
    victory = find_winner(state)
    if victory is None:
        return Outcome.DONE

    won = yield from ctx.emit(
        "player.won",
        {"player": victory.player, "condition": victory.condition.id, "text": victory.text},
        actor=victory.player,
    )
    if not won.ok:
        return Outcome.DONE
    yield from ctx.emit(
        "game.ended", {"winner": victory.player, "condition": victory.condition.id}
    )
    return Outcome.DONE


__all__ = ["Victory", "check_order", "check_victory", "find_winner", "satisfied_by"]
