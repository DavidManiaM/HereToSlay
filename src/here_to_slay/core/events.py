"""Events, verdicts, and the frames that carry an event through the bus.

Three decisions shape this module, all of them in service of modding:

1. **An event is a name plus a payload, not a class.** ``architecture_notes.md
   §3.3`` says mods may invent event names freely, so a closed hierarchy of
   ``CardDrawnEvent``/``MonsterSlainEvent`` classes would be exactly the wrong
   shape: every new verb in a variant would need a new Python type, and the
   bus would have to know it. A card subscribes with ``on: monster.slain`` — a
   *string* — so the bus matches strings.

2. **The event itself is immutable.** A PRE subscriber that wants to change an
   event returns a *new* one (:meth:`Event.replace`), which keeps the history
   honest: what was emitted and what resolved are two separate records.

3. **Mutable resolution state lives in an :class:`EventFrame`, not the event.**
   ``cancel_event`` has to reach the event currently being dispatched, and the
   depth cap has to count how deep dispatch is nested. Both are properties of
   *this dispatch*, not of the event, so they sit on a frame that the bus pushes
   and pops. The frame stack is also what an invariant report prints.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from here_to_slay.core.ids import CardId, PlayerId


class Phase(StrEnum):
    """The dispatch phases (``docs/architecture_notes.md §3``).

    ``PRE``, ``RESOLVE_AFTER`` and ``POST`` double as a trigger's ``timing:``
    value, so a card subscribes with the same word the bus dispatches with.
    ``RESOLVE`` is the mutator step; nothing subscribes to it, because exactly
    one mutator applies an event and it is not up for negotiation.
    """

    PRE = "pre"
    """Subscribers may modify or cancel. Challenges and "prevent" live here."""
    RESOLVE = "resolve"
    """The single registered mutator writes the state. No subscribers."""
    RESOLVE_AFTER = "resolve_after"
    """After the mutator, before POST: "once it has actually happened"."""
    POST = "post"
    """Subscribers react. Their effects run after the mutation, never during."""


@dataclass(frozen=True, slots=True)
class Event:
    """Something that happened, or is about to.

    ``payload`` is free-form because content defines what an event carries; the
    engine only insists on the well-known keys it mutates on. ``actor`` and
    ``source`` are lifted out of the payload because *every* condition wants
    them (``event_actor_is``, "did my own card do this?").
    """

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    #: the player whose action caused this
    actor: PlayerId | None = None
    #: the card instance that caused this
    source: CardId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    # -- payload access ----------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __contains__(self, key: str) -> bool:
        return key in self.payload

    @property
    def player(self) -> PlayerId | None:
        """``$event.player`` — the payload's player, or the actor."""
        return self.payload.get("player", self.actor)

    @property
    def card(self) -> CardId | None:
        """``$event.card`` — the payload's card, or the source."""
        return self.payload.get("card", self.source)

    @property
    def target(self) -> Any:
        return self.payload.get("target")

    @property
    def noun(self) -> str:
        """``card`` in ``card.drawn`` — what the event is *about*."""
        return self.name.partition(".")[0]

    @property
    def verb(self) -> str:
        """``drawn`` in ``card.drawn``."""
        return self.name.partition(".")[2]

    # -- derivation --------------------------------------------------------

    def replace(self, **changes: Any) -> Event:
        """A copy with payload keys overridden — how a PRE subscriber modifies.

        ``name``, ``actor`` and ``source`` may be changed by passing them
        explicitly; anything else lands in the payload.
        """
        name = changes.pop("name", self.name)
        actor = changes.pop("actor", self.actor)
        source = changes.pop("source", self.source)
        return Event(
            name=name,
            payload={**self.payload, **changes},
            actor=actor,
            source=source,
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "actor": self.actor,
            "source": self.source,
            "payload": dict(self.payload),
        }

    def __str__(self) -> str:
        bits = [f"{key}={value}" for key, value in sorted(self.payload.items())]
        return f"{self.name}({', '.join(bits)})"


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


class VerdictKind(StrEnum):
    CONTINUE = "continue"
    MODIFIED = "modified"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What a PRE subscriber decided about an event.

    Built through the three constructors rather than by hand, so a subscriber
    cannot return ``MODIFIED`` without an event or ``CANCELLED`` without a
    reason the log can show.
    """

    kind: VerdictKind
    event: Event | None = None
    reason: str = ""

    @classmethod
    def proceed(cls) -> Verdict:
        return CONTINUE

    @classmethod
    def modified(cls, event: Event) -> Verdict:
        return cls(VerdictKind.MODIFIED, event=event)

    @classmethod
    def cancelled(cls, reason: str = "") -> Verdict:
        return cls(VerdictKind.CANCELLED, reason=reason)

    @property
    def is_cancelled(self) -> bool:
        return self.kind is VerdictKind.CANCELLED

    @property
    def is_modified(self) -> bool:
        return self.kind is VerdictKind.MODIFIED


CONTINUE = Verdict(VerdictKind.CONTINUE)


# ---------------------------------------------------------------------------
# Dispatch frames
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EventFrame:
    """One in-flight dispatch. The bus owns a stack of these.

    Everything mutable about resolving an event lives here rather than on the
    :class:`Event`, so the event stays a clean record of what was announced.
    """

    event: Event
    phase: Phase = Phase.PRE
    depth: int = 0
    cancelled: bool = False
    reason: str = ""
    #: ``cancel_event: {and_discard: true}`` — read by whoever opened the frame
    discard_source: bool = False
    #: subscriber card ids that already ran, for the report
    handled_by: list[CardId] = field(default_factory=list)

    def cancel(self, reason: str = "", *, and_discard: bool = False) -> None:
        self.cancelled = True
        self.reason = reason or self.reason
        self.discard_source = self.discard_source or and_discard

    def modify(self, **changes: Any) -> Event:
        """Replace the in-flight event — the ``MODIFIED`` verdict.

        The bus re-reads ``frame.event`` after every PRE subscriber, so an op
        that calls this changes what the mutator will actually apply. Nothing in
        the base vocabulary does yet; it is here because "cost reduction" and
        "redirect that to me instead" are the first two cards any variant
        writes, and they need a seam, not a special case.
        """
        self.event = self.event.replace(**changes)
        return self.event

    def __str__(self) -> str:
        mark = " CANCELLED" if self.cancelled else ""
        return f"{'  ' * self.depth}{self.event}[{self.phase}]{mark}"


@dataclass(frozen=True, slots=True)
class EventResult:
    """What :meth:`EffectContext.emit` hands back.

    Effects branch on this: a ``draw`` whose ``card.drawn`` was cancelled must
    not go on to say the card was drawn.
    """

    event: Event
    cancelled: bool = False
    reason: str = ""
    discard_source: bool = False

    @property
    def ok(self) -> bool:
        return not self.cancelled

    def __bool__(self) -> bool:
        return not self.cancelled


class Outcome(StrEnum):
    """What running an effect tree produced.

    ``seq`` aborts on ``CANCELLED`` (``docs/card_schemas.md §3.1``), and the
    control-flow ops propagate it upwards, so a cancelled event stops the whole
    branch it was part of rather than only its own step.
    """

    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def is_cancelled(self) -> bool:
        return self is Outcome.CANCELLED


__all__ = [
    "CONTINUE",
    "Event",
    "EventFrame",
    "EventResult",
    "Outcome",
    "Phase",
    "Verdict",
    "VerdictKind",
]
