"""Intents: what a seat may legally do, what it costs, and how it resolves.

``legal_intents()`` is the function that makes the UI dumb and the AI easy
(``docs/rules_engine.md §1``): **the engine computes what is legal**, the CLI
prints it as a numbered menu, pygame highlights the matching widgets, and the
random agent picks uniformly from it. None of the three ever re-implements a
rule, so when a variant changes what is legal, all three follow with no edit.

For that to hold, legality has to be *data*. An action declares its targets:

.. code-block:: yaml

    - id: play_hero
      cost: {action_points: 1}
      targets:
        - param: card
          from: {selector: cards, of: {player: $self, zone: hand}}
          where: {op: card_kind_is, kind: hero}

and this module expands that into one concrete :class:`Intent` per legal
combination. An action with no ``targets`` is a single intent; an action whose
targets have no candidates is not offered at all — which is why "you may only
play a Hero if you are holding one" needs no Python.

Resolution follows ``rules_engine.md §3`` exactly:

===  =========================================  ==================
 #   step                                       interruptible?
===  =========================================  ==================
 1   ``action.declared``                        PRE: cancellable
 2   pay the cost, then ``action.paid``         pluggable via ``@cost``
 3   run the action's effect                    the card's own story
 4   ``action.completed``                       POST triggers
===  =========================================  ==================
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from here_to_slay.content.schema import ActionDef, PhaseDef, TargetDef
from here_to_slay.core.context import EffectContext
from here_to_slay.core.errors import EffectError, EngineError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import CardId, PlayerId
from here_to_slay.core.interpreter import Flow, Intent
from here_to_slay.core.registry import COSTS, cost
from here_to_slay.core.state import GameState

Params = dict[str, Any]

#: how many concrete intents one action may expand into before we call it a
#: content bug rather than a menu
MAX_INTENTS_PER_ACTION = 200

#: intent fields a target may fill; anything else lands in ``Intent.params``
INTENT_FIELDS = ("card", "target")


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


@cost("action_points")
def _action_points(ctx: EffectContext, params: Params) -> bool:
    """The base game's only currency.

    ``check_only`` is how :func:`can_afford` asks without spending — a cost
    handler must answer that without mutating and without asking a question,
    because ``legal_intents()`` is a plain function the UI calls every frame.
    """
    player = ctx.state.player(ctx.resolve_player(params.get("player")))
    amount = ctx.resolve_int(params.get("amount"), 0)
    if player.action_points < amount:
        return False
    if not params.get("check_only"):
        player.action_points -= amount
    return True


def can_afford(ctx: EffectContext, costs: Mapping[str, Any], player: PlayerId) -> bool:
    """Whether ``player`` could pay every part of ``costs`` right now."""
    for name, amount in costs.items():
        flow = COSTS.get(name)(ctx, _cost_params(name, amount, player, check_only=True))
        try:
            request = flow.send(None)
        except StopIteration as stop:
            if not stop.value:
                return False
            continue
        flow.close()
        raise EngineError(
            f"cost '{name}' asked a question ({type(request).__name__}) during an "
            f"affordability check; a check_only cost must answer from state alone"
        )
    return True


def pay_costs(ctx: EffectContext, costs: Mapping[str, Any], player: PlayerId) -> Flow:
    """Pay every part of ``costs``. Returns ``True`` only if all of them were.

    Costs are paid in declaration order; a later one failing leaves earlier ones
    paid, which is why affordability is checked up front by ``legal_intents()``.
    """
    for name, amount in costs.items():
        paid = yield from COSTS.get(name)(ctx, _cost_params(name, amount, player))
        if not paid:
            return False
    return True


def _cost_params(
    name: str, amount: Any, player: PlayerId, *, check_only: bool = False
) -> Params:
    return {"resource": name, "amount": amount, "player": player, "check_only": check_only}


# ---------------------------------------------------------------------------
# Legality
# ---------------------------------------------------------------------------


def current_phase(state: GameState) -> PhaseDef | None:
    return next((phase for phase in state.rules.phases if phase.id == state.phase), None)


def allowed_actions(state: GameState) -> tuple[ActionDef, ...]:
    """The actions this phase's ``allows`` list names, in that order."""
    phase = current_phase(state)
    if phase is None:
        return ()
    found = []
    for action_id in phase.allows:
        action = state.rules.action(action_id)
        if action is None:
            raise EngineError(
                f"phase '{phase.id}' allows unknown action '{action_id}' — "
                f"'hts validate' would have caught this"
            )
        if action.enabled:
            found.append(action)
    return tuple(found)


