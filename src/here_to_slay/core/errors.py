"""Engine exceptions.

The rule these follow: **a bad card should point at itself**. A mod's broken
content must fail with a message naming the card, the zone and the rule that
was violated — not surface three turns later as a mystery ``KeyError``.
"""

from __future__ import annotations

from collections.abc import Sequence


class EngineError(Exception):
    """Base class for every failure raised by ``core/``."""


class SetupError(EngineError):
    """A game cannot be built from this content + player list."""


class ZoneError(EngineError):
    """An illegal zone operation (missing card, duplicate, unknown zone)."""


class ZoneCapacityError(ZoneError):
    """A zone declared ``capacity: n`` and something tried to exceed it."""


class EffectError(EngineError):
    """An effect tree could not be executed.

    Almost always a *content* bug — a card asked for a card that isn't there, a
    zone that doesn't exist, or a reference nothing bound. The message names the
    op so the card points at itself.
    """


class UnknownOpError(EffectError):
    """No handler is registered for an op name.

    Either a typo, or a pack that ships a ``plugin.py`` which was not imported.
    """


class IllegalDecisionError(EngineError):
    """A submitted decision is not one the pending request offered.

    The UI is never trusted (``docs/rules_engine.md §6``): a decision that does
    not re-validate raises here rather than corrupting the state.
    """


class ReplayError(EngineError):
    """A decision log does not reproduce the game it claims to."""


class EngineInvariantError(EngineError):
    """A state invariant broke (``docs/rules_engine.md §8``).

    Carries every violation found plus the tail of the event log, so the report
    shows what the state looks like *and* how it got there.
    """

    def __init__(self, violations: Sequence[str], recent: Sequence[str] = ()) -> None:
        self.violations = list(violations)
        self.recent = list(recent)
        lines = [f"{len(self.violations)} invariant violation(s):"]
        lines += [f"  - {violation}" for violation in self.violations]
        if self.recent:
            lines.append("recent events:")
            lines += [f"  {event}" for event in self.recent]
        super().__init__("\n".join(lines))
