"""The three-phase event bus.

::

        ┌──────────────────────────────────────────────┐
  emit →│  PRE      subscribers may MODIFY or CANCEL   │── cancelled ──► stop
        ├──────────────────────────────────────────────┤
        │  RESOLVE  exactly one mutator applies it     │
        ├──────────────────────────────────────────────┤
        │  POST     subscribers REACT                  │
        └──────────────────────────────────────────────┘

Two properties this module exists to guarantee:

**Subscriptions are derived from state, never accumulated.** Cards do not call
``subscribe()``. Every dispatch re-reads which cards are where and asks their
``CardDef.triggers`` whether they care (``architecture_notes.md §3.1``). It
costs a walk over the cards in play and buys three things that a subscription
list cannot: an ability cannot leak after its card leaves play, save/load needs
no bookkeeping, and an AI rollout that clones the state clones the subscriptions
with it for free.

**Reaction windows open inside the frame.** ``rules.yaml`` declares which event
and phase a window opens on; :func:`_open_windows` polls it after that phase's
card subscribers have run. That is what lets a Challenge played into
``card_played`` cancel the very event it is challenging — the alternative,
opening the window around the dispatch, would leave it nothing to cancel.

**Ordering is total and deterministic.** Subscribers sort by
``(-priority, zone_kind, seat_distance, card_id, trigger_index)`` — never a dict
or set iteration order. Replay depends on it, so the sort key is written out in
:func:`subscriptions_for` and tested directly. Higher ``priority`` runs first;
seat distance is measured from the active player, so "the player whose turn it
is reacts first" holds without anyone spelling it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from here_to_slay.content.schema import TriggerDef
from here_to_slay.core.errors import EngineInvariantError
from here_to_slay.core.events import Event, EventFrame, EventResult, Phase
from here_to_slay.core.ids import CardId, PlayerId, zone_kind
from here_to_slay.core.invariants import check_if_strict
from here_to_slay.core.registry import MUTATORS
from here_to_slay.core.state import GameState
from here_to_slay.core.windows import open_window

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext, Execution
    from here_to_slay.core.interpreter import Flow

#: how many seats away from the active player an unowned card counts as
_NO_SEAT = 1 << 16


@dataclass(frozen=True, slots=True)
class Subscription:
    """One card's interest in one event, in one phase."""

    card: CardId
    controller: PlayerId | None
    trigger: TriggerDef
    index: int
    zone: str
    seat_distance: int

    @property
    def sort_key(self) -> tuple[int, str, int, str, int]:
        return (
            -self.trigger.priority,
            self.zone,
            self.seat_distance,
            str(self.card),
            self.index,
        )

    @property
    def once_key(self) -> str:
        return f"{self.trigger.on}:{self.trigger.timing}:{self.index}"


def seat_distances(state: GameState) -> dict[PlayerId, int]:
    """Each seat's distance from the active player, active first."""
    order = state.seat_order_from(state.active_player, include_start=True)
    return {player: index for index, player in enumerate(order)}


def subscriptions_for(state: GameState, event: Event, phase: Phase) -> tuple[Subscription, ...]:
    """Which cards react to ``event`` in ``phase``, in the order they act.

    A trigger is live only while its card sits in the zone kind it declares
    (``while_in``), which is what makes "this ability works from your party" and
    "this one works from your hand" the same mechanism.
    """
    distances = seat_distances(state)
    found: list[Subscription] = []
    for card_id in sorted(state.cards):
        instance = state.cards[card_id]
        definition = state.definition(instance)
        if not definition.triggers:
            continue
        here = zone_kind(instance.zone)
        for index, trigger in enumerate(definition.triggers):
            if trigger.on != event.name or trigger.timing != phase.value:
                continue
            if trigger.while_in != here:
                continue
            controller = instance.controller
            found.append(
                Subscription(
                    card=card_id,
                    controller=controller,
                    trigger=trigger,
                    index=index,
                    zone=here,
                    seat_distance=_NO_SEAT if controller is None else distances[controller],
                )
            )
    return tuple(sorted(found, key=lambda entry: entry.sort_key))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(ctx: EffectContext, event: Event) -> Flow:
    """Push ``event`` through PRE → RESOLVE → POST. Returns an `EventResult`.

    This is a generator: a subscriber may suspend for a decision, and that
    suspension has to travel all the way out to the driver, which is exactly
    what ``yield from`` gives us.
    """
    execution = ctx.execution
    state = execution.state
    _guard_depth(execution, event, state)

    frame = execution.push(EventFrame(event=event))
    execution.record(event)
    try:
        yield from _run_phase(ctx, frame, Phase.PRE)
        if frame.cancelled:
            return EventResult(
                event=frame.event,
                cancelled=True,
                reason=frame.reason,
                discard_source=frame.discard_source,
            )

        _apply(state, frame)
        check_if_strict(state, recent=execution.recent())

        yield from _run_phase(ctx, frame, Phase.RESOLVE_AFTER)
        yield from _run_phase(ctx, frame, Phase.POST)
        return EventResult(event=frame.event)
    finally:
        execution.pop()


