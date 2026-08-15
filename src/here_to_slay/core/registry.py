"""The five extension seams (``docs/architecture_notes.md §5``).

Every registry answers one question: *how do I add a new kind of X?*

============  =======================  ==========================================
Decorator     Adds                     Signature
============  =======================  ==========================================
``@effect``   a new **verb**           ``(ctx, params) -> Generator | None``
``@condition``a new **predicate**      ``(ctx, params) -> bool``
``@selector`` a new **target set**     ``(ctx, params) -> Sequence``
``@mutator``  how an event **applies** ``(state, event) -> None``
``@cost``     a new **payment type**   ``(ctx, params) -> Generator[bool]``
============  =======================  ==========================================

Two choices worth defending:

* **Mutators are keyed by event *name*, not by an event class.** The
  architecture note writes ``@mutator(EventType)``, but :mod:`.events` deals in
  open names so that a variant can invent ``corruption.spread`` without a Python
  type. Keying on the same strings the cards use keeps one vocabulary.

* **Effect handlers may be plain functions.** Most ops need to ``yield from
  ctx.emit(...)`` and so are generators anyway, but ``noop`` and ``shuffle``
  are not, and forcing a bare ``yield`` into them just to satisfy the driver
  would be a tax on every modder writing a simple verb. Plain functions are
  wrapped at registration.

Registration happens on import: a pack's ``plugin.py`` is imported by the
loader, which runs its decorators. :func:`temporarily` exists so a test can add
an op without leaking it into the next test.
"""

from __future__ import annotations

import difflib
import inspect
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any, Protocol

from here_to_slay.core.errors import EngineError, UnknownOpError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext
    from here_to_slay.core.events import Event, Outcome
    from here_to_slay.core.state import GameState

Params = dict[str, Any]
#: what an effect yields (a ``Request``) and receives (the decision's value)
EffectFlow = Generator[Any, Any, "Outcome | None"]


class EffectFn(Protocol):
    def __call__(self, ctx: EffectContext, params: Params) -> EffectFlow: ...


class ConditionFn(Protocol):
    def __call__(self, ctx: EffectContext, params: Params) -> bool: ...


class SelectorFn(Protocol):
    def __call__(self, ctx: EffectContext, params: Params) -> Sequence[Any]: ...


class MutatorFn(Protocol):
    def __call__(self, state: GameState, event: Event) -> None: ...


class CostFn(Protocol):
    def __call__(self, ctx: EffectContext, params: Params) -> Generator[Any, Any, bool]: ...


class Registry[T]:
    """A name -> handler table with a helpful failure message.

    ``KeyError`` is the wrong error for a modder: it says ``'stael_hero'`` and
    nothing else. :meth:`get` says which registry was searched and what the
    nearest known name is.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self._entries: dict[str, T] = {}

    def register(self, name: str, handler: T, *, replace: bool = False) -> T:
        if not replace and name in self._entries:
            raise EngineError(
                f"{self.label} '{name}' is already registered "
                f"(pass replace=True to override deliberately)"
            )
        self._entries[name] = handler
        return handler

    def get(self, name: str) -> T:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownOpError(self._unknown_message(name)) from None

    def find(self, name: str) -> T | None:
        return self._entries.get(name)

    def _unknown_message(self, name: str) -> str:
        close = difflib.get_close_matches(name, self._entries, n=1, cutoff=0.6)
        hint = (
            f" — did you mean '{close[0]}'?"
            if close
            else " — a pack that adds it must ship a plugin.py that registers it"
        )
        return f"no {self.label} registered for '{name}'{hint}"

    # -- introspection -----------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._entries))

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def snapshot(self) -> dict[str, T]:
        return dict(self._entries)

    def restore(self, entries: dict[str, T]) -> None:
        self._entries = dict(entries)


# ---------------------------------------------------------------------------
# The five registries
# ---------------------------------------------------------------------------

EFFECTS: Registry[EffectFn] = Registry("effect op")
CONDITIONS: Registry[ConditionFn] = Registry("condition op")
SELECTORS: Registry[SelectorFn] = Registry("selector")
MUTATORS: Registry[MutatorFn] = Registry("mutator")
COSTS: Registry[CostFn] = Registry("cost")

ALL_REGISTRIES: tuple[Registry[Any], ...] = (EFFECTS, CONDITIONS, SELECTORS, MUTATORS, COSTS)


def _as_flow(function: Callable[..., Any]) -> EffectFn:
    """Let a simple op be a plain function while the driver always ``yield``s."""
    if inspect.isgeneratorfunction(function):
        return function  # type: ignore[return-value]

    @wraps(function)
    def wrapper(ctx: EffectContext, params: Params) -> EffectFlow:
        return function(ctx, params)
        yield  # pragma: no cover - unreachable, makes this a generator function

    return wrapper  # type: ignore[return-value]


Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]


def effect(name: str, *, replace: bool = False) -> Decorator:
    """Register a new verb. The handler may be a generator or a plain function."""

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        EFFECTS.register(name, _as_flow(function), replace=replace)
        return function

    return decorate


def condition(name: str, *, replace: bool = False) -> Callable[[ConditionFn], ConditionFn]:
    """Register a new predicate. Conditions are pure: no yielding, no mutation."""

    def decorate(function: ConditionFn) -> ConditionFn:
        if inspect.isgeneratorfunction(function):
            raise EngineError(
                f"condition '{name}' is a generator; conditions must answer "
                f"without asking the player anything"
            )
        return CONDITIONS.register(name, function, replace=replace)

    return decorate


def selector(name: str, *, replace: bool = False) -> Callable[[SelectorFn], SelectorFn]:
    """Register a new target set."""

    def decorate(function: SelectorFn) -> SelectorFn:
        return SELECTORS.register(name, function, replace=replace)

    return decorate


def mutator(event_name: str, *, replace: bool = False) -> Callable[[MutatorFn], MutatorFn]:
    """Register *the* state change for an event name.

    Exactly one mutator per event: RESOLVE is the only place ``GameState`` is
    written during dispatch, which is what keeps mutation auditable.
    """

    def decorate(function: MutatorFn) -> MutatorFn:
        return MUTATORS.register(event_name, function, replace=replace)

    return decorate


def cost(name: str, *, replace: bool = False) -> Decorator:
    """Register a payment type (``action_points``, or a variant's ``mana``)."""

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        COSTS.register(name, _as_flow(function), replace=replace)  # type: ignore[arg-type]
        return function

    return decorate


@contextmanager
def temporarily() -> Iterator[None]:
    """Undo every registration made inside the block.

    For tests that add a throwaway op — and for ``hts diff-pack``, later, which
    wants to load a pack's plugin without polluting the process.
    """
    saved = [(registry, registry.snapshot()) for registry in ALL_REGISTRIES]
    try:
        yield
    finally:
        for registry, entries in saved:
            registry.restore(entries)


def registered_ops() -> dict[str, tuple[str, ...]]:
    """Everything currently registered — used by ``hts validate`` and tests."""
    return {
        "effects": EFFECTS.names,
        "conditions": CONDITIONS.names,
        "selectors": SELECTORS.names,
        "mutators": MUTATORS.names,
        "costs": COSTS.names,
    }


__all__ = [
    "CONDITIONS",
    "COSTS",
    "EFFECTS",
    "MUTATORS",
    "SELECTORS",
    "Registry",
    "condition",
    "cost",
    "effect",
    "mutator",
    "registered_ops",
    "selector",
    "temporarily",
]
