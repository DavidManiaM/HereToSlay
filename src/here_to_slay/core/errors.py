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