def legal_intents(state: GameState, seat: PlayerId | None = None) -> tuple[Intent, ...]:
    """Everything ``seat`` may legally declare right now.

    Cheap enough to call per frame and per AI rollout: a walk over the phase's
    action list, one affordability check each, and one selector evaluation per
    declared target.
    """
    if state.winner is not None:
        return ()
    seat = seat if seat is not None else state.active_player
    if seat != state.active_player:
        return ()  # reactions are offered through windows, not through the menu
    ctx = EffectContext.root(state, player=seat)
    out: list[Intent] = []
    for action in allowed_actions(state):
        if not can_afford(ctx, action.cost, seat):
            continue
        if not ctx.test(action.requires):
            continue
        out.extend(expand_intents(ctx, action))
    return tuple(out)


def expand_intents(ctx: EffectContext, action: ActionDef) -> tuple[Intent, ...]:
    """One :class:`Intent` per legal combination of the action's targets."""
    if not action.targets:
        return (Intent(action=action.id, label=action.label),)

    combinations: list[dict[str, Any]] = [{}]
    for target in action.targets:
        candidates = _candidates(ctx, target)
        if not candidates:
            return ()  # a target with nothing to point at makes the action illegal
        combinations = [
            {**combination, target.param: candidate}
            for combination in combinations
            for candidate in candidates
        ]
        if len(combinations) > MAX_INTENTS_PER_ACTION:
            raise EngineError(
                f"action '{action.id}' expands to more than {MAX_INTENTS_PER_ACTION} "
                f"intents; narrow one of its targets with a 'where' filter"
            )
    return tuple(_build_intent(ctx, action, combination) for combination in combinations)


def _candidates(ctx: EffectContext, target: TargetDef) -> tuple[Any, ...]:
    chosen = ctx.select(target.source)
    if target.where is None:
        return chosen
    return tuple(item for item in chosen if ctx.matches(target.where, item))


def _build_intent(ctx: EffectContext, action: ActionDef, values: dict[str, Any]) -> Intent:
    fields = {name: values.get(name) for name in INTENT_FIELDS}
    params = {name: value for name, value in values.items() if name not in INTENT_FIELDS}
    return Intent(
        action=action.id,
        card=fields["card"],
        target=fields["target"],
        params=params,
        label=_label(ctx, action, values),
    )


def _label(ctx: EffectContext, action: ActionDef, values: Mapping[str, Any]) -> str:
    """"Play a Hero — Dodgy Dealer". What a numbered CLI menu prints."""
    named = [_name_of(ctx, value) for value in values.values() if value is not None]
    return f"{action.label} - {', '.join(named)}" if named else action.label


def _name_of(ctx: EffectContext, value: Any) -> str:
    text = str(value)
    if text in ctx.state.cards:
        return ctx.describe(CardId(text))
    if text in ctx.state.players:
        return ctx.state.player(PlayerId(text)).name
    return text


def is_legal(state: GameState, seat: PlayerId, intent: Intent) -> bool:
    return any(offered.key() == intent.key() for offered in legal_intents(state, seat))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def perform_action(ctx: EffectContext, intent: Intent, *, player: PlayerId | None = None) -> Flow:
    """Declare, pay for, and resolve one action.

    ``$intent`` is bound for the whole thing, which is how ``rules.yaml`` says
    ``{op: play_card_from_hand, card: $intent.card}`` without the engine
    knowing that "play_hero" means a Hero.
    """
    player = player if player is not None else ctx.me
    action = ctx.rules.action(intent.action)
    if action is None:
        raise EffectError(f"no such action '{intent.action}' in rule set '{ctx.rules.id}'")
    if not action.enabled:
        raise EffectError(f"action '{action.id}' is disabled in rule set '{ctx.rules.id}'")

    scope = ctx.derive(self_player=player, intent=intent, source=intent.card, bindings={})
    payload: dict[str, Any] = {
        "action": action.id,
        "player": player,
        "card": intent.card,
        "target": intent.target,
    }

    declared = yield from scope.emit("action.declared", payload, actor=player)
    if not declared.ok:
        return Outcome.CANCELLED

    paid = yield from pay_costs(scope, action.cost, player)
    if not paid:
        # Nothing has happened yet, so this is a refusal, not a failure: the
        # cost was checked before the intent was offered, and something (a PRE
        # subscriber, a variant's cost op) changed its mind since.
        return Outcome.CANCELLED
    yield from scope.emit("action.paid", {**payload, "cost": dict(action.cost)}, actor=player)

    outcome = yield from scope.run(action.effect)
    yield from scope.emit(
        "action.completed", {**payload, "outcome": str(outcome)}, actor=player
    )
    return outcome


def intents_for(state: GameState, seat: PlayerId, action: str) -> tuple[Intent, ...]:
    """Every legal intent for one action id — the AI's and the tests' shortcut."""
    return tuple(intent for intent in legal_intents(state, seat) if intent.action == action)


__all__ = [
    "allowed_actions",
    "can_afford",
    "current_phase",
    "expand_intents",
    "intents_for",
    "is_legal",
    "legal_intents",
    "pay_costs",
    "perform_action",
]
