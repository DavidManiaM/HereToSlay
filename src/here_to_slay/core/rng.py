"""Seeded, logged randomness — the only source of chance in the engine.

Two constraints shape this module:

1. ``core/`` may not import :mod:`random` (``tests/test_layering.py`` asserts
   it). That ban is not pedantry: ``random`` is *ambient*, and a single
   ``random.shuffle`` somewhere in an effect would silently break replay. The
   PRNG here is ~20 lines of splitmix64, so we lose nothing — and we gain a
   guarantee ``random.Random`` cannot give, namely that a decision log recorded
   today still replays on a future Python whose shuffle implementation changed.

2. **Every advance is logged** (``docs/rules_engine.md §8``). ``calls`` is the
   audit trail: a die roll that disagrees with a replay names the call index
   where the two runs diverged.

``Game = f(content_hash, seed, decisions)`` — this module is the ``seed`` half.
"""

from __future__ import annotations

import hashlib
from collections.abc import MutableSequence, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

_MASK64 = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15
_MIX_A = 0xBF58476D1CE4E5B9
_MIX_B = 0x94D049BB133111EB

T = TypeVar("T")


def seed_from(value: int | str | bytes) -> int:
    """Coerce a user-supplied seed to 64 bits.

    Strings are hashed with sha256 rather than :func:`hash`, whose salt changes
    between processes — ``--seed dragons`` must mean the same game tomorrow.
    """
    if isinstance(value, int):
        return value & _MASK64
    data = value.encode("utf-8") if isinstance(value, str) else value
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


@dataclass(frozen=True, slots=True)
class RngCall:
    """One logged advance of the generator."""

    index: int
    kind: str
    detail: str
    result: Any

    def __str__(self) -> str:
        return f"[{self.index}] {self.kind}({self.detail}) -> {self.result}"


@dataclass(slots=True)
class DeterministicRng:
    """A seeded splitmix64 generator that records everything it produces.

    Reproducibility contract: two instances with the same ``seed``, asked for
    the same sequence of values, return the same results and end in the same
    ``state``.
    """

    seed: int | str = 0
    #: ``None`` means "not started yet"; :meth:`__post_init__` seeds it.
    state: int | None = None
    calls: list[RngCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.seed = seed_from(self.seed)
        self.state = self.seed if self.state is None else self.state & _MASK64

    # -- the generator -----------------------------------------------------

    def _next_u64(self) -> int:
        z = self.state = ((self.state or 0) + _GOLDEN_GAMMA) & _MASK64
        z = ((z ^ (z >> 30)) * _MIX_A) & _MASK64
        z = ((z ^ (z >> 27)) * _MIX_B) & _MASK64
        return z ^ (z >> 31)

    def _below(self, bound: int) -> int:
        """Uniform in ``[0, bound)``, rejection-sampled so it is unbiased.

        ``u64 % bound`` would favour low values whenever ``bound`` does not
        divide 2**64 — invisible in play, but it would skew a 1000-game fuzz run.
        """
        if bound <= 0:
            raise ValueError(f"bound must be positive, got {bound}")
        limit = (1 << 64) - ((1 << 64) % bound)
        while True:
            value = self._next_u64()
            if value < limit:
                return value % bound

    def _log(self, kind: str, detail: str, result: T) -> T:
        self.calls.append(RngCall(len(self.calls), kind, detail, result))
        return result

    # -- public API --------------------------------------------------------

    def randint(self, low: int, high: int) -> int:
        """Uniform integer in ``[low, high]`` — inclusive, like a die."""
        if high < low:
            raise ValueError(f"empty range: [{low}, {high}]")
        return self._log("randint", f"{low}..{high}", low + self._below(high - low + 1))

    def below(self, bound: int) -> int:
        """Uniform integer in ``[0, bound)``."""
        return self._log("below", str(bound), self._below(bound))

    def roll(self, count: int, faces: int) -> tuple[int, ...]:
        """``count`` dice of ``faces`` sides, logged as one call.

        One call per roll (not per die) so the log reads like the table: a
        ``2d6`` is one entry showing both faces.
        """
        if count < 0 or faces < 1:
            raise ValueError(f"cannot roll {count}d{faces}")
        dice = tuple(1 + self._below(faces) for _ in range(count))
        return self._log("roll", f"{count}d{faces}", dice)

    def shuffle(self, items: MutableSequence[T]) -> MutableSequence[T]:
        """Fisher-Yates, in place. Logs the permutation actually applied."""
        swaps: list[int] = []
        for index in range(len(items) - 1, 0, -1):
            target = self._below(index + 1)
            swaps.append(target)
            if target != index:
                items[index], items[target] = items[target], items[index]
        self._log("shuffle", f"n={len(items)}", tuple(swaps))
        return items

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("cannot choose from an empty sequence")
        index = self._below(len(items))
        self._log("choice", f"n={len(items)}", index)
        return items[index]

    # -- bookkeeping -------------------------------------------------------

    @property
    def advances(self) -> int:
        """How many logged calls have been made. Two runs must agree on this."""
        return len(self.calls)

    def clone(self) -> DeterministicRng:
        """An independent copy — for AI rollouts that must not disturb the game."""
        return DeterministicRng(seed=self.seed, state=self.state, calls=list(self.calls))

    def tail(self, count: int = 10) -> tuple[str, ...]:
        return tuple(str(call) for call in self.calls[-count:])