def _guard_depth(execution: Execution, event: Event, state: GameState) -> None:
    """A pathological mod card must not be able to hang the engine."""
    cap = state.rules.max_reaction_depth
    if execution.depth >= cap:
        raise EngineInvariantError(
            [
                f"event '{event.name}' would nest {execution.depth + 1} deep, "
                f"past max_reaction_depth {cap}"
            ],
            [str(frame) for frame in execution.stack],
        )


def _apply(state: GameState, frame: EventFrame) -> None:
    """RESOLVE: the one place a dispatched event changes the state."""
    frame.phase = Phase.RESOLVE
    handler = MUTATORS.find(frame.event.name)
    if handler is not None:
        handler(state, frame.event)


def _run_phase(ctx: EffectContext, frame: EventFrame, phase: Phase) -> Flow:
    """Run every subscriber for one phase, honouring cancellation as it goes."""
    frame.phase = phase
    state = ctx.execution.state
    for subscription in subscriptions_for(state, frame.event, phase):
        if frame.cancelled:
            return
        if not _still_live(state, subscription):
            continue  # an earlier subscriber moved or destroyed this card
        if _already_fired(state, subscription):
            continue
        child = ctx.derive(
            self_player=subscription.controller or frame.event.actor,
            source=subscription.card,
            event=frame.event,
            bindings={},
        )
        if not child.test(subscription.trigger.condition):
            continue
        _mark_fired(state, subscription)
        frame.handled_by.append(subscription.card)
        yield from child.run(subscription.trigger.effect)
    yield from _open_windows(ctx, frame, phase)


def _open_windows(ctx: EffectContext, frame: EventFrame, phase: Phase) -> Flow:
    """Open every reaction window ``rules.yaml`` declares for this event+phase.

    Windows run *after* this phase's card subscribers, so a passive that says
    "this cannot be challenged" gets to cancel or modify before anyone is asked.

    They run *inside* the frame, which is the point: a Challenge's
    ``cancel_event`` reaches the event it is challenging. Nothing here knows
    what a Challenge is — only that ``rules.windows`` said to ask.
    """
    if frame.cancelled:
        return
    windows = ctx.execution.state.rules.windows
    for name in sorted(windows):
        window = windows[name]
        if window.on != frame.event.name or window.timing != phase.value:
            continue
        child = ctx.derive(event=frame.event, intent=None, bindings={})
        if not child.test(window.condition):
            continue
        yield from open_window(child, name, frame.event)
        if frame.cancelled:
            return


def _still_live(state: GameState, subscription: Subscription) -> bool:
    instance = state.cards.get(subscription.card)
    return instance is not None and zone_kind(instance.zone) == subscription.trigger.while_in


def _already_fired(state: GameState, subscription: Subscription) -> bool:
    if not subscription.trigger.once_per_turn:
        return False
    instance = state.cards[subscription.card]
    fired = instance.state.get("fired_on_turn", {})
    return fired.get(subscription.once_key) == state.turn_number


def _mark_fired(state: GameState, subscription: Subscription) -> None:
    """Per-turn limits live on the card instance, so they clone and snapshot
    with the state instead of hiding in the bus."""
    if not subscription.trigger.once_per_turn:
        return
    instance = state.cards[subscription.card]
    fired = instance.state.setdefault("fired_on_turn", {})
    fired[subscription.once_key] = state.turn_number


def clear_once_per_turn(state: GameState) -> None:
    """Called by the turn machine (Phase 4) when a new turn starts."""
    for instance in state.cards.values():
        instance.state.pop("fired_on_turn", None)


def subscriber_report(state: GameState, event_name: str) -> tuple[str, ...]:
    """Every card that would react to ``event_name``, for debugging a card that
    "didn't trigger" — the first question a modder asks."""
    event = Event(name=event_name)
    return tuple(
        f"{phase.value}: {sub.card} ({sub.trigger.while_in}, priority {sub.trigger.priority})"
        for phase in Phase
        for sub in subscriptions_for(state, event, phase)
    )


__all__ = [
    "Subscription",
    "clear_once_per_turn",
    "dispatch",
    "seat_distances",
    "subscriber_report",
    "subscriptions_for",
]
